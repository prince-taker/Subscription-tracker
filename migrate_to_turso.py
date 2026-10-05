"""One-off: copy your local SQLite data into the Turso database configured in .env.

    python migrate_to_turso.py --source subscriptions.db --dry-run   # look first
    python migrate_to_turso.py --source subscriptions.db             # copy + verify
    python migrate_to_turso.py --source subscriptions.db --force     # replace remote data

Needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN. Prints the Turso hostname, never the token.
Exit code 0 means the copy verified OK.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from subtracker import config, db
from subtracker.migrate import MigrationError, copy_database


def open_source_read_only(path: Path) -> sqlite3.Connection:
    """Open the local file read-only, so a typo can't create a new empty database."""
    if not path.is_file():
        raise MigrationError(f"Source file not found: {path}")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy local subscriptions into Turso.")
    parser.add_argument("--source", type=Path, help="local SQLite file (default: DB_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be copied; write nothing")
    parser.add_argument("--force", action="store_true",
                        help="replace the remote data if Turso already has subscriptions")
    args = parser.parse_args(argv)

    config.load_config()
    try:
        backend = config.database_backend()
    except config.ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    if not backend.is_turso:
        print("Turso isn't configured: set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN in .env.",
              file=sys.stderr)
        return 2

    source_path = (args.source or Path(config.db_path())).resolve()
    print(f"Source:      local SQLite file {source_path}")
    print(f"Destination: Turso, host {backend.host}" + (" (dry run)" if args.dry_run else ""))
    try:
        source = open_source_read_only(source_path)
        dest = db.connect_backend(backend, init_schema=False)
        try:
            report = copy_database(source, dest, force=args.force, dry_run=args.dry_run)
        finally:
            source.close()
            dest.close()
    except MigrationError as exc:
        print(f"Not migrated: {exc}", file=sys.stderr)
        return 1

    if report.replaced:
        verb = "Would replace" if report.dry_run else "Replaced"
        print(f"{verb} {report.replaced} existing remote subscription(s).")
    if report.dry_run:
        print(f"Dry run: would copy {report.subscriptions} subscription(s) and "
              f"{report.meta} meta row(s). Nothing written.")
        return 0
    print(f"Copied {report.subscriptions} subscription(s) and {report.meta} meta row(s).")
    if report.ok:
        print("Verify: OK (row counts and checksum match)")
        return 0
    print("Verify: MISMATCH", file=sys.stderr)
    for problem in report.problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
