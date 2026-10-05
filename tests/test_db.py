from datetime import date

import pytest

from subtracker import db


@pytest.fixture(params=["sqlite3", "libsql"])
def conn(request, tmp_path):
    """Every db test runs on stdlib sqlite3 AND on a real (local-file) libsql connection,
    which is the same driver the app uses for Turso. Needs no network."""
    if request.param == "sqlite3":
        c = db.connect(":memory:")
    else:
        libsql = pytest.importorskip("libsql")
        c = db._prepare(libsql.connect(str(tmp_path / "libsql.db")), init_schema=True)
    yield c
    c.close()


def test_rows_are_the_same_plain_dicts_as_before(conn):
    sub_id = db.add_subscription(conn, "Netflix", 19900, "monthly", date(2026, 1, 31), "Streaming",
                                 "family plan")
    sub = db.get_subscription(conn, sub_id)
    created_at = sub.pop("created_at")
    assert isinstance(created_at, str) and len(created_at) == 19  # 'YYYY-MM-DD HH:MM:SS'
    assert sub == {
        "id": sub_id, "name": "Netflix", "amount_cents": 19900, "frequency": "monthly",
        "anchor_date": date(2026, 1, 31), "category": "Streaming", "active": True,
        "last_notified_for": None, "notes": "family plan",
    }
    assert type(sub["amount_cents"]) is int and type(sub["id"]) is int
    assert list(db.list_subscriptions(conn)[0]) == list(db.SUBSCRIPTION_COLUMNS)


def test_ids_increase_and_list_is_sorted(conn):
    b = db.add_subscription(conn, "beta", 100, "monthly", date(2026, 1, 1), "Z")
    a = db.add_subscription(conn, "Alpha", 100, "monthly", date(2026, 1, 1), "a")
    assert a == b + 1
    assert [s["name"] for s in db.list_subscriptions(conn)] == ["Alpha", "beta"]
    assert db.list_categories(conn) == ["a", "Z"]


def test_schema_creation_is_idempotent(conn):
    db.create_schema(conn)
    db.create_schema(conn)
    assert db.table_exists(conn, "subscriptions") and db.table_exists(conn, "meta")
    assert not db.table_exists(conn, "nope")


def test_add_and_get_roundtrip(conn):
    sub_id = db.add_subscription(conn, "  Netflix ", 19900, "monthly", date(2026, 1, 31), "Streaming")
    sub = db.get_subscription(conn, sub_id)
    assert sub["name"] == "Netflix"
    assert sub["anchor_date"] == date(2026, 1, 31)
    assert sub["active"] is True
    assert sub["last_notified_for"] is None


def test_update_pause_delete(conn):
    sub_id = db.add_subscription(conn, "Gym", 50000, "monthly", date(2026, 1, 1))
    db.update_subscription(conn, sub_id, amount_cents=55000, category="Fitness")
    db.set_active(conn, sub_id, False)
    sub = db.get_subscription(conn, sub_id)
    assert (sub["amount_cents"], sub["category"], sub["active"]) == (55000, "Fitness", False)
    assert db.list_subscriptions(conn, active_only=True) == []
    db.delete_subscription(conn, sub_id)
    assert db.get_subscription(conn, sub_id) is None


@pytest.mark.parametrize(
    "kwargs",
    [dict(name="   "), dict(amount_cents=0), dict(amount_cents=-5), dict(frequency="daily")],
)
def test_validation_errors(conn, kwargs):
    base = dict(name="X", amount_cents=100, frequency="monthly", anchor_date=date(2026, 1, 1))
    with pytest.raises(ValueError):
        db.add_subscription(conn, **{**base, **kwargs})


def test_update_rejects_unknown_field(conn):
    sub_id = db.add_subscription(conn, "X", 100, "monthly", date(2026, 1, 1))
    with pytest.raises(ValueError):
        db.update_subscription(conn, sub_id, id=99)


def test_meta_and_mark_notified(conn):
    assert db.get_meta(conn, "last_summary_month") is None
    db.set_meta(conn, "last_summary_month", "2026-09")
    db.set_meta(conn, "last_summary_month", "2026-10")
    assert db.get_meta(conn, "last_summary_month") == "2026-10"
    sub_id = db.add_subscription(conn, "X", 100, "monthly", date(2026, 1, 1))
    db.mark_notified(conn, {sub_id: date(2026, 10, 1)})
    assert db.get_subscription(conn, sub_id)["last_notified_for"] == date(2026, 10, 1)
