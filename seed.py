"""Fill a LOCAL development database with realistic fake subscriptions.

Usage:  python seed.py [--db dev.db] [--force]

Anchors are relative to today so there's always something due soon to look at.
Refuses to run at all while Turso is configured, and refuses to touch a database that
already has subscriptions unless --force is given, so fake data can't reach your real data.
"""

import argparse
import sys
from datetime import date, timedelta

from subtracker import config, db

SEED = [
    # name, amount_cents, frequency, anchor offset from today (days), category
    ("Netflix", 19900, "monthly", 1, "Streaming"),
    ("Spotify Family", 11999, "monthly", -20, "Streaming"),
    ("Showmax", 9900, "monthly", 2, "Streaming"),
    ("Virgin Active", 64900, "monthly", -3, "Fitness"),
    ("Microsoft 365", 129900, "yearly", -300, "Software"),
    ("Google One 100GB", 2999, "monthly", 10, "Software"),
    ("Weekly veg box", 18500, "weekly", -5, "Food"),
    ("Car insurance excess cover", 45000, "quarterly", -60, "Insurance"),
]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="local SQLite file (default: DB_PATH)")
    parser.add_argument("--force", action="store_true", help="seed even if data exists")
    args = parser.parse_args(argv)

    config.load_config()
    try:
        backend = config.database_backend()
    except config.ConfigError as exc:
        raise SystemExit(f"Refusing to seed: {exc}")
    if backend.is_turso:
        # Even with --db: if Turso is configured, this shell/.env is pointed at real data.
        raise SystemExit("Refusing to seed: Turso is configured (TURSO_DATABASE_URL / "
                         "TURSO_AUTH_TOKEN). Seed data is for local files only. Clear both "
                         "variables in this shell or .env first.")

    path = args.db or backend.location
    conn = db.connect(path)
    try:
        if db.list_subscriptions(conn) and not args.force:
            raise SystemExit(f"{path} already has subscriptions; use --force to add seed data anyway.")
        today = date.today()
        for name, cents, freq, offset, category in SEED:
            db.add_subscription(conn, name, cents, freq, today + timedelta(days=offset), category)
    finally:
        conn.close()
    print(f"Seeded {len(SEED)} subscriptions into {path}")


if __name__ == "__main__":
    main(sys.argv[1:])
