"""Enrichment must preserve original observations, reviewed data and sending decisions."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from linkedin_automation.application.public_prospect_review import prepare_public_collection
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository


def test_only_missing_unreviewed_public_fields_are_filled(tmp_path):
    definition = YamlSearchDefinitionLoader().load(Path("config/bluqq-crm-recruitment.yaml"))
    item = {
        "name": "Public Person",
        "profile_url": "https://www.linkedin.com/in/public-one/",
        "public_title": "Public Person - Founder at Example recruitment agency",
        "snippet": "Location: London · Skills: CRM, Python · Education: Example University",
        "matched": ["title:Founder", "location:London", "keyword:recruitment"],
        "missing": [],
        "query": "recruitment London",
    }
    run, rows, observations = prepare_public_collection(
        {"id": str(uuid4()), "created": 1700000000, "results": [item]}, definition
    )
    fresh, record = rows[0]
    old = replace(
        fresh,
        current_title="",
        company="",
        location="London. View Public Person profile on LinkedIn, a professional community",
        skills=(),
        education=(),
    )
    original_observation = replace(
        observations[0],
        candidate=replace(
            observations[0].candidate,
            current_title="",
            company="",
            location="",
            skills=(),
            education=(),
        ),
    )
    repository = DatabaseResearchRepository(tmp_path / "research.sqlite3")
    repository.initialize()
    try:
        original_record = replace(record, status=OutreachStatus.NEEDS_VERIFICATION, score=0)
        repository.import_review_run(
            run, [(old, original_record)], observations=[original_observation]
        )
        before = repository.list_observations(run.id)
        assert repository.enrich_public_review_missing(rows) == 1
        updated = repository.get_lead(old.id)
        assert updated.location == "London" and updated.skills == ("CRM", "Python")
        assert repository.list_observations(run.id) == before
        current = repository.get_outreach(old.id)
        assert current.status == OutreachStatus.NEEDS_VERIFICATION
        assert current.score == record.score > original_record.score
        assert current.connection_message == record.connection_message
        assert repository.enrich_public_review_missing(rows) == 0
        repository.upsert_outreach(replace(current, status=OutreachStatus.APPROVED), manual=True)
        assert (
            repository.enrich_public_review_missing(
                [(replace(fresh, about="New public text"), record)]
            )
            == 0
        )
        assert repository.get_lead(old.id).about == ""
        assert repository.get_outreach(old.id).status == OutreachStatus.APPROVED
    finally:
        repository.close()
