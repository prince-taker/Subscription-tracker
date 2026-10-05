# Subscription Tracker

Tracks recurring subscriptions, emails you **before** each charge, and sends a monthly summary.
The email alert is the core feature; the Streamlit dashboard is secondary. See `CLAUDE.md` for the full spec
and `HOSTING.md` for how it is hosted.

How it runs:

- **Turso** (free plan) holds the one real database.
- **GitHub Actions** runs `reminder.py` every morning in the cloud, so alerts don't depend on your laptop being on.
- **The Streamlit dashboard** runs on your laptop when you want to add or edit something. It talks to the same Turso database.
- **Local SQLite files** are only for tests and fake dev data.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # Windows: copy .env.example .env
```

Fill in `.env`:

| Variable | Meaning |
|---|---|
| `GMAIL_ADDRESS` | Your Gmail address. Emails are sent from and to this address. |
| `GMAIL_APP_PASSWORD` | A Google **app password** (not your normal password), see below. |
| `REMINDER_DAYS` | Alert when a charge is this many days away or fewer. Default `2`. |
| `TURSO_DATABASE_URL` | Your Turso database URL, e.g. `libsql://subscriptions-you.turso.io`. |
| `TURSO_AUTH_TOKEN` | A Turso token for that database. Never commit, print or paste it anywhere public. |
| `DB_PATH` | Local SQLite file, used only when both Turso values are blank. Relative paths are relative to the project folder. Default `subscriptions.db`. |

**Which database is used** depends only on the two Turso variables:

| `TURSO_DATABASE_URL` | `TURSO_AUTH_TOKEN` | Database |
|---|---|---|
| set | set | Turso (live) |
| blank | blank | local file at `DB_PATH` |
| set | blank, or the other way round | **error**, nothing runs |

The half-set case is an error on purpose: silently falling back to an empty local file is how a job
ends up reading nothing and never alerting you. The app shows "Live database (Turso)" or
"Local file: dev.db" at the top, and `reminder.py`, `backup.py` and `migrate_to_turso.py` print the
database (Turso hostname only, never the token) as their first line.

`.env`, `*.db`, `backups/` and the dry-run previews are git-ignored. They hold private financial
data, so don't commit or share them.

### Gmail app password

1. Turn on 2-Step Verification: Google Account → Security → 2-Step Verification.
2. Go to <https://myaccount.google.com/apppasswords>, create one (name it "Subscription tracker").
3. Paste the 16 characters into `GMAIL_APP_PASSWORD` without the spaces.
4. In Gmail, create a filter for subject `Subscription:` → "Never send it to Spam" and "Categorise as Primary",
   so alerts don't vanish into Promotions.

## Usage

```bash
streamlit run app.py                         # the UI
python reminder.py --dry-run                 # print what would be emailed; sends/changes nothing
python reminder.py --today 2026-10-05 --dry-run   # pretend it's another day
python reminder.py                           # real run (GitHub Actions runs this daily)
python backup.py                             # JSON backup into backups/
python -m pytest                             # tests (offline; never touch Turso or Gmail)
```

For development data: comment out both Turso lines in `.env`, then run `python seed.py --db dev.db`.
It creates 8 fake subscriptions with charge dates near today. Set `DB_PATH=dev.db` when you run the app
or the reminder. `seed.py` **refuses to run while Turso is configured**, even with `--db`, and it won't
add data to a database that already has subscriptions unless you pass `--force`.

The tests clear the Turso variables and don't load `.env`, so `pytest` can't touch your live data even
when your `.env` points at Turso.

Emails are sent as HTML with the plain text as a fallback (multipart/alternative). The design
reference is `docs/email_preview.html`. `--dry-run` prints the plain text and also writes
`preview_alert.html` and `preview_summary.html` to the project folder so you can open them in a
browser. Both files are git-ignored because they contain your real data. A dry run with no alerts due
writes no alert preview, so an old `preview_alert.html` can be left over from an earlier run.

`reminder.py` exits with code `1` if an email failed to send, and `2` on a config error. Nothing is
marked as sent in either case, so the next run retries. On GitHub Actions a non-zero exit fails the
job, and GitHub emails you about failed scheduled runs, so you hear about it.

### Backups

Turso's free plan can only restore the last day, and can't restore a deleted database. Keep your own copies:

- `python backup.py` writes `backups/subscriptions-YYYY-MM-DD-HHMMSS.json` (SAST time).
- The **Download backup** button in the Manage tab gives you the same JSON in your browser.

