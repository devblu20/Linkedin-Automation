"""Typer command-line interface for Phase 1 configuration validation."""

import logging
from contextlib import closing
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import UUID

import typer

from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.application.exceptions import StorageError, WorkflowError
from linkedin_automation.application.validate_search import ValidateSearchDefinition
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInCollector
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.runtime import RuntimeSettings
from linkedin_automation.infrastructure.storage import GoogleDriveStorage
from linkedin_automation.observability.logging import configure_logging

app = typer.Typer(help="Validate and run supervised LinkedIn lead research workflows.")
logger = logging.getLogger(__name__)


@app.callback()
def cli() -> None:
    """Validate and run supervised LinkedIn lead research workflows."""


@app.command()
def validate(
    search_file: Annotated[
        Path,
        typer.Argument(help="Path to a versioned YAML search definition."),
    ],
) -> None:
    """Validate and summarize a lead-search definition."""
    use_case = ValidateSearchDefinition(YamlSearchDefinitionLoader())
    try:
        summary = use_case.execute(search_file)
    except ConfigurationError as error:
        logger.warning("search_definition_invalid", extra={"search_file": str(search_file)})
        typer.echo(f"Invalid search definition: {error}", err=True)
        raise typer.Exit(code=2) from error

    logger.info("search_definition_valid", extra={"search_file": str(search_file)})
    typer.echo(f"Valid search definition: {summary.name}")
    typer.echo(f"Schema version: {summary.version}")
    typer.echo(
        f"Criteria: {summary.positive_criteria} positive, {summary.excluded_criteria} excluded"
    )
    typer.echo(f"Limits: {summary.max_results} results, {summary.max_pages} pages")
    typer.echo(f"Daily limit: {summary.max_daily_results} results")


def _workflow(*, with_storage: bool = False) -> ResearchWorkflow:
    settings = RuntimeSettings.from_environment()
    storage = None
    if with_storage:
        if settings.google_credentials_path is None or settings.google_token_path is None:
            raise StorageError(
                "GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE must be configured"
            )
        storage = GoogleDriveStorage.from_oauth(
            settings.google_credentials_path, settings.google_token_path
        )
    return ResearchWorkflow(
        loader=YamlSearchDefinitionLoader(),
        repository=DatabaseResearchRepository(settings.database_url),
        collector=PlaywrightLinkedInCollector(
            browser_data_dir=settings.browser_data_dir,
            screenshot_dir=settings.screenshot_dir,
        ),
        reporter=ExcelReportWriter(settings.artifact_dir),
        storage=storage,
    )


def _fail(error: Exception) -> NoReturn:
    logger.warning("workflow_failed", extra={"error_type": error.__class__.__name__})
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=2) from error


@app.command()
def run(
    search_file: Annotated[Path, typer.Argument(help="Validated YAML search definition.")],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate orchestration without collection or output.")
    ] = False,
    upload: Annotated[
        bool, typer.Option("--upload", help="Upload the generated report to Google Drive.")
    ] = False,
) -> None:
    """Start a new supervised research run."""
    if not dry_run:
        typer.echo(
            "Opening the supervised LinkedIn browser. Sign in if prompted; "
            "the run will continue automatically."
        )
    try:
        with closing(_workflow(with_storage=upload)) as workflow:
            result = workflow.start(search_file, dry_run=dry_run, upload=upload)
    except (ConfigurationError, WorkflowError, ValueError, OSError) as error:
        _fail(error)
    typer.echo(f"Run ID: {result.run.id}")
    typer.echo(f"Status: {result.run.status.value}")
    typer.echo(f"Profiles observed: {result.run.counters.observed}")
    typer.echo(f"Qualified leads: {result.lead_count}")
    if result.run.report_path:
        typer.echo(f"Report: {result.run.report_path}")
    if result.run.drive_url:
        typer.echo(f"Drive: {result.run.drive_url}")


