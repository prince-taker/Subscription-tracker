from datetime import date

import pytest

from subtracker.money import format_rands, rands_to_cents
from subtracker.schedule import (
    charges_in_range,
    month_bounds,
    monthly_equivalent_cents,
    next_charge_date,
)


# --- Monthly anchored on the 31st -------------------------------------------------

@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2026, 1, 15), date(2026, 1, 31)),   # January has 31 days
        (date(2026, 2, 1), date(2026, 2, 28)),    # non-leap February clamps to 28
        (date(2028, 2, 1), date(2028, 2, 29)),    # leap February clamps to 29
        (date(2026, 3, 1), date(2026, 3, 31)),    # returns to the 31st after Feb (no drift)
        (date(2026, 4, 1), date(2026, 4, 30)),    # 30-day month clamps to 30
        (date(2026, 5, 1), date(2026, 5, 31)),
    ],
)
def test_monthly_anchor_31st(today, expected):
    assert next_charge_date(date(2025, 12, 31), "monthly", today) == expected


def test_monthly_31st_day_after_clamped_charge_goes_to_next_month():
    # Feb 28 charge has passed; next is Mar 31, not Mar 28.
    assert next_charge_date(date(2026, 1, 31), "monthly", date(2026, 3, 1)) == date(2026, 3, 31)


def test_monthly_31st_in_range_returns_to_31st():
    sub = {"anchor_date": date(2026, 1, 31), "frequency": "monthly"}
    assert charges_in_range(sub, date(2026, 1, 1), date(2026, 5, 31)) == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]


# --- Yearly anchored on Feb 29 ----------------------------------------------------

@pytest.mark.parametrize(
    "today, expected",
    [
        (date(2024, 3, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2026, 2, 28)),
        (date(2027, 3, 1), date(2028, 2, 29)),   # leap year uses the 29th again
        (date(2028, 2, 29), date(2028, 2, 29)),
    ],
)
def test_yearly_feb_29(today, expected):
    assert next_charge_date(date(2024, 2, 29), "yearly", today) == expected


# --- Boundaries -------------------------------------------------------------------

@pytest.mark.parametrize("frequency", ["weekly", "monthly", "quarterly", "yearly"])
def test_charge_today_is_upcoming(frequency):
    anchor = date(2025, 10, 4)
    today = {"weekly": date(2026, 10, 3), "monthly": date(2026, 10, 4),
             "quarterly": date(2026, 10, 4), "yearly": date(2026, 10, 4)}[frequency]
    # 2025-10-04 + 52 weeks = 2026-10-03
    assert next_charge_date(anchor, frequency, today) == today


def test_anchor_equals_today():
    assert next_charge_date(date(2026, 10, 4), "monthly", date(2026, 10, 4)) == date(2026, 10, 4)


@pytest.mark.parametrize("frequency", ["weekly", "monthly", "quarterly", "yearly"])
def test_anchor_in_future_returns_anchor(frequency):
    assert next_charge_date(date(2026, 12, 25), frequency, date(2026, 10, 4)) == date(2026, 12, 25)


def test_anchor_far_in_past_monthly():
    # Anchor 3 years back on the 15th; today is the 4th -> this month's 15th.
    assert next_charge_date(date(2023, 10, 15), "monthly", date(2026, 10, 4)) == date(2026, 10, 15)
    # Today is after the 15th -> next month's.
    assert next_charge_date(date(2023, 10, 15), "monthly", date(2026, 10, 16)) == date(2026, 11, 15)


def test_anchor_far_in_past_weekly():
    # 2023-10-05 is a Thursday; 2026-10-08 is the Thursday on/after 2026-10-04.
    assert next_charge_date(date(2023, 10, 5), "weekly", date(2026, 10, 4)) == date(2026, 10, 8)


def test_anchor_far_in_past_yearly():
    assert next_charge_date(date(2023, 3, 10), "yearly", date(2026, 10, 4)) == date(2027, 3, 10)


