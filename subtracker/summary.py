"""Monthly totals and email bodies. Pure: no I/O, no clock (pass `today` in).

Two different "this month" numbers, deliberately kept separate:
- due_this_month_cents: cash view, actual charges landing in the calendar month.
- monthly_average_cents: budget view, every subscription normalised to a monthly cost.
"""

from collections import defaultdict
from html import escape
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from subtracker.money import format_rands
from subtracker.schedule import charges_in_range, month_bounds, monthly_equivalent_cents

Sub = Mapping[str, Any]

# Explicit names so output doesn't depend on the machine's locale.
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _active(subs: Iterable[Sub]) -> list[Sub]:
    return [s for s in subs if s["active"]]


def short_date(d: date) -> str:
    """'Wed 7 Oct' style date."""
    return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1][:3]}"


def month_label(year: int, month: int) -> str:
    """'October 2026' style label."""
    return f"{_MONTHS[month - 1]} {year}"


def days_away_text(days: int) -> str:
    """Human wording for a day count: 'today', 'tomorrow', 'in 3 days'."""
    return {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")


def charges_in_month(subs: Iterable[Sub], year: int, month: int) -> list[dict[str, Any]]:
    """Every actual charge of an active subscription inside the month, sorted by date."""
    start, end = month_bounds(year, month)
    rows = [
        {"id": s["id"], "name": s["name"], "amount_cents": s["amount_cents"],
         "category": s["category"], "charge_date": d}
        for s in _active(subs)
        for d in charges_in_range(s, start, end)
    ]
    return sorted(rows, key=lambda r: (r["charge_date"], r["name"].lower()))


def due_this_month_cents(subs: Iterable[Sub], year: int, month: int) -> int:
    """Cash view: sum of actual charges falling inside the calendar month."""
    return sum(r["amount_cents"] for r in charges_in_month(subs, year, month))


def monthly_average_cents(subs: Iterable[Sub]) -> int:
    """Budget view: sum of monthly-equivalent cost across active subscriptions."""
    return sum(monthly_equivalent_cents(s["amount_cents"], s["frequency"]) for s in _active(subs))


def monthly_average_by_category(subs: Iterable[Sub]) -> dict[str, int]:
    """Monthly-average cents per category, largest first."""
    totals: dict[str, int] = defaultdict(int)
    for s in _active(subs):
        totals[s["category"]] += monthly_equivalent_cents(s["amount_cents"], s["frequency"])
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def upcoming_charges(subs: Iterable[Sub], today: date, days: int) -> list[dict[str, Any]]:
    """Every charge of an active subscription in [today, today + days], sorted by date."""
    end = today + timedelta(days=days)
    rows = [
        {"id": s["id"], "name": s["name"], "amount_cents": s["amount_cents"],
         "category": s["category"], "frequency": s["frequency"],
         "charge_date": d, "days_away": (d - today).days}
        for s in _active(subs)
        for d in charges_in_range(s, today, end)
    ]
    return sorted(rows, key=lambda r: (r["charge_date"], r["name"].lower()))


def _charge_line(name: str, amount_cents: int, d: date, extra: str = "") -> str:
    line = f"- {name}: {format_rands(amount_cents)} on {short_date(d)}"
    return f"{line} ({extra})" if extra else line


def alert_email(alerts: list[Mapping[str, Any]], reminder_days: int) -> tuple[str, str, str]:
    """Build (subject, plain_text, html) for one combined reminder email.

    Each alert needs name, amount_cents, charge_date and days_away.
    """
    if not alerts:
        raise ValueError("alert_email needs at least one alert")
    alerts = sorted(alerts, key=lambda a: (a["charge_date"], a["name"].lower()))
    if len(alerts) == 1:
        a = alerts[0]
        subject = (f"Subscription: {a['name']} ({format_rands(a['amount_cents'])}) "
                   f"charges {short_date(a['charge_date'])}")
    else:
        subject = f"Subscription: {len(alerts)} charges in the next {reminder_days} days"
    lines = ["Upcoming charges:", ""]
    lines += [_charge_line(a["name"], a["amount_cents"], a["charge_date"],
                           days_away_text(a["days_away"])) for a in alerts]
    total = sum(a["amount_cents"] for a in alerts)
    lines += ["", f"Total: {format_rands(total)}", "",
              "Cancel or pause anything you no longer use before it charges."]
    return subject, "\n".join(lines), _alert_html(subject, alerts, reminder_days, total)


def summary_email(subs: Iterable[Sub], year: int, month: int, today: date) -> tuple[str, str, str]:
    """Build (subject, plain_text, html) for the monthly summary email.

    `today` only affects the HTML: charges before it are shown as already made (grey).
    """
    subs = list(subs)
    charges = charges_in_month(subs, year, month)
    due = sum(c["amount_cents"] for c in charges)
    label = month_label(year, month)
    subject = f"Subscription: {label} summary, {format_rands(due)} due"

    lines = [f"{label} summary", ""]
    lines.append(f"Due this month (actual charges): {format_rands(due)}")
    lines.append(f"Monthly average (all subscriptions spread per month): "
                 f"{format_rands(monthly_average_cents(subs))}")
    lines.append(f"Active subscriptions: {len(_active(subs))}")
    lines += ["", "Charges this month:"]
    if charges:
        lines += [_charge_line(c["name"], c["amount_cents"], c["charge_date"]) for c in charges]
    else:
        lines.append("- none")
    by_cat = monthly_average_by_category(subs)
    if by_cat:
        lines += ["", "Monthly average by category:"]
        lines += [f"- {cat}: {format_rands(cents)}" for cat, cents in by_cat.items()]
    html = _summary_html(subject, label, month, charges, due, monthly_average_cents(subs),
                         len(_active(subs)), by_cat, today)
    return subject, "\n".join(lines), html


# --- HTML email bodies -----------------------------------------------------------
# Markup and inline styles copied from docs/email_preview.html (plain "statement" style).
# Tables and inline CSS only, because many email clients ignore <style> blocks.
# Every subscription name and category goes through escape().

_TEXT = "#1a1a1a"
_SECONDARY = "#666666"
_MUTED = "#999999"
_RULE = "border-top:1px solid #e6e6e6;"
_TOTAL_RULE = "border-top:1px solid #1a1a1a;"
_FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_TABLE = 'role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'


def _plural(count: int, word: str) -> str:
    """'1 subscription', '3 subscriptions'."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _join_names(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _html_document(subject: str, preheader: str, content: str) -> str:
    """Wrap one email in a minimal HTML document with a hidden inbox preview line."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:#ffffff;">

<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px;">
  {preheader}
</div>

<table {_TABLE} style="background-color:#ffffff;">
  <tr>
    <td align="center" style="padding:36px 20px 40px 20px;">

      <table {_TABLE} style="max-width:560px;width:100%;font-family:{_FONT};color:{_TEXT};text-align:left;">
{content}
      </table>

    </td>
  </tr>
</table>

</body>
</html>
"""


def _heading(title: str, description: str, gap: int) -> str:
    return f"""
        <tr>
          <td style="font-size:20px;line-height:28px;font-weight:600;color:{_TEXT};padding-bottom:4px;">
            {title}
          </td>
        </tr>
        <tr>
          <td style="font-size:14px;line-height:21px;color:{_SECONDARY};padding-bottom:{gap}px;">
            {description}
          </td>
        </tr>
"""


def _section_heading(title: str, description: str, top: str = "") -> str:
    return f"""
        <tr>
          <td style="font-size:14px;line-height:21px;font-weight:600;color:{_TEXT};{top}padding-bottom:2px;">
            {title}
          </td>
        </tr>
        <tr>
          <td style="font-size:13px;line-height:19px;color:{_SECONDARY};padding-bottom:10px;">
            {description}
          </td>
        </tr>
"""


def _rows_table(rows: str) -> str:
    return f"""        <tr>
          <td>
            <table {_TABLE}>
{rows}
            </table>
          </td>
        </tr>
"""


def _alert_html(subject: str, alerts: list[Mapping[str, Any]], reminder_days: int, total: int) -> str:
    # Inbox preview line: "Netflix tomorrow, Showmax and Weekly veg box in 2 days. R483.00 in total."
    groups: list[tuple[str, list[str]]] = []
    for a in alerts:  # already sorted by date, so equal days_away are adjacent
        when = days_away_text(a["days_away"])
        if groups and groups[-1][0] == when:
            groups[-1][1].append(escape(a["name"]))
        else:
            groups.append((when, [escape(a["name"])]))
    preheader = (", ".join(f"{_join_names(names)} {when}" for when, names in groups)
                 + f". {format_rands(total)} in total.")

    cell = f"padding:14px 0;{_RULE}"
    rows = "".join(f"""
              <tr>
                <td style="{cell}" valign="top">
                  <div style="font-size:15px;line-height:22px;color:{_TEXT};">{escape(a["name"])}</div>
                  <div style="font-size:13px;line-height:19px;color:{_SECONDARY};">{short_date(a["charge_date"])}, {days_away_text(a["days_away"])}</div>
                </td>
                <td align="right" style="{cell}font-size:15px;line-height:22px;color:{_TEXT};" valign="top">{format_rands(a["amount_cents"])}</td>
              </tr>
""" for a in alerts)
    total_cell = f"padding:14px 0 0 0;{_TOTAL_RULE}font-size:15px;line-height:22px;font-weight:600;color:{_TEXT};"
    rows += f"""
              <tr>
                <td style="{total_cell}">Total</td>
                <td align="right" style="{total_cell}">{format_rands(total)}</td>
              </tr>
"""
    description = (f"{_plural(len(alerts), 'subscription')} will charge your account in the next "
                   f"{_plural(reminder_days, 'day')}, {format_rands(total)} in total.")
    content = (_heading("Upcoming subscription charges", description, 24)
               + "\n" + _rows_table(rows) + f"""
        <tr>
          <td style="font-size:14px;line-height:21px;color:{_SECONDARY};padding-top:28px;">
            Pause or cancel anything you no longer use before its date.
          </td>
        </tr>
""")
    return _html_document(subject, preheader, content)


def _figure(label: str, amount: str, note: str) -> str:
    return f"""                <td width="50%" valign="top">
                  <div style="font-size:13px;line-height:19px;color:{_SECONDARY};">{label}</div>
                  <div style="font-size:24px;line-height:32px;font-weight:600;color:{_TEXT};">{amount}</div>
                  <div style="font-size:12px;line-height:18px;color:{_MUTED};">{note}</div>
                </td>
"""


def _summary_html(
    subject: str,
    label: str,
    month: int,
    charges: list[Mapping[str, Any]],
    due: int,
    average: int,
    active_count: int,
    by_cat: Mapping[str, int],
    today: date,
) -> str:
    preheader = (f"{format_rands(due)} due this month across "
                 f"{_plural(active_count, 'active subscription')}.")

    figures = f"""
        <tr>
          <td style="padding-bottom:32px;">
            <table {_TABLE}>
              <tr>
{_figure("Due this month", format_rands(due), "what actually gets charged")}{_figure("Monthly average", format_rands(average), "weekly and yearly plans, spread evenly")}              </tr>
            </table>
          </td>
        </tr>
"""

    row = f"padding:10px 0;{_RULE}font-size:14px;line-height:20px;"
    charge_rows = ""
    for c in charges:
        past = c["charge_date"] < today  # a charge today is still upcoming, as everywhere else
        date_colour, text_colour = (_MUTED, _MUTED) if past else (_SECONDARY, _TEXT)
        charge_rows += f"""
              <tr>
                <td width="90" style="{row}color:{date_colour};" valign="top">{short_date(c["charge_date"])}</td>
                <td style="{row}color:{text_colour};" valign="top">{escape(c["name"])}</td>
                <td align="right" style="{row}color:{text_colour};" valign="top">{format_rands(c["amount_cents"])}</td>
              </tr>
"""
    total = f"padding:10px 0 0 0;{_TOTAL_RULE}font-size:14px;line-height:20px;font-weight:600;color:{_TEXT};"
    charge_rows += f"""
              <tr>
                <td width="90" style="padding:10px 0 0 0;{_TOTAL_RULE}">&nbsp;</td>
                <td style="{total}">Total</td>
                <td align="right" style="{total}">{format_rands(due)}</td>
              </tr>
"""

    cat_rows = "".join(f"""
              <tr>
                <td style="{row}color:{_TEXT};">{escape(cat)}</td>
                <td align="right" style="{row}color:{_TEXT};">{format_rands(cents)}</td>
              </tr>""" for cat, cents in by_cat.items())
    cat_rows += f"""

              <tr>
                <td style="{total}">Total</td>
                <td align="right" style="{total}">{format_rands(average)}</td>
              </tr>
"""

    month_name = _MONTHS[month - 1]
    content = (
        _heading("Monthly subscription summary",
                 f"Your subscription spending for {label}, across "
                 f"{_plural(active_count, 'active subscription')}.", 28)
        + figures
        + _section_heading("Charges this month",
                           f"Every charge that falls in {month_name}. Grey rows have already been charged.")
        + _rows_table(charge_rows)
        + _section_heading("Monthly average by category",
                           "What each category costs per month on average.", top="padding-top:32px;")
        + _rows_table(cat_rows)
    )
    return _html_document(subject, preheader, content)
