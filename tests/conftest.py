import sys
from pathlib import Path

import pytest

# Make the project root importable so tests can `import subtracker` and `import reminder`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from subtracker import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_from_real_config(monkeypatch):
    """Tests must never touch the live Turso database or read the real .env.

    Your .env may hold Turso credentials. Without this, a test that runs app.py or
    reminder.main() would load them and read or write your real data.
    """
    for var in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN", "GITHUB_ACTIONS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "load_config", lambda: None)
