from datetime import date

import pytest

import reminder
from subtracker import db


class FakeSender:
    """Records emails; can be told to fail."""

    def __init__(self, fail: bool = False):
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    def __call__(self, subject: str, body: str, html: str) -> None:
        if self.fail:
            raise OSError("SMTP down")
        self.sent.append((subject, body, html))

    def alerts(self):
        return [s for s in self.sent if "summary" not in s[0]]

    def summaries(self):
        return [s for s in self.sent if "summary" in s[0]]


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    # Pretend this month's summary already went out, so alert tests only see alerts.
    db.set_meta(c, reminder.SUMMARY_META_KEY, "2026-10")
    yield c
    c.close()


TODAY = date(2026, 10, 4)


def add(conn, name="Netflix", anchor=date(2026, 1, 6), freq="monthly", cents=19900):
    return db.add_subscription(conn, name, cents, freq, anchor)


@pytest.mark.parametrize("charge_day, expect_alert", [
    (3, False),   # yesterday: next charge is next month, outside window
    (4, True),    # today (0 days)
    (5, True),    # 1 day
    (6, True),    # 2 days == REMINDER_DAYS
    (7, False),   # 3 days, outside
])
def test_alert_window(conn, charge_day, expect_alert):
    add(conn, anchor=date(2026, 1, charge_day))
    send = FakeSender()
    assert reminder.run(conn, TODAY, reminder_days=2, send=send)
    assert bool(send.alerts()) is expect_alert


def test_single_alert_subject_and_marks_notified(conn):
    sub_id = add(conn, anchor=date(2026, 1, 6))
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    assert send.alerts()[0][0] == "Subscription: Netflix (R199.00) charges Tue 6 Oct"
    assert db.get_subscription(conn, sub_id)["last_notified_for"] == date(2026, 10, 6)


def test_combined_email_for_several_alerts(conn):
    add(conn, "Netflix", date(2026, 1, 5))
    add(conn, "Showmax", date(2026, 1, 6))
    add(conn, "Spotify", date(2026, 1, 4))
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    assert len(send.alerts()) == 1
    assert send.alerts()[0][0] == "Subscription: 3 charges in the next 2 days"


def test_running_twice_does_not_send_twice(conn):
    add(conn, anchor=date(2026, 1, 5))
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    reminder.run(conn, TODAY, 2, send=send)
    reminder.run(conn, date(2026, 10, 5), 2, send=send)  # next day, same charge
    assert len(send.alerts()) == 1


def test_next_cycle_alerts_again(conn):
    add(conn, anchor=date(2026, 1, 5))
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    reminder.run(conn, date(2026, 11, 4), 2, send=send)  # November's charge
    assert len(send.alerts()) == 2


def test_failed_send_leaves_unmarked_and_retries(conn):
    sub_id = add(conn, anchor=date(2026, 1, 5))
    assert reminder.run(conn, TODAY, 2, send=FakeSender(fail=True)) is False
    assert db.get_subscription(conn, sub_id)["last_notified_for"] is None
    send = FakeSender()
    assert reminder.run(conn, TODAY, 2, send=send)
    assert len(send.alerts()) == 1
    assert db.get_subscription(conn, sub_id)["last_notified_for"] == date(2026, 10, 5)


def test_paused_subscription_ignored(conn):
    sub_id = add(conn, anchor=date(2026, 1, 5))
    db.set_active(conn, sub_id, False)
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    assert send.sent == []


def test_dry_run_sends_and_changes_nothing(conn, capsys, tmp_path):
    sub_id = add(conn, anchor=date(2026, 1, 5))
    db.set_meta(conn, reminder.SUMMARY_META_KEY, "2026-09")
    send = FakeSender()
    reminder.run(conn, TODAY, 2, dry_run=True, send=send, preview_dir=tmp_path)
    assert send.sent == []
    assert db.get_subscription(conn, sub_id)["last_notified_for"] is None
    assert db.get_meta(conn, reminder.SUMMARY_META_KEY) == "2026-09"
    out = capsys.readouterr().out
    assert "[DRY RUN] Subject: Subscription: Netflix" in out
    assert "[DRY RUN] Subject: Subscription: October 2026 summary" in out
    assert "- Netflix: R199.00 on Mon 5 Oct (tomorrow)" in out  # plain text still printed
    assert "Upcoming subscription charges" in (tmp_path / "preview_alert.html").read_text()
    assert "Monthly subscription summary" in (tmp_path / "preview_summary.html").read_text()


def test_real_send_passes_plain_and_html(conn):
    add(conn, anchor=date(2026, 1, 5))
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    subject, plain, html = send.alerts()[0]
    assert plain.startswith("Upcoming charges:")
    assert html.startswith("<!doctype html>")


# --- Monthly summary -------------------------------------------------------------

def test_summary_sent_once_per_month(conn):
    db.set_meta(conn, reminder.SUMMARY_META_KEY, "2026-09")
    add(conn, anchor=date(2026, 1, 20))
    send = FakeSender()
    reminder.run(conn, date(2026, 10, 1), 2, send=send)
    reminder.run(conn, date(2026, 10, 2), 2, send=send)
    reminder.run(conn, date(2026, 10, 15), 2, send=send)
    assert [s[0] for s in send.summaries()] == ["Subscription: October 2026 summary, R199.00 due"]
    assert db.get_meta(conn, reminder.SUMMARY_META_KEY) == "2026-10"


def test_summary_catch_up_when_machine_off_on_the_1st(conn):
    db.set_meta(conn, reminder.SUMMARY_META_KEY, "2026-09")
    send = FakeSender()
    reminder.run(conn, date(2026, 10, 9), 2, send=send)  # first run of October is on the 9th
    assert len(send.summaries()) == 1
    reminder.run(conn, date(2026, 11, 1), 2, send=send)  # and November goes out normally
    assert len(send.summaries()) == 2


def test_summary_failure_retries_next_run(conn):
    db.set_meta(conn, reminder.SUMMARY_META_KEY, "2026-09")
    reminder.run(conn, TODAY, 2, send=FakeSender(fail=True))
    assert db.get_meta(conn, reminder.SUMMARY_META_KEY) == "2026-09"
    send = FakeSender()
    reminder.run(conn, TODAY, 2, send=send)
    assert len(send.summaries()) == 1


def test_main_uses_send_email_and_dry_run_flag(tmp_path, monkeypatch, capsys):
    """CLI wiring: --today and --dry-run, DB from DB_PATH, send_email never called."""
    db_file = tmp_path / "t.db"
    c = db.connect(str(db_file))
    db.add_subscription(c, "Netflix", 19900, "monthly", date(2026, 1, 7))
    c.close()
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setattr("subtracker.config.load_config", lambda: None)
    monkeypatch.setattr("subtracker.config.PROJECT_DIR", tmp_path)  # previews go here, not the repo

    def boom(*args, **kwargs):
        raise AssertionError("send_email must not be called in dry run")

    monkeypatch.setattr("subtracker.emailer.send_email", boom)
    assert reminder.main(["--dry-run", "--today", "2026-10-05"]) == 0
    assert "Netflix (R199.00) charges Wed 7 Oct" in capsys.readouterr().out
    assert (tmp_path / "preview_alert.html").exists()
    assert (tmp_path / "preview_summary.html").exists()
