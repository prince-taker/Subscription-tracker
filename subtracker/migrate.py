"""Copy a local SQLite database into another database (Turso in practice).

The copy works on any two connections, so tests run it between two temp SQLite files.
"""

import hashlib
import json
from dataclasses import dataclass, field

from subtracker import db

_SUB_COLS = db.SUBSCRIPTION_COLUMNS  # includes id, active, last_notified_for, created_at
_CHECKSUM_COLS = ("name", "amount_cents", "frequency", "anchor_date")


class MigrationError(RuntimeError):
    """The migration can't or shouldn't go ahead. The message says why."""


@dataclass
class MigrationReport:
    subscriptions: int = 0
    meta: int = 0
    replaced: int = 0  # destination rows deleted because of --force
    dry_run: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def count_rows(conn: db.Connection, table: str) -> int:
    """Row count, or 0 if the table doesn't exist yet."""
    if not db.table_exists(conn, table):
        return 0
    return db.query(conn, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]


def subscriptions_checksum(conn: db.Connection) -> str:
    """SHA-256 over (name, amount_cents, frequency, anchor_date) of every row, order-independent."""
    rows = db.query(conn, f"SELECT {', '.join(_CHECKSUM_COLS)} FROM subscriptions")
    canonical = sorted([r[c] for c in _CHECKSUM_COLS] for r in rows)
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def verify(source: db.Connection, dest: db.Connection) -> list[str]:
    """Compare row counts and the checksum. Returns a list of mismatches (empty means OK)."""
    problems = []
    for table in ("subscriptions", "meta"):
        src_n, dst_n = count_rows(source, table), count_rows(dest, table)
        if src_n != dst_n:
            problems.append(f"{table}: source has {src_n} rows, destination has {dst_n}")
    src_sum, dst_sum = subscriptions_checksum(source), subscriptions_checksum(dest)
    if src_sum != dst_sum:
        problems.append(f"subscriptions checksum differs: source {src_sum[:12]}..., "
                        f"destination {dst_sum[:12]}...")
    return problems


def copy_database(
    source: db.Connection, dest: db.Connection, force: bool = False, dry_run: bool = False
) -> MigrationReport:
    """Copy all subscriptions (keeping id, active, last_notified_for, created_at) and meta.

    Refuses if the destination already has subscriptions, unless force=True, in which case the
    destination's subscriptions and meta are REPLACED so it ends up an exact copy (merging
    would clash on ids and could never pass the checksum). dry_run reports and writes nothing.
    """
    if not db.table_exists(source, "subscriptions"):
        raise MigrationError("The source has no subscriptions table. Is it the right file?")
    subs = db.query(source, f"SELECT {', '.join(_SUB_COLS)} FROM subscriptions ORDER BY id")
    meta = db.query(source, "SELECT key, value FROM meta") if db.table_exists(source, "meta") else []

    existing = count_rows(dest, "subscriptions")
    if existing and not force:
        raise MigrationError(f"The destination already has {existing} subscription(s). "
                             "Re-run with --force to replace them with the source data.")
    report = MigrationReport(subscriptions=len(subs), meta=len(meta),
                             replaced=existing if force else 0, dry_run=dry_run)
    if dry_run:
        return report

    db.create_schema(dest)
    if force:
        dest.execute("DELETE FROM subscriptions")
        dest.execute("DELETE FROM meta")
    placeholders = ", ".join("?" for _ in _SUB_COLS)
    for row in subs:
        dest.execute(f"INSERT INTO subscriptions ({', '.join(_SUB_COLS)}) VALUES ({placeholders})",
                     tuple(row[c] for c in _SUB_COLS))
    for row in meta:
        dest.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (row["key"], row["value"]))
    dest.commit()

    report.problems = verify(source, dest)
    return report
