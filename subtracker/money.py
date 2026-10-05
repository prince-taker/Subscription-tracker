"""Money helpers. Money is always integer cents internally; rands only at the edges (UI, email)."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_rands(cents: int) -> str:
    """Format integer cents as South African rands, e.g. 123450 -> 'R1,234.50'."""
    sign = "-" if cents < 0 else ""
    rands, rem = divmod(abs(cents), 100)
    return f"{sign}R{rands:,}.{rem:02d}"


def rands_to_cents(value: str | int | float | Decimal) -> int:
    """Convert a rand amount (e.g. '199.99', 199.99, 'R1,234.50') to integer cents.

    Goes through Decimal(str(value)) so a float like 0.29 doesn't become 28 cents.
    Raises ValueError for anything that isn't a number.
    """
    text = str(value).strip().replace("R", "").replace(",", "").replace(" ", "")
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Not a valid amount: {value!r}") from exc
    if not amount.is_finite():
        raise ValueError(f"Not a valid amount: {value!r}")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
