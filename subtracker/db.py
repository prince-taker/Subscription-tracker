"""Database access: connection, schema creation and CRUD, for SQLite and Turso.

Both backends speak the same SQL. To work on both we only use the plain DB-API surface that
libsql and sqlite3 share: execute() with positional `?` parameters, fetchone/fetchall,
cursor.description, commit() and close(). Notably NOT row_factory (libsql has none), named
`:param` placeholders (libsql rejects them), executescript/executemany or lastrowid.

Rows come back as plain dicts with dates already parsed into `datetime.date`, so the pure
logic modules never see ISO strings.
"""

import sqlite3
from datetime import date
from typing import Any, Protocol, Sequence

from subtracker import config
from subtracker.schedule import FREQUENCIES

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS subscriptions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
    frequency         TEXT    NOT NULL CHECK (frequency IN ('weekly','monthly','quarterly','yearly')),
    anchor_date       TEXT    NOT NULL,
    category          TEXT    NOT NULL DEFAULT 'Other',
    active            INTEGER NOT NULL DEFAULT 1,
    last_notified_for TEXT,
    notes             TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)""",
)

SUBSCRIPTION_COLUMNS = ("id", "name", "amount_cents", "frequency", "anchor_date", "category",
                        "active", "last_notified_for", "notes", "created_at")


class Connection(Protocol):
    """What we need from a connection; both sqlite3 and libsql connections fit."""

    def execute(self, sql: str, parameters: Sequence[Any] = ..., /) -> Any: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


def connect(db_path: str | None = None, init_schema: bool = True) -> Connection:
    """Open a database and (by default) make sure the schema exists.

    db_path given: that local SQLite file (tests use ':memory:'; dev data; migration source).
    db_path None: whatever the env vars select, see config.database_backend().
    """
    if db_path is not None:
        return _prepare(sqlite3.connect(db_path), init_schema)
    return connect_backend(config.database_backend(), init_schema)


def connect_backend(backend: config.Backend, init_schema: bool = True) -> Connection:
    """Open the given backend. Turso connections are lazy: errors surface on first query."""
    if backend.is_turso:
        import libsql  # imported here so local-only use and the tests don't depend on it

        conn = libsql.connect(backend.location, auth_token=backend.auth_token)
    else:
        conn = sqlite3.connect(backend.location)
    return _prepare(conn, init_schema)


def _prepare(conn: Connection, init_schema: bool) -> Connection:
    if init_schema:
        create_schema(conn)
    return conn


def create_schema(conn: Connection) -> None:
    """Create the tables if they don't exist (one statement per execute, for libsql)."""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()


