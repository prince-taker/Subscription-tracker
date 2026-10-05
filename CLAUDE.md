# Subscription Tracker

A personal tool that tracks recurring subscriptions, emails me **before** each charge, and sends a monthly summary. Built as a fun evening project (about 3 hours). Optimise for something that works and that I will actually keep using, not for perfection.

## The problem this solves

Forgetting. A dashboard I have to remember to open does not fix that, so **the email alert is the core feature and the dashboard is secondary.** If time runs short, cut dashboard polish, never the alerts.

## Features

### Must have (MVP)
1. Add, edit, deactivate and delete subscriptions.
2. Dashboard: due-this-month total, upcoming charges, spend by category.
3. Daily reminder script: emails me when a charge is within `REMINDER_DAYS` (default 2).
4. Monthly summary email on the 1st of each month.

### Stretch (only after MVP works and is tested)
- Upload a bank statement CSV and detect recurring transactions (same merchant, similar amount, roughly regular gap) to suggest subscriptions I forgot about.
- Price-change tracking (store history when an amount is edited).
- Free-trial flag with an alert before the trial ends.

## Tech stack (keep it boring, R0 running cost)

- Python 3.11+
- **Streamlit** for the UI
- **Turso** (hosted libSQL, free plan) for the one real database, via the official **`libsql`** package. Local **SQLite** files via the stdlib `sqlite3` module for tests and fake dev data. No ORM; the schema is tiny. See `HOSTING.md`.
- `smtplib` + Gmail SMTP for email
- `python-dotenv` for config
- `pytest` for tests
- `pandas` only where it genuinely helps (dashboard tables and charts)
- **GitHub Actions** runs the daily reminder (`.github/workflows/reminder.yml`)
- Ask me before adding any other dependency.

## Project structure

```
sub-tracker/
  CLAUDE.md
  README.md
  requirements.txt
  .env.example
  HOSTING.md            # hosted database + GitHub Actions design (overrides older scheduling notes)
  .gitignore            # must ignore .env, *.db, __pycache__, .venv, backups/, preview_*.html
  .github/workflows/reminder.yml   # daily reminder run in the cloud
  app.py                # Streamlit UI (thin: calls into subtracker/)
  reminder.py           # CLI entry point run daily by GitHub Actions
  seed.py               # fake dev data, local files only (refuses when Turso is configured)
  backup.py             # writes a timestamped JSON backup into backups/
  migrate_to_turso.py   # one-off copy of a local .db into Turso
  subtracker/
    __init__.py
    config.py           # .env loading, backend selection (Turso vs local file), SAST "today"
    db.py               # connection for either backend, schema creation, CRUD
    migrate.py          # copy logic + checksum for migrate_to_turso.py
    backup.py           # builds the JSON backup
    schedule.py         # PURE date logic (no I/O)
    summary.py          # builds monthly totals and email bodies (no I/O)
    emailer.py          # the only module that talks to SMTP
    money.py            # cents <-> "R1,234.50" formatting
  tests/
    test_schedule.py
    test_summary.py
    test_reminder.py
    ...                 # plus config, db, migrate, backup, safety, workflow, app, email HTML
```

**Design rule:** pure logic (`schedule.py`, `summary.py`, `money.py`) never touches the database, network or the system clock. Anything that needs "today" takes it as a parameter (`today: date`). That makes the date logic testable, and the date logic is where the bugs will be.

## Data model

```sql
CREATE TABLE IF NOT EXISTS subscriptions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    frequency         TEXT    NOT NULL CHECK (frequency IN ('weekly','monthly','quarterly','yearly')),
    anchor_date       TEXT    NOT NULL,   -- ISO date of any known real charge (first or most recent)
    category          TEXT    NOT NULL DEFAULT 'Other',
    active            INTEGER NOT NULL DEFAULT 1,
    last_notified_for TEXT,               -- ISO charge date we last sent an alert for
    notes             TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- meta key 'last_summary_month' holds e.g. '2026-10' so the monthly summary is sent once.
```

Decisions and why:
- **Money is stored as integer cents**, never floats. Floats cause rounding drift once you start summing and averaging.
- **We store an anchor date instead of "billing day"**, because the anchor works for every frequency (weekly, quarterly, yearly) with one mechanism.
- Dates are stored as ISO strings (`YYYY-MM-DD`); SQLite has no native date type.

## Core logic: `schedule.py`

### `next_charge_date(anchor, frequency, today) -> date`
Return the earliest charge date that is **>= today** (a charge today counts as upcoming).

Rules:
- If `anchor > today`, return `anchor`.
- **Monthly / quarterly / yearly:** step in units of 1 / 3 / 12 months. For step `k`, compute the target month from the anchor, then use `min(anchor.day, last_day_of_that_month)`.
- **Weekly:** `anchor + 7*k days`.

