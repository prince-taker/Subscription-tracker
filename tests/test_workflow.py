"""Guard the safety properties of the GitHub Actions workflow.

Plain text checks (no PyYAML: it isn't a project dependency).
"""

from pathlib import Path

WORKFLOW = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "reminder.yml").read_text()


def test_schedule_and_manual_runs_default_to_dry_run():
    assert '- cron: "47 5 * * *"' in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    dry_run_block = WORKFLOW.split("dry_run:", 1)[1].split("permissions:", 1)[0]
    assert "type: boolean" in dry_run_block and "default: true" in dry_run_block


def test_kill_switch_guards_scheduled_runs():
    assert ("if: github.event_name == 'workflow_dispatch' || vars.REMINDER_ENABLED == 'true'"
            in WORKFLOW)


def test_least_privilege_no_overlap_and_timeout():
    assert "permissions:\n  contents: read\n" in WORKFLOW
    assert "concurrency:\n  group: reminder\n  cancel-in-progress: false\n" in WORKFLOW
    assert "timeout-minutes: 10" in WORKFLOW


def test_secrets_come_from_github_secrets_and_are_never_echoed():
    for var in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        assert f"{var}: ${{{{ secrets.{var} }}}}" in WORKFLOW
    assert "python reminder.py --dry-run" in WORKFLOW
    assert "echo" not in WORKFLOW
    assert "pip install -r requirements.txt" in WORKFLOW