# --- Weekly ----------------------------------------------------------------------

def test_weekly_across_month_boundary():
    # Thursday 2026-10-29 -> next Thursday is 2026-11-05.
    assert next_charge_date(date(2026, 10, 1), "weekly", date(2026, 10, 30)) == date(2026, 11, 5)


def test_charges_in_range_weekly_five_occurrences():
    # October 2026 has five Thursdays: 1, 8, 15, 22, 29.
    sub = {"anchor_date": date(2026, 1, 1), "frequency": "weekly"}
    start, end = month_bounds(2026, 10)
    assert charges_in_range(sub, start, end) == [
        date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 15),
        date(2026, 10, 22), date(2026, 10, 29),
    ]


def test_charges_in_range_weekly_four_occurrences():
    sub = {"anchor_date": date(2026, 1, 1), "frequency": "weekly"}
    start, end = month_bounds(2026, 11)
    assert len(charges_in_range(sub, start, end)) == 4


def test_charges_in_range_inclusive_and_ignores_dates_before_anchor():
    sub = {"anchor_date": date(2026, 10, 15), "frequency": "monthly"}
    assert charges_in_range(sub, date(2026, 9, 1), date(2026, 11, 15)) == [
        date(2026, 10, 15), date(2026, 11, 15)
    ]


def test_charges_in_range_empty_for_yearly_off_month():
    sub = {"anchor_date": date(2025, 3, 10), "frequency": "yearly"}
    assert charges_in_range(sub, *month_bounds(2026, 10)) == []


# --- Quarterly -------------------------------------------------------------------

def test_quarterly_stepping():
    sub = {"anchor_date": date(2026, 1, 31), "frequency": "quarterly"}
    assert charges_in_range(sub, date(2026, 1, 1), date(2027, 1, 31)) == [
        date(2026, 1, 31), date(2026, 4, 30), date(2026, 7, 31),
        date(2026, 10, 31), date(2027, 1, 31),
    ]


def test_quarterly_next_charge():
    assert next_charge_date(date(2025, 11, 20), "quarterly", date(2026, 10, 4)) == date(2026, 11, 20)


def test_unknown_frequency_raises():
    with pytest.raises(ValueError):
        next_charge_date(date(2026, 1, 1), "daily", date(2026, 1, 1))


# --- monthly_equivalent_cents ----------------------------------------------------

@pytest.mark.parametrize(
    "amount, frequency, expected",
    [
        (19900, "monthly", 19900),
        (10000, "weekly", 43333),     # 10000 * 52 / 12 = 43333.33 -> 43333
        (10001, "weekly", 43338),     # 10001 * 52 / 12 = 43337.67 -> 43338
        (3, "weekly", 13),            # 3 * 52 / 12 = 13.0 exactly
        (30000, "quarterly", 10000),
        (10000, "quarterly", 3333),   # 3333.33 -> 3333
        (20000, "quarterly", 6667),   # 6666.67 -> 6667
        (240000, "yearly", 20000),
        (100006, "yearly", 8334),     # 8333.83 -> 8334
        (6, "yearly", 1),             # 0.5 -> 1 (half up, not banker's 0)
    ],
)
def test_monthly_equivalent_cents(amount, frequency, expected):
    assert monthly_equivalent_cents(amount, frequency) == expected


# --- money -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "cents, text",
    [(0, "R0.00"), (5, "R0.05"), (19900, "R199.00"), (123450, "R1,234.50"),
     (123456789, "R1,234,567.89"), (-1500, "-R15.00")],
)
def test_format_rands(cents, text):
    assert format_rands(cents) == text


@pytest.mark.parametrize(
    "value, cents",
    [("199.99", 19999), (0.29, 29), (199, 19900), ("R1,234.50", 123450), ("0.005", 1)],
)
def test_rands_to_cents(value, cents):
    assert rands_to_cents(value) == cents


def test_rands_to_cents_rejects_garbage():
    with pytest.raises(ValueError):
        rands_to_cents("abc")