**Do NOT compute dates by repeatedly adding a month to the previous result.** That drifts: Jan 31 becomes Feb 28, then Mar 28, and never returns to the 31st. Always compute from the original anchor day. Use `calendar.monthrange` for the last day of the month.

### `charges_in_range(sub, start, end) -> list[date]`
All charge dates in `[start, end]` inclusive. Needed because a weekly subscription can charge 4 or 5 times in one month.

### `monthly_equivalent_cents(amount_cents, frequency) -> int`
Normalise to a monthly average. Weekly is `amount * 52 / 12`, quarterly is `/ 3`, yearly is `/ 12`. Round once at the end.

## Two different "this month" numbers (keep both, label them clearly)

1. **Due this month (cash view):** the sum of actual charges falling inside the calendar month. An annual R2,400 plan counts fully in the month it hits and not at all in the others.
2. **Monthly average (budget view):** the sum of `monthly_equivalent_cents` across active subscriptions. The same annual plan counts as R200 every month.

Mixing these up is the main way a tracker like this lies to you, so the dashboard shows both with explicit labels.

## Reminder script: `reminder.py`

Runs once a day on GitHub Actions (see Scheduling). Flow:

1. Load config and print which database this run uses (Turso hostname only, never the token). Compute `today` in `Africa/Johannesburg` via `zoneinfo` (not the machine's local time).
2. For each **active** subscription compute `next_charge_date`.
3. A subscription needs an alert when **both** hold:
   - `0 <= (next_charge - today).days <= REMINDER_DAYS`
   - `last_notified_for != next_charge` (idempotency; no duplicate emails if the script runs twice)
4. Send **one combined email** listing all due alerts, not one email per subscription.
5. Only after the email send succeeds, set `last_notified_for` for those subscriptions. If sending fails, do not mark them, so the next run retries.
6. Monthly summary: if `meta.last_summary_month != current month`, send the summary, then update the meta value. This is the catch-up behaviour: if the machine was off on the 1st, the summary still goes out at the next run in that month.

The `<= REMINDER_DAYS` window (rather than `==`) is deliberate. If a run is delayed or skipped (GitHub often delays scheduled runs), the alert still fires at the next run instead of being silently missed.

CLI flags: `--dry-run` (print the emails, send nothing, change nothing) and `--today YYYY-MM-DD` (override the date for testing). Both are required; they make the script testable without waiting for real dates.

## Email: `emailer.py`

- Gmail SMTP over SSL: `smtp.gmail.com`, port 465. Auth with a Google **app password** (requires 2-Step Verification on the account).
- Send from and to my own address.
- Secrets come from environment variables only: `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`. Never hardcode, never commit, never log them.
- **Subject lines start with `Subscription:`** so I can build a Gmail filter that keeps them out of spam/Promotions.
  - Alert: `Subscription: Netflix (R199.00) charges Wed 7 Oct` for one, or `Subscription: 3 charges in the next 2 days` for several.
  - Summary: `Subscription: October 2026 summary, R1,234.00 due`.
- Plain-text body is fine. Keep it scannable: one line per charge (name, amount, date, days away), then the total.
- `emailer.py` is the only module that imports `smtplib`, so tests can mock one function.

Reference implementation:

```python
import os, smtplib
from email.message import EmailMessage

def send_email(subject: str, body: str) -> None:
    addr = os.environ["GMAIL_ADDRESS"]
    msg = EmailMessage()
    msg["From"] = addr
    msg["To"] = addr
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(addr, os.environ["GMAIL_APP_PASSWORD"])
        server.send_message(msg)
```

## Config (`.env`, with a committed `.env.example` that has placeholders only)

```
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
REMINDER_DAYS=2
TURSO_DATABASE_URL=
TURSO_AUTH_TOKEN=
DB_PATH=subscriptions.db
```

Backend selection (`config.database_backend()`): both `TURSO_*` set means Turso; neither set means the local SQLite file at `DB_PATH`; **only one set is an error**, never a silent fallback to a local file. Inside GitHub Actions, missing Turso config is also an error. In GitHub Actions these come from repository secrets, and `REMINDER_DAYS` from a repository variable.

## UI: `app.py` (Streamlit)

Keep it to one file with tabs. Tabs:

1. **Dashboard**
   - Metric cards: *Due this month*, *Monthly average*, *Active subscriptions*.
   - Table of upcoming charges for the next 30 days, sorted by date, with a "days away" column.
   - Bar chart of monthly-average spend by category.
2. **Manage**
   - Table of all subscriptions with edit and delete, plus an active/paused toggle (pause instead of delete when I cancel something; history is useful).
   - Add form: name, amount in rands (convert to cents on save), frequency, anchor date, category (selectbox with the existing categories plus "new").
   - Validate: name is non-empty, amount > 0. Show friendly errors, not stack traces.
   - "Download backup" button (JSON of everything; Turso's free plan restores only 1 day back).
3. **Alerts** (small)
   - Button "Send test email" so I can verify SMTP without waiting for a real charge.
   - Show when the last summary was sent.

Display money as `R1,234.50`. The top of the page always says which database is in use: "Live database (Turso)" or "Local file: dev.db".

## Scheduling

My laptop is only on at weekends, so the reminder runs in the cloud. Full design and reasons in **`HOSTING.md`**, which overrides anything older here.

- **Turso** holds the one real database. The Streamlit dashboard (on my laptop) and the reminder both connect to it directly. No sync layer, no embedded replica.
- **GitHub Actions** (`.github/workflows/reminder.yml`) runs `reminder.py` daily at 05:47 UTC (07:47 SAST), and manually from the Actions tab (dry run by default).
- Scheduled runs only happen once the repo variable `REMINDER_ENABLED` is `true`. That's a kill switch so nothing runs against an empty database before the migration.
- A failed send makes `reminder.py` exit 1, so the job fails and GitHub emails me about it.
- `last_notified_for` and `last_summary_month` persist because they live in Turso, not on the runner's disk.

## Tests (write these alongside the code, not after)

`test_schedule.py` must cover at least:
- Monthly anchor on the 31st: Jan, Feb (28 and leap year 29), Apr (30 days), and confirm it **returns to the 31st** in March.
- Yearly anchor on Feb 29: non-leap years clamp to Feb 28, leap years use the 29th.
- Charge date exactly equals `today` (should return today, not next cycle).
- Anchor in the future.
- Anchor far in the past (for example 3 years back) lands on the correct cycle.
- Weekly across a month boundary; `charges_in_range` returns 5 dates for a month with 5 occurrences.
- Quarterly stepping.
- `monthly_equivalent_cents` for each frequency, including rounding.

`test_summary.py`: due-this-month vs monthly-average differ correctly for a yearly plan.

`test_reminder.py` (mock `send_email`, use an in-memory or temp DB):
- Alert fires inside the window and not outside it.
- Running twice does not send twice.
- A failed send leaves `last_notified_for` unset, so the next run retries.
- Paused subscriptions are ignored.
- Summary is sent once per month, including the catch-up case.

## Build order (follow it, and run the tests before moving on)

1. **Scaffold:** structure, `requirements.txt`, `.gitignore`, `.env.example`, git init. Make sure `.env` and `*.db` are ignored **before the first commit**.
2. **`money.py` + `schedule.py` + `test_schedule.py`.** Do not move on until the tests pass. This is the part most likely to be wrong.
3. **`db.py`:** schema creation and CRUD. Seed script with about 6 realistic fake subscriptions for development.
4. **`summary.py`:** both monthly numbers plus email body builders, with tests.
5. **`emailer.py` + `reminder.py`** with `--dry-run`. Run it against the seed data and eyeball the output before sending anything real.
6. **`app.py`:** Dashboard, then Manage, then Alerts tab.
7. **Hosting** (see `HOSTING.md`): set the GitHub Actions secrets, run `migrate_to_turso.py`, run the workflow manually as a dry run and check the output, then set `REMINDER_ENABLED=true`. End-to-end test: add a subscription charging in 2 days, confirm the scheduled run's email arrives in my inbox (not spam) and the next run doesn't duplicate it.
8. **README** with setup (venv, install, app password steps, Turso, secrets and workflow).

## How I want you to work

- Briefly explain the reasoning behind non-obvious choices as you go. I'm learning, and I'd rather understand the trade-off than just get the code.
- Prefer simple, maintainable code over clever code. Small functions, type hints, docstrings on the public functions.
- Don't over-engineer. No classes where a function will do, no abstractions for hypothetical future needs.
- If a requirement here is wrong or there's a better approach, say so and explain why, rather than silently following it or silently deviating.
- Never print, log or commit secrets (including `TURSO_AUTH_TOKEN`). Treat `.db` files and `backups/` as private financial data.
- Ask before adding dependencies or changing the data model.

## Definition of done (MVP)

- [ ] `pytest` passes, including all the edge cases listed above
- [ ] I can add, edit, pause and delete a subscription from the UI
- [ ] Dashboard shows both "due this month" and "monthly average", clearly labelled
- [ ] A real alert email arrives in my inbox 2 days before a test charge, and running the script again does not duplicate it
- [ ] The monthly summary email arrives and is sent only once per month
- [ ] The GitHub Actions workflow runs `reminder.py` daily against Turso without me touching anything
- [ ] No secrets or `.db` files are in git history
