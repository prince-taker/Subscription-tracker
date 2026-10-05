"""Smoke tests for the Streamlit UI using Streamlit's built-in AppTest (no browser needed)."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from subtracker import db

APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setenv("DB_PATH", str(path))
    return path


def run_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def test_empty_db_renders(db_file):
    at = run_app()
    assert [m.label for m in at.metric][2] == "Active subscriptions"


def test_dashboard_metrics_with_data(db_file):
    conn = db.connect(str(db_file))
    db.add_subscription(conn, "Annual", 240000, "yearly", date(2020, 3, 1), "Software")
    conn.close()
    at = run_app()
    values = [m.value for m in at.metric]
    assert values[1] == "R200.00"  # monthly average of a R2,400 yearly plan
    assert values[2] == "1"


def test_add_via_form_and_validation(db_file):
    at = run_app()  # empty DB, so the only "Name"/"Amount" inputs are the add form's
    at.button(key="FormSubmitter:add-Add subscription").click().run()
    assert any("Name can't be empty" in e.value for e in at.error)

    name = next(w for w in at.text_input if w.label == "Name")
    name.input("Netflix")
    next(w for w in at.number_input if w.label == "Amount (R)").set_value(199.99)
    at.button(key="FormSubmitter:add-Add subscription").click().run()
    assert not at.exception

    conn = db.connect(str(db_file))
    [sub] = db.list_subscriptions(conn)
    assert (sub["name"], sub["amount_cents"]) == ("Netflix", 19999)


def test_pause_button(db_file):
    conn = db.connect(str(db_file))
    sub_id = db.add_subscription(conn, "Gym", 64900, "monthly", date(2026, 1, 1))
    at = run_app()
    at.button(key=f"toggle_{sub_id}").click().run()
    assert db.get_subscription(conn, sub_id)["active"] is False
