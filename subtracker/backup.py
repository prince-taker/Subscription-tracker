"""Build a JSON backup of every subscription and the meta table.

Turso's free plan only restores the last day, so this is the long-term copy. Used by
backup.py (writes a file into backups/) and the app's "Download backup" button.
"""

import json
from datetime import datetime
from typing import Any

from subtracker import db


def export_all(conn: db.Connection) -> dict[str, Any]:
    """Every column of every subscription (as stored: ISO date strings) plus meta."""
    subs = db.query(conn, f"SELECT {', '.join(db.SUBSCRIPTION_COLUMNS)} FROM subscriptions ORDER BY id")
    meta = {r["key"]: r["value"] for r in db.query(conn, "SELECT key, value FROM meta ORDER BY key")}
    return {"subscriptions": subs, "meta": meta}


def backup_filename(now: datetime) -> str:
    """'subscriptions-2026-10-04-074700.json': sorts by time, safe on Windows (no colons)."""
    return f"subscriptions-{now:%Y-%m-%d-%H%M%S}.json"


def backup_json(conn: db.Connection, now: datetime, source_label: str) -> str:
    """The backup as pretty JSON text. `source_label` is e.g. 'Live database (Turso)'."""
    payload = {"exported_at": now.isoformat(timespec="seconds"), "source": source_label,
               **export_all(conn)}
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
