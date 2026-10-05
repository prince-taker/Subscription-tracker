"""Migration between two temp SQLite files (no network)."""

from datetime import date

import pytest

import migrate_to_turso
from subtracker import db
from subtracker.migrate import (
    MigrationError,
    copy_database,
    count_rows,
    subscriptions_checksum,
    verify,
)

TOKEN = "eyJ-secret-token"


def all_rows(conn):
    return db.query(conn, "SELECT * FROM subscriptions ORDER BY id"), \
        db.query(conn, "SELECT * FROM meta ORDER BY key")


@pytest.fixture
def source(tmp_path):
    conn = db.connect(str(tmp_path / "source.db"))
    a = db.add_subscription(conn, "Netflix", 19900, "monthly", date(2026, 1, 5), "Streaming", "4K")
    b = db.add_subscription(conn, "Old gym", 50000, "monthly", date(2025, 3, 1), "Fitness")
    db.delete_subscription(conn, a)  # leave a gap in the ids, so id preservation is visible
    c = db.add_subscription(conn, "Tom & Jerry <TV>", 9900, "yearly", date(2024, 2, 29), "Kids")
    db.set_active(conn, b, False)
    db.mark_notified(conn, {c: date(2026, 2, 28)})
    conn.execute("UPDATE subscriptions SET created_at = '2024-01-01 09:00:00' WHERE id = ?", (b,))
    conn.commit()
    db.set_meta(conn, "last_summary_month", "2026-10")
    yield conn
    conn.close()


@pytest.fixture
def dest(tmp_path):
    conn = db.connect(str(tmp_path / "dest.db"), init_schema=False)
    yield conn
    conn.close()


def test_copy_preserves_every_column_and_meta(source, dest):
    report = copy_database(source, dest)
    assert report.ok, report.problems
    assert (report.subscriptions, report.meta) == (2, 1)
    assert all_rows(dest) == all_rows(source)  # id, active, last_notified_for, created_at included
    ids = [r["id"] for r in all_rows(dest)[0]]
    assert ids == [2, 3]


def test_new_rows_after_migration_get_fresh_ids(source, dest):
    copy_database(source, dest)
    assert db.add_subscription(dest, "New", 100, "monthly", date(2026, 1, 1)) == 4


def test_refuses_if_destination_has_subscriptions(source, dest):
    db.create_schema(dest)
    db.add_subscription(dest, "Already here", 100, "monthly", date(2026, 1, 1))
    before = all_rows(dest)
    with pytest.raises(MigrationError, match="already has 1 subscription"):
        copy_database(source, dest)
    assert all_rows(dest) == before


def test_force_replaces_destination(source, dest):
    db.create_schema(dest)
    db.add_subscription(dest, "Already here", 100, "monthly", date(2026, 1, 1))
    db.set_meta(dest, "last_summary_month", "2020-01")
    report = copy_database(source, dest, force=True)
    assert report.ok and report.replaced == 1
    assert all_rows(dest) == all_rows(source)


def test_dry_run_writes_nothing(source, dest):
    report = copy_database(source, dest, dry_run=True)
    assert report.dry_run and report.subscriptions == 2
    assert not db.table_exists(dest, "subscriptions")


def test_source_without_schema_is_rejected(tmp_path, dest):
    empty = db.connect(str(tmp_path / "wrong.db"), init_schema=False)
    with pytest.raises(MigrationError, match="no subscriptions table"):
        copy_database(empty, dest)


def test_checksum_matches_and_detects_tampering(source, dest):
    copy_database(source, dest)
    assert subscriptions_checksum(source) == subscriptions_checksum(dest)
    assert verify(source, dest) == []

    dest.execute("UPDATE subscriptions SET amount_cents = 1 WHERE id = 3")
    dest.commit()
    problems = verify(source, dest)
    assert len(problems) == 1 and "checksum differs" in problems[0]

    dest.execute("DELETE FROM meta")
    dest.commit()
    assert any("meta: source has 1 rows, destination has 0" in p for p in verify(source, dest))


def test_checksum_ignores_row_order_and_non_checksum_columns(tmp_path):
    a = db.connect(str(tmp_path / "a.db"))
    b = db.connect(str(tmp_path / "b.db"))
    db.add_subscription(a, "X", 100, "monthly", date(2026, 1, 1))
    db.add_subscription(a, "Y", 200, "weekly", date(2026, 1, 2))
    db.add_subscription(b, "Y", 200, "weekly", date(2026, 1, 2), category="Other cat")
    db.add_subscription(b, "X", 100, "monthly", date(2026, 1, 1))
    assert subscriptions_checksum(a) == subscriptions_checksum(b)
    assert count_rows(b, "nope") == 0


# --- CLI ---------------------------------------------------------------------------

def turso_stand_in(monkeypatch, tmp_path):
    """Configure Turso, but route the connection to a local file (tests are offline)."""
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://subs-prince.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)
    target = str(tmp_path / "remote_stand_in.db")
    monkeypatch.setattr(db, "connect_backend",
                        lambda backend, init_schema=True: db.connect(target, init_schema))
    return target


def test_cli_copies_and_reports_ok(monkeypatch, tmp_path, source, capsys):
    target = turso_stand_in(monkeypatch, tmp_path)
    assert migrate_to_turso.main(["--source", str(tmp_path / "source.db")]) == 0
    out, err = capsys.readouterr()
    assert "Destination: Turso, host subs-prince.turso.io" in out
    assert "Verify: OK" in out
    assert TOKEN not in out + err
    assert all_rows(db.connect(target)) == all_rows(source)


def test_cli_refuses_second_run_without_force(monkeypatch, tmp_path, source, capsys):
    turso_stand_in(monkeypatch, tmp_path)
    args = ["--source", str(tmp_path / "source.db")]
    assert migrate_to_turso.main(args) == 0
    assert migrate_to_turso.main(args) == 1
    assert "already has 2 subscription(s)" in capsys.readouterr().err
    assert migrate_to_turso.main(args + ["--force"]) == 0


def test_cli_dry_run(monkeypatch, tmp_path, source, capsys):
    target = turso_stand_in(monkeypatch, tmp_path)
    assert migrate_to_turso.main(["--source", str(tmp_path / "source.db"), "--dry-run"]) == 0
    assert "would copy 2 subscription(s)" in capsys.readouterr().out
    assert not db.table_exists(db.connect(target, init_schema=False), "subscriptions")


def test_cli_missing_source_is_not_created(monkeypatch, tmp_path, capsys):
    turso_stand_in(monkeypatch, tmp_path)
    missing = tmp_path / "typo.db"
    assert migrate_to_turso.main(["--source", str(missing)]) == 1
    assert "Source file not found" in capsys.readouterr().err
    assert not missing.exists()


def test_cli_requires_turso(tmp_path, source, capsys):
    assert migrate_to_turso.main(["--source", str(tmp_path / "source.db")]) == 2
    assert "Turso isn't configured" in capsys.readouterr().err


def test_copy_into_libsql_destination(source, tmp_path):
    """Same driver as the real Turso copy, against a local file."""
    libsql = pytest.importorskip("libsql")
    dest = libsql.connect(str(tmp_path / "libsql_dest.db"))
    report = copy_database(source, dest)
    assert report.ok, report.problems
    assert all_rows(dest) == all_rows(source)
    with pytest.raises(MigrationError):
        copy_database(source, dest)
    assert copy_database(source, dest, force=True).ok