def query(conn: Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run a SELECT and return rows as dicts keyed by column name (works on both backends)."""
    cur = conn.execute(sql, tuple(params))
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _query_one(conn: Connection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    rows = query(conn, sql, params)
    return rows[0] if rows else None


def table_exists(conn: Connection, name: str) -> bool:
    """True if a table of this name exists."""
    return bool(query(conn, "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                      (name,)))


_EDITABLE = {"name", "amount_cents", "frequency", "anchor_date", "category", "active", "notes"}


def _to_dict(row: dict[str, Any]) -> dict[str, Any]:
    sub = dict(row)
    sub["anchor_date"] = date.fromisoformat(sub["anchor_date"])
    sub["last_notified_for"] = (
        date.fromisoformat(sub["last_notified_for"]) if sub["last_notified_for"] else None
    )
    sub["active"] = bool(sub["active"])
    return sub


def _validate(fields: dict[str, Any]) -> dict[str, Any]:
    """Validate/normalise user-editable fields. Raises ValueError with a friendly message."""
    clean = dict(fields)
    if "name" in clean:
        clean["name"] = (clean["name"] or "").strip()
        if not clean["name"]:
            raise ValueError("Name can't be empty.")
    if "amount_cents" in clean:
        if not isinstance(clean["amount_cents"], int) or clean["amount_cents"] <= 0:
            raise ValueError("Amount must be greater than R0.00.")
    if "frequency" in clean and clean["frequency"] not in FREQUENCIES:
        raise ValueError(f"Frequency must be one of: {', '.join(FREQUENCIES)}.")
    if "anchor_date" in clean:
        if not isinstance(clean["anchor_date"], date):
            raise ValueError("Anchor date must be a date.")
        clean["anchor_date"] = clean["anchor_date"].isoformat()
    if "category" in clean:
        clean["category"] = (clean["category"] or "").strip() or "Other"
    if "active" in clean:
        clean["active"] = int(bool(clean["active"]))
    if "notes" in clean:
        clean["notes"] = (clean["notes"] or "").strip() or None
    return clean


def add_subscription(
    conn: Connection,
    name: str,
    amount_cents: int,
    frequency: str,
    anchor_date: date,
    category: str = "Other",
    notes: str | None = None,
) -> int:
    """Insert a subscription and return its id."""
    f = _validate(dict(name=name, amount_cents=amount_cents, frequency=frequency,
                       anchor_date=anchor_date, category=category, notes=notes))
    # RETURNING id is plain SQL, so it doesn't depend on driver support for lastrowid.
    new_id = conn.execute(
        "INSERT INTO subscriptions (name, amount_cents, frequency, anchor_date, category, notes)"
        " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        (f["name"], f["amount_cents"], f["frequency"], f["anchor_date"], f["category"], f["notes"]),
    ).fetchone()[0]
    conn.commit()
    return new_id


def update_subscription(conn: Connection, sub_id: int, **fields: Any) -> None:
    """Update any of: name, amount_cents, frequency, anchor_date, category, active, notes.

    If the anchor or frequency changes, the old `last_notified_for` may no longer match a real
    charge date; that's harmless (it simply won't equal the new next charge), so we leave it.
    """
    unknown = set(fields) - _EDITABLE
    if unknown:
        raise ValueError(f"Can't edit field(s): {', '.join(sorted(unknown))}")
    if not fields:
        return
    f = _validate(fields)
    columns = list(f)  # only names from _EDITABLE, so safe to put in the SQL text
    assignments = ", ".join(f"{col} = ?" for col in columns)
    conn.execute(f"UPDATE subscriptions SET {assignments} WHERE id = ?",
                 (*(f[col] for col in columns), sub_id))
    conn.commit()


def set_active(conn: Connection, sub_id: int, active: bool) -> None:
    """Pause (False) or resume (True) a subscription."""
    update_subscription(conn, sub_id, active=active)


def delete_subscription(conn: Connection, sub_id: int) -> None:
    """Permanently delete a subscription. Prefer set_active(False) to keep history."""
    conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))
    conn.commit()


def get_subscription(conn: Connection, sub_id: int) -> dict[str, Any] | None:
    """Return one subscription as a dict, or None."""
    row = _query_one(conn, "SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
    return _to_dict(row) if row else None


def list_subscriptions(conn: Connection, active_only: bool = False) -> list[dict[str, Any]]:
    """Return all (or only active) subscriptions, ordered by name."""
    sql = "SELECT * FROM subscriptions"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY name COLLATE NOCASE"
    return [_to_dict(r) for r in query(conn, sql)]


def list_categories(conn: Connection) -> list[str]:
    """Distinct categories in use, sorted."""
    rows = query(conn, "SELECT DISTINCT category FROM subscriptions ORDER BY category COLLATE NOCASE")
    return [r["category"] for r in rows]


def mark_notified(conn: Connection, notified: dict[int, date]) -> None:
    """Record, per subscription id, the charge date we just sent an alert for."""
    for sub_id, charge_date in notified.items():
        conn.execute("UPDATE subscriptions SET last_notified_for = ? WHERE id = ?",
                     (charge_date.isoformat(), sub_id))
    conn.commit()


def get_meta(conn: Connection, key: str) -> str | None:
    """Read a value from the meta table."""
    row = _query_one(conn, "SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else None


def set_meta(conn: Connection, key: str, value: str) -> None:
    """Write a value to the meta table."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
