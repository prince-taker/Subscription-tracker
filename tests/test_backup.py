import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

import backup
from subtracker import db
from subtracker.backup import backup_filename, backup_json, export_all

SAST = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 4, 7, 47, 0, tzinfo=SAST)
APP = str(Path(__file__).resolve().parent.parent / "app.py")


def make_db(path):
    conn = db.connect(str(path))
    a = db.add_subscription(conn, "Tom & Jerry <TV>", 9900, "monthly", date(2026, 1, 5), "Kids", "ñ note")
    b = db.add_subscription(conn, "Gym", 64900, "monthly", date(2026, 1, 1), "Fitness")
    db.set_active(conn, b, False)
    db.mark_notified(conn, {a: date(2026, 10, 5)})
    db.set_meta(conn, "last_summary_month", "2026-10")
    return conn


def test_export_contains_every_column_and_meta(tmp_path):
    conn = make_db(tmp_path / "x.db")
    data = export_all(conn)
    assert [s["name"] for s in data["subscriptions"]] == ["Tom & Jerry <TV>", "Gym"]
    first = data["subscriptions"][0]
    assert list(first) == list(db.SUBSCRIPTION_COLUMNS)
    assert first["anchor_date"] == "2026-01-05" and first["last_notified_for"] == "2026-10-05"
    assert data["subscriptions"][1]["active"] == 0
    assert data["meta"] == {"last_summary_month": "2026-10"}


def test_backup_json_round_trips(tmp_path):
    conn = make_db(tmp_path / "x.db")
    payload = json.loads(backup_json(conn, NOW, "Local file: x.db"))
    assert payload["exported_at"] == "2026-10-04T07:47:00+02:00"
    assert payload["source"] == "Local file: x.db"
    assert payload["subscriptions"] == export_all(conn)["subscriptions"]


def test_backup_filename_is_sortable_and_windows_safe():
    name = backup_filename(NOW)
    assert name == "subscriptions-2026-10-04-074700.json"
    assert ":" not in name


def test_backup_script_writes_timestamped_file(monkeypatch, tmp_path, capsys):
    make_db(tmp_path / "x.db").close()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setattr("subtracker.config.PROJECT_DIR", tmp_path)
    monkeypatch.setattr("subtracker.config.sa_now", lambda: NOW)
    assert backup.main() == 0
    written = tmp_path / "backups" / "subscriptions-2026-10-04-074700.json"
    assert len(json.loads(written.read_text(encoding="utf-8"))["subscriptions"]) == 2
    assert capsys.readouterr().out.startswith("Database: local SQLite file")


def test_backups_folder_is_git_ignored():
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert "backups/" in gitignore.splitlines()


def test_manage_tab_has_download_backup_button(monkeypatch, tmp_path):
    make_db(tmp_path / "x.db").close()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    buttons = at.get("download_button")
    assert len(buttons) == 1
    assert buttons[0].proto.label == "Download backup"
