"""Local review-site integration tests with no external network calls."""

from pathlib import Path

from fastapi.testclient import TestClient

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import LeadCandidate, ResearchRun
from linkedin_automation.domain.services import create_outreach_record, process_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.interfaces.review_site import create_review_app


def test_reviews_evidence_and_connection_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "review.sqlite3"
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    run = ResearchRun.create(
        search_name=definition.name,
        schema_version=definition.version,
        definition_json=definition.model_dump_json(),
        definition_hash="c" * 64,
        dry_run=False,
    )
    lead = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/alex-founder",
            full_name="Alex Founder",
            headline="Founder of a family office",
            current_title="Founder",
            company="Example Capital",
            location="London",
        ),
        definition,
        run.id,
    )
    assert lead is not None
    repository = SqliteResearchRepository(database)
    repository.initialize()
    repository.add_run(run)
    repository.upsert_lead(lead)
    repository.upsert_outreach(create_outreach_record(lead, definition))
    repository.close()

    payload = {
        "firm_type": "family office",
        "funds_gbp": 1_000_000,
        "fund_evidence_url": "https://example.com/public-filing",
        "fund_evidence_notes": "Public filing",
        "connection_message": "BluQQ connection draft",
        "follow_up_message": "BluQQ follow-up draft",
        "status": OutreachStatus.APPROVED.value,
    }
    with TestClient(create_review_app(database, run.id)) as client:
        response = client.get("/api/leads")
        assert response.status_code == 200
        assert response.json()[0]["outreach"]["status"] == "needs_verification"

        approved = client.put(f"/api/leads/{lead.id}", json=payload)
        assert approved.status_code == 200
        assert approved.json()["score"] == 85

        payload["status"] = OutreachStatus.CONNECTION_SENT.value
        assert client.put(f"/api/leads/{lead.id}", json=payload).status_code == 200
        payload["status"] = OutreachStatus.CONNECTED.value
        connected = client.put(f"/api/leads/{lead.id}", json=payload)
        assert connected.status_code == 200
        assert connected.json()["status"] == "connected"

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "sending remains manual" in dashboard.text
        assert "Post-connection message" in dashboard.text
