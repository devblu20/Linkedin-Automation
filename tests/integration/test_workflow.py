"""Offline end-to-end workflow using real persistence and fake external adapters."""

from pathlib import Path
from uuid import UUID

import pytest

from linkedin_automation.application.exceptions import CollectionLimitError
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.domain.enums import RunStatus
from linkedin_automation.domain.models import (
    CollectionBatch,
    Lead,
    LeadCandidate,
    OutreachRecord,
    ResearchRun,
    StoredArtifact,
)
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository


class FakeCollector:
    def collect(
        self, definition: SearchDefinition, *, start_page: int = 1, dry_run: bool = False
    ) -> CollectionBatch:
        if dry_run:
            return CollectionBatch((), start_page - 1, True)
        return CollectionBatch(
            (
                LeadCandidate(
                    profile_url="https://linkedin.com/in/ada-example?trk=one",
                    full_name="Ada Example",
                    headline="AI Engineer building LLM systems",
                    location="India",
                ),
                LeadCandidate(
                    profile_url="https://www.linkedin.com/in/ada-example/",
                    full_name="Ada Example",
                    headline="AI Engineer building LLM systems",
                    company="Example Labs",
                    location="India",
                ),
                LeadCandidate(
                    profile_url="https://linkedin.com/in/intern-example",
                    full_name="Intern Example",
                    headline="AI Engineer Intern",
                    location="India",
                ),
            ),
            checkpoint_page=start_page,
            complete=True,
        )


class FakeReporter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.statuses: list[RunStatus] = []

    def write(
        self,
        run: ResearchRun,
        leads: list[Lead],
        outreach: list[OutreachRecord],
        file_name: str,
    ) -> Path:
        self.statuses.append(run.status)
        assert not outreach
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / file_name
        path.write_bytes(f"run={run.id};leads={len(leads)}".encode())
        return path


class FakeStorage:
    def __init__(self) -> None:
        self.uploads = 0

    def upload(self, path: Path, *, folder_id: str, idempotency_key: str) -> StoredArtifact:
        assert path.is_file()
        assert folder_id == "test-folder"
        self.uploads += 1
        return StoredArtifact("drive-id", "https://drive/drive-id")


def test_runs_complete_offline_pipeline_idempotently(tmp_path: Path, monkeypatch: object) -> None:
    repository = SqliteResearchRepository(tmp_path / "research.sqlite3")
    storage = FakeStorage()
    reporter = FakeReporter(tmp_path / "artifacts")
    workflow = ResearchWorkflow(
        loader=YamlSearchDefinitionLoader(),
        repository=repository,
        collector=FakeCollector(),
        reporter=reporter,
        storage=storage,
    )
    # pytest's monkeypatch fixture is intentionally typed as object for no runtime dependency.
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "test-folder")  # type: ignore[attr-defined]

    result = workflow.start(Path("config/search.example.yaml"), dry_run=False, upload=True)
    repeated = workflow.upload(result.run.id)

    assert result.run.status == RunStatus.COMPLETED
    assert result.lead_count == 1
    assert result.run.counters.observed == 3
    assert result.run.counters.accepted == 1
    assert result.run.counters.duplicated == 1
    assert result.run.counters.rejected == 1
    assert result.run.drive_file_id == "drive-id"
    assert repeated.drive_file_id == "drive-id"
    assert storage.uploads == 1
    assert reporter.statuses == [RunStatus.COMPLETED]
    workflow.close()


def test_enforces_daily_limit_across_runs(tmp_path: Path) -> None:
    search_file = tmp_path / "daily.yaml"
    search_file.write_text(
        """version: 1
name: daily-limit
search:
  titles:
    include: [AI Engineer]
limits:
  max_results: 10
  max_pages: 1
  max_daily_results: 1
output:
  file_name: daily.xlsx
  drive_folder_id_env: GOOGLE_DRIVE_FOLDER_ID
""",
        encoding="utf-8",
    )
    workflow = ResearchWorkflow(
        loader=YamlSearchDefinitionLoader(),
        repository=SqliteResearchRepository(tmp_path / "daily.sqlite3"),
        collector=FakeCollector(),
        reporter=FakeReporter(tmp_path / "reports"),
    )
    first = workflow.start(search_file, dry_run=False, upload=False)
    assert first.run.counters.observed == 1
    with pytest.raises(CollectionLimitError, match="daily collection limit") as caught:
        workflow.start(search_file, dry_run=False, upload=False)
    assert caught.value.run_id is not None
    paused = workflow.get_run(UUID(caught.value.run_id))
    assert paused.status == RunStatus.PAUSED
    workflow.close()