A backup holds every column of every subscription, plus the `meta` table, which records the last summary month.

## Hosting setup (Turso + GitHub Actions)

The design and its reasons are in `HOSTING.md`. These steps need your accounts and your laptop, so
you do them yourself. Do them in order. Until step 9, nothing runs on a schedule.

1. **Back up your local data first.** With the Turso lines in `.env` still blank, run
   `python backup.py`. This writes a JSON copy of your current `subscriptions.db` into `backups/`.
2. **Create the Turso database and a token.** Sign up at <https://turso.tech> (the free plan is enough) and create a
   database (e.g. `subscriptions`). Copy its URL (`libsql://...`) and create an auth token for it.
   If you give the token an expiry date, note it: when it expires, the daily job fails and GitHub
   emails you. With the Turso CLI instead (macOS/Linux, or WSL on Windows):
   `turso db create subscriptions`, `turso db show subscriptions --url`, `turso db tokens create subscriptions`.
3. **Point your laptop at Turso.** Put both values in `.env` as `TURSO_DATABASE_URL` and
   `TURSO_AUTH_TOKEN`, then run `pip install -r requirements.txt` to get `libsql`.
4. **Migrate your data.** Look first, then copy:
   ```bash
   python migrate_to_turso.py --source subscriptions.db --dry-run
   python migrate_to_turso.py --source subscriptions.db
   ```
   The second command must finish with `Verify: OK (row counts and checksum match)`. It refuses if
   Turso already has subscriptions. `--force` *replaces* what's there with your local data.
   Afterwards, rename the local file (e.g. `subscriptions-before-turso.db`) so nothing edits it by mistake.
5. **Check the dashboard.** `streamlit run app.py` should say **Live database (Turso)** at the top and show your subscriptions.
6. **Add the GitHub secrets.** In the repo, go to Settings → Secrets and variables → Actions → *Secrets* →
   New repository secret, and add `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`.
   Optional: under *Variables*, set `REMINDER_DAYS` (defaults to `2`).
7. **Merge this to `main`.** GitHub only shows the "Run workflow" button for, and only schedules, workflows on the default branch.
8. **Do a manual dry run.** Go to Actions → *Subscription reminder* → Run workflow, keep "Print emails
   instead of sending" ticked, and run it. In the log, the first line of the *Run reminder* step should be
   `Database: Turso, host <your-db>.turso.io (dry run)`, followed by the emails you expect.
9. **Turn on the schedule.** Under Settings → Secrets and variables → Actions → *Variables*, add `REMINDER_ENABLED` = `true`.
   From then on it runs daily at 05:47 UTC (07:47 SAST). GitHub often starts scheduled runs late, which
   is fine because the alert window still catches the charge.
10. **End-to-end test.** Add a subscription that charges in 2 days. Wait for the morning run, or use Run
    workflow with the box unticked. The alert should arrive in your inbox, not spam. Run it again and
    the log says `No charges inside the reminder window.` and no second email arrives. If this month's
    summary hasn't been sent yet, it is also sent on the first real run.

To pause the daily job, set `REMINDER_ENABLED` to anything other than `true`, or disable the workflow in the Actions tab.

## Project layout

```
app.py              Streamlit UI (thin)
reminder.py         CLI run daily by GitHub Actions: alerts + monthly summary
seed.py             fake data for development (local files only)
backup.py           JSON backup into backups/
migrate_to_turso.py one-off copy of a local .db into Turso
.github/workflows/reminder.yml   the daily schedule
subtracker/
  schedule.py       pure date logic (next charge, charges in a range, monthly equivalent)
  summary.py        pure: monthly totals, upcoming charges, email subject/body builders
  money.py          cents <-> "R1,234.50"
  db.py             connection for SQLite or Turso, schema + CRUD
  migrate.py        copy + checksum used by migrate_to_turso.py
  backup.py         builds the JSON backup
  emailer.py        the only module that imports smtplib
  config.py         .env loading, database selection, REMINDER_DAYS, "today" in Africa/Johannesburg
tests/
```

## Decisions and deviations from the spec

- **Files live at the repo root**, not in a `sub-tracker/` subfolder. The repo is the project folder.
- **Added `subtracker/config.py`** (not in the spec layout). Both `app.py` and `reminder.py` need the
  same config loading and the same "today in SAST" logic, so it lives in one place instead of two copies.
- **`.env` and `DB_PATH` are resolved relative to the project folder**, not the current directory.
  Scripts and shortcuts often run from a different working directory. With a plain `load_dotenv()` the
  job would find no config, or create a new empty database somewhere else, and never alert you.
