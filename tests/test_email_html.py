"""HTML emails: match docs/email_preview.html, escape user text, keep plain text unchanged."""

import re
from datetime import date
from email import message_from_bytes
from pathlib import Path

import pytest

import reminder
from subtracker import emailer
from subtracker.summary import alert_email, summary_email

PREVIEW = Path(__file__).resolve().parent.parent / "docs" / "email_preview.html"
TODAY = date(2026, 10, 4)  # the preview's sample date


def sub(id, name, cents, freq, anchor, category):
    return {"id": id, "name": name, "amount_cents": cents, "frequency": freq, "anchor_date": anchor,
            "category": category, "active": True, "last_notified_for": None}


# Same data as seed.py produces when run on 2026-10-04, which is what the preview shows.
SEED = [
    sub(1, "Netflix", 19900, "monthly", date(2026, 10, 5), "Streaming"),
    sub(2, "Spotify Family", 11999, "monthly", date(2026, 9, 14), "Streaming"),
    sub(3, "Showmax", 9900, "monthly", date(2026, 10, 6), "Streaming"),
    sub(4, "Virgin Active", 64900, "monthly", date(2026, 10, 1), "Fitness"),
    sub(5, "Microsoft 365", 129900, "yearly", date(2025, 12, 8), "Software"),
    sub(6, "Google One 100GB", 2999, "monthly", date(2026, 10, 14), "Software"),
    sub(7, "Weekly veg box", 18500, "weekly", date(2026, 9, 29), "Food"),
    sub(8, "Car insurance excess cover", 45000, "quarterly", date(2026, 8, 5), "Insurance"),
]

# Plain text captured from the code BEFORE HTML was added; it must not change.
ALERT_PLAIN = ('Upcoming charges:\n\n- Netflix: R199.00 on Mon 5 Oct (tomorrow)\n'
               '- Showmax: R99.00 on Tue 6 Oct (in 2 days)\n- Weekly veg box: R185.00 on Tue 6 Oct (in 2 days)\n'
               '\nTotal: R483.00\n\nCancel or pause anything you no longer use before it charges.')
SUMMARY_PLAIN = (
    'October 2026 summary\n\nDue this month (actual charges): R1,836.98\n'
    'Monthly average (all subscriptions spread per month): R2,156.90\nActive subscriptions: 8\n\n'
    'Charges this month:\n- Virgin Active: R649.00 on Thu 1 Oct\n- Netflix: R199.00 on Mon 5 Oct\n'
    '- Showmax: R99.00 on Tue 6 Oct\n- Weekly veg box: R185.00 on Tue 6 Oct\n'
    '- Weekly veg box: R185.00 on Tue 13 Oct\n- Google One 100GB: R29.99 on Wed 14 Oct\n'
    '- Spotify Family: R119.99 on Wed 14 Oct\n- Weekly veg box: R185.00 on Tue 20 Oct\n'
    '- Weekly veg box: R185.00 on Tue 27 Oct\n\nMonthly average by category:\n- Food: R801.67\n'
    '- Fitness: R649.00\n- Streaming: R417.99\n- Insurance: R150.00\n- Software: R138.24')


def seed_alert():
    return alert_email(reminder.find_due_alerts(SEED, TODAY, 2), 2)


def seed_summary():
    return summary_email(SEED, 2026, 10, TODAY)


def _normalise(markup: str) -> str:
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    markup = re.sub(r"\s+", " ", markup)
    return re.sub(r"\s*(<|>)\s*", r"\1", markup).strip()


def _body(markup: str) -> str:
    return re.search(r"<body[^>]*>(.*)</body>", markup, flags=re.S).group(1)


def test_html_matches_design_preview_exactly():
    preview = _normalise(_body(PREVIEW.read_text()))
    preview = re.sub(r"<p [^>]*>PREVIEW:.*?</p>", "", preview)  # preview-only labels
    ours = _normalise(_body(seed_alert()[2])) + _normalise(_body(seed_summary()[2]))
    assert ours == preview


def test_plain_text_unchanged():
    assert seed_alert()[1] == ALERT_PLAIN
    assert seed_summary()[1] == SUMMARY_PLAIN


def test_subjects_unchanged():
    assert seed_alert()[0] == "Subscription: 3 charges in the next 2 days"
    assert seed_summary()[0] == "Subscription: October 2026 summary, R1,836.98 due"


def test_html_contains_every_name_and_amount():
    _, _, alert = seed_alert()
    for name, amount in [("Netflix", "R199.00"), ("Showmax", "R99.00"), ("Weekly veg box", "R185.00"),
                         ("R483.00", "R483.00")]:
        assert name in alert and amount in alert
    _, _, summary = seed_summary()
    for s in SEED:
        if s["name"] not in ("Microsoft 365", "Car insurance excess cover"):  # no charge in October
            assert s["name"] in summary
        assert s["category"] in summary
    for amount in ["R1,836.98", "R2,156.90", "R649.00", "R29.99", "R119.99", "R801.67", "R138.24"]:
        assert amount in summary


def test_names_and_categories_are_escaped():
    nasty = sub(9, "Tom & Jerry <TV>", 5000, "monthly", date(2026, 10, 5), "Kids & <Family>")
    _, plain, alert = alert_email(reminder.find_due_alerts([nasty], TODAY, 2), 2)
    _, _, summary = summary_email([nasty], 2026, 10, TODAY)
    for markup in (alert, summary):
        assert "Tom &amp; Jerry &lt;TV&gt;" in markup
        assert "<TV>" not in markup
    assert "Kids &amp; &lt;Family&gt;" in summary
    assert "<Family>" not in summary
    assert "Tom & Jerry <TV>" in plain  # plain text stays literal


def test_single_alert_and_single_subscription_wording():
    one = sub(1, "Netflix", 19900, "monthly", date(2026, 10, 5), "Streaming")
    _, _, alert = alert_email(reminder.find_due_alerts([one], TODAY, 1), 1)
    assert "1 subscription will charge your account in the next 1 day, R199.00 in total." in alert
    assert "Netflix tomorrow. R199.00 in total." in alert
    _, _, summary = summary_email([one], 2026, 10, TODAY)
    assert "across 1 active subscription." in summary


def test_summary_grey_rows_only_before_today():
    _, _, summary = summary_email(SEED, 2026, 10, date(2026, 10, 5))
    # Virgin Active (1 Oct) is past; Netflix (5 Oct, today) is still upcoming.
    assert 'color:#999999;" valign="top">Virgin Active' in summary
    assert 'color:#1a1a1a;" valign="top">Netflix' in summary


def test_html_has_no_scripts_images_or_external_resources():
    for markup in (seed_alert()[2], seed_summary()[2]):
        lowered = markup.lower()
        for banned in ("<script", "<img", "<link", "<style", "url(", "http", "border-radius"):
            assert banned not in lowered


class FakeSMTP:
    """Stands in for smtplib.SMTP_SSL and records sent messages."""

    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setenv("GMAIL_ADDRESS", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP.sent


def test_send_email_is_multipart_alternative(fake_smtp):
    subject, plain, html = seed_alert()
    emailer.send_email(subject, plain, html)

    msg = message_from_bytes(fake_smtp[0].as_bytes())
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_payload(decode=True).decode().rstrip("\n") == plain
    assert "Upcoming subscription charges" in parts[1].get_payload(decode=True).decode()


@pytest.mark.parametrize("html", [None, ""])
def test_send_email_without_html_stays_plain(fake_smtp, html):
    emailer.send_email("Subscription: test email", "hello", html)
    assert fake_smtp[0].get_content_type() == "text/plain"
