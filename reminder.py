"""Daily reminder job. Run once a day by GitHub Actions (.github/workflows/reminder.yml).

    python reminder.py                 # normal run
    python reminder.py --dry-run       # print emails, send nothing, change nothing
    python reminder.py --today 2026-10-05 --dry-run
"""

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from subtracker import config, db, emailer
from subtracker.schedule import next_charge_date
from subtracker.summary import alert_email, summary_email

SUMMARY_META_KEY = "last_summary_month"
SendFn = Callable[[str, str, str], None]  # (subject, plain_text, html)


def find_due_alerts(subs: list[dict[str, Any]], today: date, reminder_days: int) -> list[dict[str, Any]]:
    """Active subscriptions whose next charge is within the window and not yet alerted for."""
    alerts = []
    for s in subs:
        if not s["active"]:
            continue
        charge = next_charge_date(s["anchor_date"], s["frequency"], today)
        days_away = (charge - today).days
        if 0 <= days_away <= reminder_days and s["last_notified_for"] != charge:
            alerts.append({"id": s["id"], "name": s["name"], "amount_cents": s["amount_cents"],
                           "charge_date": charge, "days_away": days_away})
    return alerts


def run(
    conn: db.Connection,
    today: date,
    reminder_days: int,
    dry_run: bool = False,
    send: SendFn | None = None,
    preview_dir: Path | None = None,
) -> bool:
    """Send due alerts and (once per month) the summary. Returns False if any send failed.

    Alerts and summary are independent: a failed alert email doesn't block the summary,
    and vice versa. State is only written after a successful send, so failures retry
    on the next run. A dry run prints the plain text and, if `preview_dir` is given, also
    writes preview_alert.html / preview_summary.html there to open in a browser.
    """
    send = send or emailer.send_email
    ok = True
    subs = db.list_subscriptions(conn)

    alerts = find_due_alerts(subs, today, reminder_days)
    if alerts:
        subject, body, html = alert_email(alerts, reminder_days)
        if dry_run:
            _print_email(subject, body)
            _write_preview(preview_dir, "preview_alert.html", html)
        else:
            try:
                send(subject, body, html)
                db.mark_notified(conn, {a["id"]: a["charge_date"] for a in alerts})
                print(f"Sent alert for {len(alerts)} charge(s).")
            except Exception as exc:  # noqa: BLE001 - any failure means "retry next run"
                ok = False
                print(f"Alert email failed, will retry next run: {exc}", file=sys.stderr)
    else:
        print("No charges inside the reminder window.")

    month_key = f"{today.year:04d}-{today.month:02d}"
    if db.get_meta(conn, SUMMARY_META_KEY) != month_key:
        subject, body, html = summary_email(subs, today.year, today.month, today)
        if dry_run:
            _print_email(subject, body)
            _write_preview(preview_dir, "preview_summary.html", html)
        else:
            try:
                send(subject, body, html)
                db.set_meta(conn, SUMMARY_META_KEY, month_key)
                print(f"Sent {month_key} summary.")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"Summary email failed, will retry next run: {exc}", file=sys.stderr)
    return ok


def _print_email(subject: str, body: str) -> None:
    print("=" * 60)
    print(f"[DRY RUN] Subject: {subject}")
    print("-" * 60)
    print(body)
    print("=" * 60)


def _write_preview(preview_dir: Path | None, filename: str, html: str) -> None:
    if preview_dir is None:
        return
    path = Path(preview_dir) / filename
    path.write_text(html, encoding="utf-8")
    print(f"[DRY RUN] HTML version written to {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send subscription reminder and summary emails.")
    parser.add_argument("--dry-run", action="store_true", help="print emails; send and change nothing")
    parser.add_argument("--today", type=date.fromisoformat, help="override today (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    config.load_config()
    try:
        backend = config.database_backend()
        reminder_days = config.reminder_days()
    except (config.ConfigError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    # Always say which database this run uses, so a run against the wrong one is obvious.
    print(backend.describe() + (" (dry run)" if args.dry_run else ""))
    today = args.today or config.sa_today()
    conn = db.connect_backend(backend)
    try:
        ok = run(conn, today, reminder_days, dry_run=args.dry_run,
                 preview_dir=config.PROJECT_DIR)
    finally:
        conn.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