- **Timezone fallback:** on Windows, Python's `zoneinfo` has no timezone data unless you install
  the `tzdata` package. Rather than add a dependency, `sa_today()` falls back to a fixed UTC+2 if
  `Africa/Johannesburg` can't be loaded. That gives the same answer because South Africa has no
  daylight saving time.
- **`charges_in_range` never returns dates before the anchor.** The tracker doesn't know whether the
  subscription existed before its anchor date. So if the anchor is a *future* charge date, earlier
  charges in the same month don't count toward "Due this month". Use a past charge date as the
  anchor if you have one.
- **Alerts and the summary are independent.** If the alert email fails, the summary is still
  tried, and the other way round. Each one only writes its "sent" marker after its own send succeeds.
- **First run sends a summary straight away.** `last_summary_month` starts empty, so the first
  `reminder.py` run sends the current month's summary, even mid-month. This is the catch-up rule
  applied to a new install, and it's a handy way to check that email works.
- **Editing a subscription doesn't clear `last_notified_for`.** If you change the anchor date, the
  old marker no longer matches the next charge date, so the alert still fires. If you change only
  the amount, an alert you already got for that charge isn't re-sent.
- **The monthly average rounds half up** (R0.005 → R0.01), not with Python's banker's rounding.
- **HTML copy follows the preview, with singular forms when the count is 1.** For example,
  "1 subscription will charge your account in the next 1 day" rather than "1 subscriptions ... 1 days".
- **The summary's grey "already charged" rows are charges dated before today.** A charge due today
  isn't grey, which matches how "upcoming" works everywhere else. Because of this, `summary_email`
  now takes `today`.
- **If a section of the HTML summary has no rows**, only its Total row (R0.00) is shown. The preview
  has no copy for an empty month, so no new copy was added.
- **The HTML and plain text say slightly different things.** The HTML follows the preview's copy
  (for example "Pause or cancel anything you no longer use before its date."). The plain-text
  fallback is unchanged from before.
- **Day and month names are hardcoded in English**, so email subjects such as
  `Subscription: Netflix (R199.00) charges Wed 7 Oct` look the same whatever your OS language is.
- **Seed anchors use the machine's date** (`date.today()`), not SAST. It's development data only,
  so being a day off around midnight doesn't matter.
- **Delete needs a confirmation tick box**, and the UI nudges you toward Pause, because the spec
  prefers keeping history.
- **`db.py` only uses what `sqlite3` and `libsql` have in common.** I checked against `libsql`
  0.1.11. It rejects named `:param` placeholders and has no `row_factory`, so queries use positional
  `?` placeholders and rows are built from `cursor.description`. Inserts use `INSERT ... RETURNING id`
  (plain SQL) instead of the driver's `lastrowid`, and there's one statement per `execute()`. The
  `db.py` tests run on both drivers, with `libsql` pointed at a local file.
- **Turso couldn't be tested from the cloud environment** (no network). Everything up to the driver is
  tested offline, including a real `libsql` connection to a local file. The first true remote test is
  hosting setup steps 4 and 5.
- **GitHub Actions without Turso secrets is an error** (`HOSTING.md` doesn't ask for this). A missing
  secret reaches the job as an empty string, which would otherwise mean "use a local file". On a
  fresh runner that file is empty, so the job would email you a R0.00 summary every day. GitHub sets
  `GITHUB_ACTIONS=true` on every runner, so the check needs nothing extra in the workflow.
- **`migrate_to_turso.py --force` replaces the remote data** instead of merging into it. Merging would
  clash on the preserved `id`s, and the checksum could never match. The source file is opened
  read-only, so a typo in `--source` can't create an empty database. `--source` defaults to `DB_PATH`.
- **Backups include the `meta` table and say which database they came from**, so a backup is enough to rebuild everything.
- **Any run against Turso creates the tables if they're missing**, including a dry run. That's
  harmless (`CREATE TABLE IF NOT EXISTS`), and it means a dry run against a fresh database shows
  "no charges" instead of crashing.
- **The dashboard opens a new Turso connection on every page interaction.** That's a few
  small HTTP requests each time, well inside the free plan, and it avoids sharing one connection
  across Streamlit's threads.
- **`tests/test_db.py` and `tests/test_app.py` are extra tests** beyond the spec. The app test uses
  Streamlit's built-in `AppTest`, so it needs no new dependency.
