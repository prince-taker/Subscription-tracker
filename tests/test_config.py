from pathlib import Path

import pytest

from subtracker import config

URL = "libsql://subs-prince.aws-eu-west-1.turso.io"
TOKEN = "eyJ-secret-token"


def test_both_set_selects_turso(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)
    backend = config.database_backend()
    assert backend.is_turso
    assert backend.location == URL and backend.auth_token == TOKEN
    assert backend.label() == "Live database (Turso)"
    assert backend.describe() == "Database: Turso, host subs-prince.aws-eu-west-1.turso.io"


def test_neither_set_selects_local_file(monkeypatch):
    monkeypatch.setenv("DB_PATH", "dev.db")
    backend = config.database_backend()
    assert not backend.is_turso
    assert backend.location == str(config.PROJECT_DIR / "dev.db")
    assert backend.label() == "Local file: dev.db"


def test_blank_values_count_as_unset(monkeypatch):
    # GitHub Actions turns a missing secret into an empty string.
    monkeypatch.setenv("TURSO_DATABASE_URL", "  ")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
    assert not config.database_backend().is_turso


@pytest.mark.parametrize("set_var, missing", [
    ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"),
    ("TURSO_AUTH_TOKEN", "TURSO_DATABASE_URL"),
])
def test_only_one_set_is_an_error(monkeypatch, set_var, missing):
    monkeypatch.setenv(set_var, URL if set_var == "TURSO_DATABASE_URL" else TOKEN)
    with pytest.raises(config.ConfigError, match=f"{set_var} is set but {missing} is not"):
        config.database_backend()


def test_github_actions_without_turso_is_an_error(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(config.ConfigError, match="GitHub Actions"):
        config.database_backend()


def test_token_never_appears_in_output(monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)
    backend = config.database_backend()
    for text in (backend.describe(), backend.label(), repr(backend), str(backend)):
        assert TOKEN not in text


def test_error_message_never_contains_values(monkeypatch):
    monkeypatch.setenv("TURSO_AUTH_TOKEN", TOKEN)
    with pytest.raises(config.ConfigError) as exc:
        config.database_backend()
    assert TOKEN not in str(exc.value)
