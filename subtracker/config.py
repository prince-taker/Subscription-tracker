"""Config loading, database backend selection and "today" in South African time.

Shared by app.py, reminder.py, seed.py, backup.py and migrate_to_turso.py.
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REMINDER_DAYS = 2


def load_config() -> None:
    """Load .env from the project folder, not the current directory.

    Schedulers (cron, Task Scheduler, launchd) often run with a different working
    directory, so a plain load_dotenv() would silently find nothing.
    """
    load_dotenv(PROJECT_DIR / ".env")


def db_path() -> str:
    """DB_PATH from the environment; relative paths are resolved against the project folder."""
    raw = os.environ.get("DB_PATH", "").strip() or "subscriptions.db"
    path = Path(raw)
    return str(path if path.is_absolute() else PROJECT_DIR / path)


class ConfigError(RuntimeError):
    """The configuration is unsafe or incomplete. The message says how to fix it."""


@dataclass(frozen=True)
class Backend:
    """Which database to use. Built only by database_backend()."""

    kind: str  # "turso" or "sqlite"
    location: str  # Turso URL or absolute SQLite file path
    auth_token: str = field(default="", repr=False)  # repr=False: never ends up in logs/tracebacks

    @property
    def is_turso(self) -> bool:
        return self.kind == "turso"

    @property
    def host(self) -> str:
        """Hostname of the Turso URL (safe to print; the token lives elsewhere)."""
        return urlparse(self.location).hostname or "(unrecognised URL)"

    def label(self) -> str:
        """Short label for the UI: 'Live database (Turso)' or 'Local file: dev.db'."""
        return "Live database (Turso)" if self.is_turso else f"Local file: {Path(self.location).name}"

    def describe(self) -> str:
        """One line for logs. Shows the Turso hostname only, never the token."""
        if self.is_turso:
            return f"Database: Turso, host {self.host}"
        return f"Database: local SQLite file {self.location}"


def database_backend() -> Backend:
    """Pick the database from env vars. Never silently falls back to a local file.

    - TURSO_DATABASE_URL and TURSO_AUTH_TOKEN both set: Turso.
    - Neither set: the local SQLite file at DB_PATH.
    - Only one set: ConfigError. A half-configured Turso must not quietly become an empty
      local database, because then the job reads nothing and never alerts.
    - Neither set while running in GitHub Actions: ConfigError. The runner's disk is wiped
      after every run, so a local file there is always empty and would send a R0.00 summary.
    """
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if url and token:
        return Backend("turso", url, token)
    if url or token:
        have, missing = (("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN") if url
                         else ("TURSO_AUTH_TOKEN", "TURSO_DATABASE_URL"))
        raise ConfigError(f"{have} is set but {missing} is not. Set both to use Turso, or "
                          "neither to use the local SQLite file at DB_PATH.")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise ConfigError("Running in GitHub Actions without TURSO_DATABASE_URL and "
                          "TURSO_AUTH_TOKEN. Add both as repository secrets.")
    return Backend("sqlite", db_path())


def reminder_days() -> int:
    """REMINDER_DAYS from the environment (default 2). Must be a non-negative integer."""
    raw = os.environ.get("REMINDER_DAYS", "").strip()
    if not raw:
        return DEFAULT_REMINDER_DAYS
    try:
        days = int(raw)
    except ValueError as exc:
        raise ValueError(f"REMINDER_DAYS must be a whole number, got {raw!r}") from exc
    if days < 0:
        raise ValueError("REMINDER_DAYS can't be negative")
    return days


def _sa_timezone() -> timezone:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Africa/Johannesburg")  # type: ignore[return-value]
    except Exception:  # ZoneInfoNotFoundError, or missing tz data
        return timezone(timedelta(hours=2))


def sa_now() -> datetime:
    """Current time in Africa/Johannesburg (see sa_today for the fallback)."""
    return datetime.now(_sa_timezone())


def sa_today() -> date:
    """Today's date in Africa/Johannesburg, regardless of the machine's timezone.

    Windows Python ships without the IANA tz database (it needs the `tzdata` package), so
    if zoneinfo can't find the zone we fall back to a fixed UTC+2. That is exact for South
    Africa, which has no daylight saving.
    """
    return sa_now().date()
