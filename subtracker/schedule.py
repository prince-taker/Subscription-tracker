"""Pure date logic for subscription charges.

No database, network or system clock in here: anything that needs "today" takes it as a parameter.
"""

import calendar
from datetime import date, timedelta
from typing import Any, Mapping

FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly")
_MONTH_STEP = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _check_frequency(frequency: str) -> None:
    if frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency!r}")


def _add_months_clamped(anchor: date, months: int) -> date:
    """Return anchor shifted by `months`, clamping the day to the target month's length.

    Always computed from the ORIGINAL anchor, so Jan 31 -> Feb 28 -> Mar 31 (no drift).
    """
    index = anchor.year * 12 + (anchor.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor.day, last_day))


def charge_date_k(anchor: date, frequency: str, k: int) -> date:
    """Return the k-th charge date counting from the anchor (k=0 is the anchor itself)."""
    _check_frequency(frequency)
    if frequency == "weekly":
        return anchor + timedelta(days=7 * k)
    return _add_months_clamped(anchor, _MONTH_STEP[frequency] * k)


def _first_k_on_or_after(anchor: date, frequency: str, day: date) -> int:
    """Smallest k >= 0 whose charge date is >= day."""
    if anchor >= day:
        return 0
    if frequency == "weekly":
        return -(-(day - anchor).days // 7)  # ceiling division
    step = _MONTH_STEP[frequency]
    months_apart = (day.year - anchor.year) * 12 + (day.month - anchor.month)
    k = max(0, months_apart // step)
    # The estimate may be one step short (e.g. clamped day falls before `day`), so walk forward.
    while charge_date_k(anchor, frequency, k) < day:
        k += 1
    return k


def next_charge_date(anchor: date, frequency: str, today: date) -> date:
    """Return the earliest charge date that is >= today (a charge today counts as upcoming)."""
    _check_frequency(frequency)
    return charge_date_k(anchor, frequency, _first_k_on_or_after(anchor, frequency, today))


def charges_in_range(sub: Mapping[str, Any], start: date, end: date) -> list[date]:
    """All charge dates of `sub` in [start, end] inclusive.

    `sub` needs `anchor_date` (a date) and `frequency`. Dates before the anchor are never
    returned: we don't know the subscription existed before its anchor.
    """
    anchor, frequency = sub["anchor_date"], sub["frequency"]
    _check_frequency(frequency)
    dates: list[date] = []
    k = _first_k_on_or_after(anchor, frequency, start)
    while (d := charge_date_k(anchor, frequency, k)) <= end:
        dates.append(d)
        k += 1
    return dates


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of a calendar month."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _div_round_half_up(numerator: int, denominator: int) -> int:
    """Integer division rounded half up (Python's round() uses banker's rounding)."""
    return (2 * numerator + denominator) // (2 * denominator)


def monthly_equivalent_cents(amount_cents: int, frequency: str) -> int:
    """Normalise a charge to its average monthly cost in cents, rounding once at the end."""
    _check_frequency(frequency)
    if frequency == "weekly":
        return _div_round_half_up(amount_cents * 52, 12)
    return _div_round_half_up(amount_cents, _MONTH_STEP[frequency])
