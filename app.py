"""Streamlit UI. Thin layer: all logic lives in subtracker/.

Run with:  streamlit run app.py
"""

from datetime import date

import pandas as pd
import streamlit as st

from reminder import SUMMARY_META_KEY, find_due_alerts
from subtracker import config, db, emailer
from subtracker.backup import backup_filename, backup_json
from subtracker.money import format_rands, rands_to_cents
from subtracker.schedule import FREQUENCIES, next_charge_date, monthly_equivalent_cents
from subtracker.summary import (
    due_this_month_cents,
    month_label,
    monthly_average_by_category,
    monthly_average_cents,
    upcoming_charges,
)

DEFAULT_CATEGORIES = ["Streaming", "Software", "Fitness", "Insurance", "Food", "Utilities", "Other"]
NEW_CATEGORY = "New category..."
UPCOMING_DAYS = 30

st.set_page_config(page_title="Subscription Tracker", page_icon="💸", layout="wide")

config.load_config()
try:
    backend = config.database_backend()
except config.ConfigError as exc:
    st.error(f"Database not configured correctly: {exc}")
    st.stop()

# Say which database this is before anything else, so editing the wrong one is hard to miss.
st.caption(f"**{backend.label()}**")

# A fresh connection per rerun: a few queries for Turso, and it avoids sharing one connection
# across Streamlit's threads (sqlite3 and libsql connections are bound to their thread).
try:
    conn = db.connect_backend(backend)
except Exception as exc:  # noqa: BLE001 - e.g. Turso unreachable or a bad token
    st.error(f"Couldn't open the database ({backend.label()}): {exc}")
    st.stop()
today = config.sa_today()


def flash(message: str) -> None:
    """Show a success message after the next rerun."""
    st.session_state["flash"] = message


def category_options() -> list[str]:
    existing = db.list_categories(conn)
    return sorted(set(DEFAULT_CATEGORIES) | set(existing), key=str.lower)


def amount_to_cents(amount: float) -> int:
    """number_input gives a float; go via a 2dp string so 199.99 stays 19999 cents."""
    return rands_to_cents(f"{amount:.2f}")


st.title("Subscription Tracker")
if msg := st.session_state.pop("flash", None):
    st.success(msg)

subs = db.list_subscriptions(conn)
active_subs = [s for s in subs if s["active"]]

tab_dash, tab_manage, tab_alerts = st.tabs(["Dashboard", "Manage", "Alerts"])