@app.command()
def resume(
    run_id: Annotated[UUID, typer.Argument(help="Research run UUID.")],
    upload: Annotated[bool, typer.Option("--upload")] = False,
) -> None:
    """Resume a paused or failed run from its last page checkpoint."""
    try:
        with closing(_workflow(with_storage=upload)) as workflow:
            result = workflow.resume(run_id, upload=upload)
    except (ConfigurationError, WorkflowError, ValueError, OSError) as error:
        _fail(error)
    typer.echo(f"Run ID: {result.run.id}")
    typer.echo(f"Status: {result.run.status.value}")
    typer.echo(f"Profiles observed: {result.run.counters.observed}")
    typer.echo(f"Qualified leads: {result.lead_count}")


@app.command()
def status(run_id: Annotated[UUID, typer.Argument(help="Research run UUID.")]) -> None:
    """Show durable state and counters for a run."""
    try:
        with closing(_workflow()) as workflow:
            research_run = workflow.get_run(run_id)
    except (WorkflowError, ValueError, OSError) as error:
        _fail(error)
    typer.echo(f"Run ID: {research_run.id}")
    typer.echo(f"Search: {research_run.search_name}")
    typer.echo(f"Status: {research_run.status.value}")
    typer.echo(f"Checkpoint page: {research_run.checkpoint_page}")
    typer.echo(
        "Counts: "
        f"{research_run.counters.observed} observed, "
        f"{research_run.counters.accepted} accepted, "
        f"{research_run.counters.rejected} rejected, "
        f"{research_run.counters.duplicated} duplicated"
    )
    if research_run.error_message:
        typer.echo(f"Last error: {research_run.error_message}")


@app.command("export")
def export_report(run_id: Annotated[UUID, typer.Argument(help="Research run UUID.")]) -> None:
    """Regenerate the Excel report for a stored run."""
    try:
        with closing(_workflow()) as workflow:
            path = workflow.export(run_id)
    except (WorkflowError, ValueError, OSError) as error:
        _fail(error)
    typer.echo(f"Report: {path}")


@app.command()
def upload(run_id: Annotated[UUID, typer.Argument(help="Research run UUID.")]) -> None:
    """Upload a stored run report to Google Drive idempotently."""
    try:
        with closing(_workflow(with_storage=True)) as workflow:
            research_run = workflow.upload(run_id)
    except (WorkflowError, ValueError, OSError) as error:
        _fail(error)
    typer.echo(f"Drive: {research_run.drive_url}")


@app.command("drive-auth")
def drive_auth(
    expected_email: Annotated[
        str,
        typer.Option(help="Google account that must authorize Drive uploads."),
    ] = "marketingcodex77@gmail.com",
) -> None:
    """Authorize Google Drive and verify the selected Google account."""
    settings = RuntimeSettings.from_environment()
    if settings.google_credentials_path is None or settings.google_token_path is None:
        _fail(
            StorageError("GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE must be configured")
        )
    try:
        storage = GoogleDriveStorage.from_oauth(
            settings.google_credentials_path, settings.google_token_path
        )
        authorized = storage.authorized_email()
    except (WorkflowError, ValueError, OSError) as error:
        _fail(error)
    if authorized.casefold() != expected_email.strip().casefold():
        _fail(
            StorageError(
                f"Drive authorized as {authorized}; expected {expected_email}. "
                "Remove the external OAuth token and authorize again with the intended account."
            )
        )
    typer.echo(f"Google Drive authorized as: {authorized}")


@app.command()
def review(
    run_id: Annotated[UUID, typer.Argument(help="Research run UUID to review.")],
    port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765,
) -> None:
    """Start the local qualification and outreach-review site."""
    import uvicorn

    from linkedin_automation.interfaces.review_site import create_review_app

    settings = RuntimeSettings.from_environment()
    typer.echo(f"Review site: http://127.0.0.1:{port}")
    uvicorn.run(create_review_app(settings.database_url, run_id), host="127.0.0.1", port=port)


def main() -> None:
    """Configure process logging and run the CLI."""
    configure_logging()
    app()
