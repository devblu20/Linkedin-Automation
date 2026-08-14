"""CLI behavior tests with no external service access."""

from pathlib import Path

from typer.testing import CliRunner

from linkedin_automation.interfaces.cli.app import app

runner = CliRunner()


def test_validate_command_succeeds_for_example() -> None:
    result = runner.invoke(app, ["validate", "config/search.example.yaml"])
    assert result.exit_code == 0
    assert "Valid search definition: india-ai-engineering-leads" in result.stdout
    assert "Schema version: 1" in result.stdout
    assert "Limits: 100 results, 10 pages" in result.stdout
    assert "Daily limit: 250 results" in result.stdout


def test_validate_command_reports_configuration_error(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text("version: 2\n", encoding="utf-8")

    result = runner.invoke(app, ["validate", str(invalid_path)])

    assert result.exit_code == 2
    assert "Invalid search definition" in result.stderr
    assert "Traceback" not in result.stderr


def test_help_exposes_complete_workflow_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("validate", "run", "resume", "status", "export", "upload"):
        assert command in result.stdout


def test_dry_run_completes_without_browser_or_report(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run", "config/search.example.yaml", "--dry-run"],
        env={
            "LINKEDIN_AUTOMATION_DATABASE": str(tmp_path / "research.sqlite3"),
            "LINKEDIN_AUTOMATION_ARTIFACT_DIR": str(tmp_path / "artifacts"),
            "LINKEDIN_AUTOMATION_BROWSER_DATA": str(tmp_path / "browser"),
            "LINKEDIN_AUTOMATION_SCREENSHOT_DIR": str(tmp_path / "screenshots"),
        },
    )
    assert result.exit_code == 0
    assert "Status: completed" in result.stdout
    assert "Profiles observed: 0" in result.stdout
    assert "Qualified leads: 0" in result.stdout
    assert not (tmp_path / "artifacts").exists()
