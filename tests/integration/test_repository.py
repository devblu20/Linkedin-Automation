"""SQLite repository integration tests."""

from dataclasses import replace
from pathlib import Path

from sqlalchemy import create_engine, text

from linkedin_automation.domain.enums import RunStatus
from linkedin_automation.domain.models import LeadCandidate, ResearchRun
from linkedin_automation.domain.services import process_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository


def test_persists_runs_and_deduplicates_leads(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite3"
    repository = SqliteResearchRepository(database)
    repository.initialize()
    definition = YamlSearchDefinitionLoader().load(Path("config/search.example.yaml"))
    run = ResearchRun.create(
        search_name=definition.name,
        schema_version=1,
        definition_json=definition.model_dump_json(),
        definition_hash="a" * 64,
        dry_run=False,
    )
    repository.add_run(run)
    paused = replace(
        run.transition(RunStatus.COLLECTING).transition(RunStatus.PAUSED), checkpoint_page=3
    )
    repository.save_run(paused)

    candidate = LeadCandidate(
        profile_url="https://linkedin.com/in/ada-example?trk=one",
        full_name="Ada Example",
        headline="AI Engineer building LLM systems",
        location="India",
    )
    first = process_candidate(candidate, definition, run.id)
    second = process_candidate(
        replace(candidate, profile_url="https://www.linkedin.com/in/ada-example/", company="Lab"),
        definition,
        run.id,
    )
    assert first is not None and second is not None
    assert repository.upsert_lead(first) is True
    assert repository.upsert_lead(second) is False

    reopened = SqliteResearchRepository(database)
    stored_run = reopened.get_run(run.id)
    leads = reopened.list_leads(run.id)
    assert stored_run is not None
    assert stored_run.status == RunStatus.PAUSED
    assert stored_run.checkpoint_page == 3
    assert len(leads) == 1
    assert leads[0].company == "Lab"
    inspection_engine = create_engine(f"sqlite:///{database.as_posix()}")
    with inspection_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
    inspection_engine.dispose()
    repository.close()
    reopened.close()
