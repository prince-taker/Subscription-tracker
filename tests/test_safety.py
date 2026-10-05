"""Safety rails: seed refuses on Turso, reminder and app say which database they use."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import reminder
import seed
from subtracker import db

URL = "libsql://subs-prince.turso.io"
TOKEN = "eyJ-secret-token"
APP = str(Path(__file__).resolve().parent.parent / "app.py")


def turso_env(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)


# --- seed.py ---------------------------------------------------------------------

def test_seed_refuses_when_turso_configured(monkeypatch, tmp_path):
    turso_env(monkeypatch)
    target = tmp_path / "dev.db"
    with pytest.raises(SystemExit, match="Turso is configured"):
        seed.main(["--db", str(target)])
    assert not target.exists()


def test_seed_refuses_when_turso_half_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    with pytest.raises(SystemExit, match="TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is not"):
        seed.main(["--db", str(tmp_path / "dev.db")])


def test_seed_works_on_local_file_and_refuses_twice(tmp_path, capsys):
    target = tmp_path / "dev.db"
    seed.main(["--db", str(target)])
    assert len(db.list_subscriptions(db.connect(str(target)))) == len(seed.SEED)
    with pytest.raises(SystemExit, match="already has subscriptions"):
        seed.main(["--db", str(target)])


def test_seed_default_path_is_db_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "from_env.db"))
    seed.main([])
    assert (tmp_path / "from_env.db").exists()


# --- reminder.py -----------------------------------------------------------------

def test_reminder_prints_local_backend(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr("subtracker.config.PROJECT_DIR", tmp_path)
    assert reminder.main(["--dry-run", "--today", "2026-10-04"]) == 0
    first_line = capsys.readouterr().out.splitlines()[0]
    assert first_line == f"Database: local SQLite file {tmp_path / 'x.db'} (dry run)"


def test_reminder_prints_turso_host_never_token(monkeypatch, tmp_path, capsys):
    turso_env(monkeypatch)
    monkeypatch.setattr("subtracker.config.PROJECT_DIR", tmp_path)
    # Stand in for Turso with a local file so this needs no network.
    local = str(tmp_path / "stand_in.db")
    monkeypatch.setattr(reminder.db, "connect_backend", lambda backend: db.connect(local))
    assert reminder.main(["--dry-run", "--today", "2026-10-04"]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "Database: Turso, host subs-prince.turso.io (dry run)"
    assert TOKEN not in out + err


def test_reminder_half_configured_exits_nonzero_without_touching_db(monkeypatch, capsys):
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)

    def no_connect(*args, **kwargs):
        raise AssertionError("must not connect")

    monkeypatch.setattr(reminder.db, "connect_backend", no_connect)
    assert reminder.main(["--dry-run"]) == 2
    out, err = capsys.readouterr()
    assert "TURSO_AUTH_TOKEN is set but TURSO_DATABASE_URL is not" in err
    assert TOKEN not in out + err


def test_reminder_in_github_actions_without_turso_fails(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert reminder.main(["--dry-run"]) == 2
    assert "GitHub Actions" in capsys.readouterr().err


# --- app.py ----------------------------------------------------------------------

def test_app_shows_local_file_label(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "dev.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert at.caption[0].value == "**Local file: dev.db**"


def test_app_shows_turso_label(monkeypatch, tmp_path):
    turso_env(monkeypatch)
    local = str(tmp_path / "stand_in.db")
    monkeypatch.setattr(db, "connect_backend", lambda backend: db.connect(local))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert at.caption[0].value == "**Live database (Turso)**"


def test_app_half_configured_shows_error_and_stops(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert "TURSO_DATABASE_URL is set but TURSO_AUTH_TOKEN is not" in at.error[0].value
    assert len(at.tabs) == 0