# --- Dashboard -------------------------------------------------------------------
with tab_dash:
    c1, c2, c3 = st.columns(3)
    c1.metric(
        f"Due this month ({month_label(today.year, today.month)})",
        format_rands(due_this_month_cents(subs, today.year, today.month)),
        help="Cash view: the actual charges that land in this calendar month. "
             "An annual plan counts in full in the month it charges, and not at all otherwise.",
    )
    c2.metric(
        "Monthly average",
        format_rands(monthly_average_cents(subs)),
        help="Budget view: every active subscription spread evenly per month "
             "(weekly x 52 / 12, quarterly / 3, yearly / 12).",
    )
    c3.metric("Active subscriptions", len(active_subs))

    st.subheader(f"Upcoming charges (next {UPCOMING_DAYS} days)")
    upcoming = upcoming_charges(subs, today, UPCOMING_DAYS)
    if upcoming:
        st.dataframe(
            pd.DataFrame([
                {"Date": r["charge_date"], "Name": r["name"],
                 "Amount": format_rands(r["amount_cents"]), "Days away": r["days_away"],
                 "Category": r["category"]}
                for r in upcoming
            ]),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Nothing charges in the next 30 days.")

    st.subheader("Monthly average by category")
    by_cat = monthly_average_by_category(subs)
    if by_cat:
        chart = pd.DataFrame(
            {"Rands per month": [cents / 100 for cents in by_cat.values()]},  # float only for plotting
            index=pd.Index(list(by_cat.keys()), name="Category"),
        )
        st.bar_chart(chart)
    else:
        st.info("Add a subscription to see spend by category.")

# --- Manage ----------------------------------------------------------------------
with tab_manage:
    st.subheader("All subscriptions")
    if subs:
        st.dataframe(
            pd.DataFrame([
                {"Name": s["name"], "Amount": format_rands(s["amount_cents"]),
                 "Frequency": s["frequency"],
                 "Next charge": next_charge_date(s["anchor_date"], s["frequency"], today)
                 if s["active"] else None,
                 "Monthly avg": format_rands(monthly_equivalent_cents(s["amount_cents"], s["frequency"])),
                 "Category": s["category"], "Status": "Active" if s["active"] else "Paused"}
                for s in subs
            ]),
            hide_index=True,
            width="stretch",
        )

        now = config.sa_now()
        st.download_button(
            "Download backup",
            data=backup_json(conn, now, backend.label()),
            file_name=backup_filename(now),
            mime="application/json",
            help="Everything as JSON. Turso's free plan only restores the last day, so keep your own copy.",
        )

        st.caption("Edit, pause or delete. Pause when you cancel something; it keeps the history.")
        for s in subs:
            status = "" if s["active"] else " (paused)"
            with st.expander(f"{s['name']} · {format_rands(s['amount_cents'])} {s['frequency']}{status}"):
                toggle_label = "Pause" if s["active"] else "Resume"
                if st.button(toggle_label, key=f"toggle_{s['id']}"):
                    db.set_active(conn, s["id"], not s["active"])
                    flash(f"{s['name']} {'paused' if s['active'] else 'resumed'}.")
                    st.rerun()

                with st.form(key=f"edit_{s['id']}"):
                    name = st.text_input("Name", value=s["name"])
                    amount = st.number_input("Amount (R)", min_value=0.0, step=1.0, format="%.2f",
                                             value=s["amount_cents"] / 100)
                    frequency = st.selectbox("Frequency", FREQUENCIES,
                                             index=FREQUENCIES.index(s["frequency"]))
                    anchor = st.date_input("Anchor date (any real charge date)", value=s["anchor_date"])
                    category = st.text_input("Category", value=s["category"])
                    notes = st.text_area("Notes", value=s["notes"] or "")
                    save_col, del_col = st.columns(2)
                    save = save_col.form_submit_button("Save changes", type="primary")
                    confirm = del_col.checkbox("Yes, delete permanently")
                    delete = del_col.form_submit_button("Delete")

                if save:
                    try:
                        db.update_subscription(
                            conn, s["id"], name=name, amount_cents=amount_to_cents(amount),
                            frequency=frequency, anchor_date=anchor, category=category, notes=notes,
                        )
                        flash(f"Saved {name.strip()}.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                if delete:
                    if confirm:
                        db.delete_subscription(conn, s["id"])
                        flash(f"Deleted {s['name']}.")
                        st.rerun()
                    else:
                        st.warning("Tick 'Yes, delete permanently' first, or use Pause instead.")
    else:
        st.info("No subscriptions yet. Add your first one below.")

    st.subheader("Add a subscription")
    with st.form("add", clear_on_submit=True):
        name = st.text_input("Name", placeholder="e.g. Netflix")
        amount = st.number_input("Amount (R)", min_value=0.0, step=1.0, format="%.2f")
        frequency = st.selectbox("Frequency", FREQUENCIES, index=FREQUENCIES.index("monthly"))
        anchor = st.date_input("Anchor date (any real charge date, past or next)", value=today)
        options = category_options() + [NEW_CATEGORY]
        category = st.selectbox("Category", options, index=options.index("Other"))
        new_category = st.text_input(f"New category name (only used with '{NEW_CATEGORY}')")
        notes = st.text_area("Notes (optional)")
        submitted = st.form_submit_button("Add subscription", type="primary")

    if submitted:
        chosen = new_category if category == NEW_CATEGORY else category
        try:
            if category == NEW_CATEGORY and not new_category.strip():
                raise ValueError("Type a name for the new category.")
            db.add_subscription(conn, name, amount_to_cents(amount), frequency, anchor, chosen, notes)
            flash(f"Added {name.strip()}.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

# --- Alerts ----------------------------------------------------------------------
with tab_alerts:
    reminder_days = config.reminder_days()
    st.subheader("Email")
    st.write("Sends a test email to `GMAIL_ADDRESS` so you can check SMTP and your Gmail filter.")
    if st.button("Send test email"):
        try:
            emailer.send_email(
                "Subscription: test email",
                f"Test from Subscription Tracker on {today.isoformat()}. If you can read this, "
                "alerts will reach you.",
            )
            st.success("Sent. Check your inbox (and that it didn't land in spam or Promotions).")
        except Exception as exc:  # noqa: BLE001 - show any SMTP/config problem as a friendly error
            st.error(f"Couldn't send: {exc}")

    st.subheader("Status")
    last_summary = db.get_meta(conn, SUMMARY_META_KEY)
    st.write(f"Last monthly summary sent for: **{last_summary or 'never'}**")
    st.write(f"Reminder window: **{reminder_days} day(s)** before a charge (`REMINDER_DAYS`).")
    pending = find_due_alerts(subs, today, reminder_days)
    if pending:
        st.write("The next `reminder.py` run would alert for:")
        for a in pending:
            st.write(f"- {a['name']}: {format_rands(a['amount_cents'])} on "
                     f"{a['charge_date'].isoformat()}")
    else:
        st.write("No unsent alerts are due right now.")
