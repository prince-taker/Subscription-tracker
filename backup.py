"""Write all subscriptions to a timestamped JSON file in backups/ (git-ignored).

    python backup.py

Uses whichever database your env vars select (Turso or the local DB_PATH file).
"""

import sys

from subtracker import config, db
from subtracker.backup import backup_filename, backup_json

BACKUP_DIR = "backups"


def main() -> int:
    config.load_config()
    try:
        backend = config.database_backend()
    except config.ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    print(backend.describe())
    now = config.sa_now()
    conn = db.connect_backend(backend, init_schema=False)
    try:
        text = backup_json(conn, now, backend.label())
    finally:
        conn.close()
    folder = config.PROJECT_DIR / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    path = folder / backup_filename(now)
    path.write_text(text, encoding="utf-8")
    print(f"Backup written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
