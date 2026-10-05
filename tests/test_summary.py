from datetime import date

import pytest

from subtracker.summary import (
    alert_email,
    due_this_month_cents,
    monthly_average_by_category,
    monthly_average_cents,
    summary_email,
    upcoming_charges,
)


def sub(id, name, cents, freq, anchor, category="Other", active=True):
    return {"id": id, "name": name, "amount_cents": cents, "frequency": freq,
            "anchor_date": anchor, "category": category, "active": active}


ANNUAL = sub(1, "Annual plan", 240000, "yearly", date(2025, 10, 15), "Software")


def test_yearly_plan_cash_vs_budget_view():
    # Cash view: the full R2,400 in the month it hits, nothing in other months.
    assert due_this_month_cents([ANNUAL], 2026, 10) == 240000
    assert due_this_month_cents([ANNUAL], 2026, 11) == 0
    # Budget view: R200 every month regardless.
    assert monthly_average_cents([ANNUAL]) == 20000


def test_weekly_counts_every_occurrence_in_month():
    weekly = sub(2, "Veg box", 10000, "weekly", date(2026, 1, 1))  # Thursdays
    assert due_this_month_cents([weekly], 2026, 10) == 50000  # 5 Thursdays
    assert due_this_month_cents([weekly], 2026, 11) == 40000  # 4 Thursdays
    assert monthly_average_cents([weekly]) == 43333


def test_paused_subscriptions_are_excluded():
    paused = sub(3, "Old gym", 50000, "monthly", date(2026, 1, 5), active=False)
    assert due_this_month_cents([ANNUAL, paused], 2026, 10) == 240000
    assert monthly_average_cents([ANNUAL, paused]) == 20000
    assert upcoming_charges([paused], date(2026, 10, 1), 30) == []


def test_monthly_average_by_category():
    subs = [ANNUAL, sub(4, "Netflix", 19900, "monthly", date(2026, 1, 7), "Streaming"),
            sub(5, "Showmax", 9900, "monthly", date(2026, 1, 9), "Streaming")]
    assert monthly_average_by_category(subs) == {"Streaming": 29800, "Software": 20000}


def test_upcoming_charges_sorted_with_days_away():
    subs = [sub(4, "Netflix", 19900, "monthly", date(2026, 1, 7)),
            sub(6, "Spotify", 11999, "monthly", date(2026, 1, 4))]
    rows = upcoming_charges(subs, date(2026, 10, 4), 30)
    assert [(r["name"], r["charge_date"], r["days_away"]) for r in rows] == [
        ("Spotify", date(2026, 10, 4), 0),
        ("Netflix", date(2026, 10, 7), 3),
    ]  # Spotify on 4 Nov is 31 days away, just outside the 30-day window
    assert upcoming_charges(subs, date(2026, 10, 4), 31)[-1]["days_away"] == 31


def test_alert_email_single_subject():
    subject, body, _ = alert_email(
        [{"name": "Netflix", "amount_cents": 19900, "charge_date": date(2026, 10, 7), "days_away": 2}],
        reminder_days=2,
    )
    assert subject == "Subscription: Netflix (R199.00) charges Wed 7 Oct"
    assert "- Netflix: R199.00 on Wed 7 Oct (in 2 days)" in body
    assert "Total: R199.00" in body


def test_alert_email_multiple_subject_and_total():
    alerts = [
        {"name": "Netflix", "amount_cents": 19900, "charge_date": date(2026, 10, 6), "days_away": 2},
        {"name": "Showmax", "amount_cents": 9900, "charge_date": date(2026, 10, 4), "days_away": 0},
        {"name": "Gym", "amount_cents": 64900, "charge_date": date(2026, 10, 5), "days_away": 1},
    ]
    subject, body, _ = alert_email(alerts, reminder_days=2)
    assert subject == "Subscription: 3 charges in the next 2 days"
    assert body.index("Showmax") < body.index("Gym") < body.index("Netflix")
    assert "(today)" in body and "(tomorrow)" in body
    assert "Total: R947.00" in body


def test_alert_email_requires_alerts():
    with pytest.raises(ValueError):
        alert_email([], 2)


def test_summary_email():
    subs = [ANNUAL, sub(4, "Netflix", 19900, "monthly", date(2026, 1, 7), "Streaming")]
    subject, body, _ = summary_email(subs, 2026, 10, date(2026, 10, 1))
    assert subject == "Subscription: October 2026 summary, R2,599.00 due"
    assert "Due this month (actual charges): R2,599.00" in body
    assert "Monthly average (all subscriptions spread per month): R399.00" in body
    assert "- Netflix: R199.00 on Wed 7 Oct" in body
