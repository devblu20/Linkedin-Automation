from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import LeadCandidate, ResearchRun
from linkedin_automation.domain.services import create_outreach_record, process_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.review_site import create_review_app


def seed(database, template="bluqq-london-prop-family-offices.yaml", complete=False):
    definition = YamlSearchDefinitionLoader().load(Path("config") / template)
    run = ResearchRun.create(
        search_name=definition.name,
        schema_version=2,
        definition_json=definition.model_dump_json(),
        definition_hash="b" * 64,
        dry_run=False,
    )
    candidate = LeadCandidate(
        profile_url="https://linkedin.com/in/review-example",
        full_name="Alex Example",
        headline="Founder of a family office; independent trader",
        current_title="Founder",
        company="Example Capital",
        location="London",
        company_size_min=1 if complete else None,
        company_size_max=5 if complete else None,
        self_employed=True if complete else None,
        profile_snapshot="private raw snapshot",
    )
    lead = process_candidate(candidate, definition, run.id)
    assert lead is not None
    record = create_outreach_record(lead, definition)
    repository = SqliteResearchRepository(database)
    repository.initialize()
    repository.add_run(run)
    repository.upsert_lead(lead)
    repository.upsert_outreach(record)
    repository.close()
    return lead, record, definition


@pytest.fixture
def hosted_review(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    lead, record, definition = seed(tmp_path / "research.sqlite3")
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client, lead, record, definition


def payload(record, **updates):
    result = {
        key: getattr(record, key)
        for key in (
            "firm_type",
            "funds_gbp",
            "fund_evidence_url",
            "fund_evidence_notes",
            "connection_message",
            "follow_up_message",
            "status",
        )
    }
    result.update(updates)
    return result


def test_assessment_preview_does_not_save_and_approval_is_evidence_gated(hosted_review):
    client, lead, record, definition = hosted_review
    endpoint = f"/api/leads/{lead.id}"
    before = client.get("/api/leads").json()
    assessment = client.get(endpoint + "/qualification").json()
    assert assessment["score"] == 55
    assert assessment["minimum_score"] == definition.qualification.minimum_score
    assert not assessment["can_approve"]
    assert any("minimum-funds" in b for b in assessment["blockers"])
    assert "private raw snapshot" not in str(assessment)
    assert "Company" not in assessment["missing_fields"]
    evidence = payload(
        record,
        funds_gbp=1_000_000,
        fund_evidence_url="https://example.com/public-filing",
        status="approved",
    )
    preview = client.post(endpoint + "/qualification", json=evidence).json()
    assert preview["score"] == 85 and preview["can_approve"]
    assert client.get("/api/leads").json() == before
    assert client.put(endpoint, json=payload(record, status="approved")).status_code == 422
    assert client.put(endpoint, json=evidence).json()["status"] == "approved"
    assert client.get("/api/summary").json()["qualified"] == 1
    assert client.get("/api/summary").json()["connection_sent"] == 0


@pytest.mark.parametrize(
    "status", ["connection_queued", "connection_sent", "connected", "message_ready", "message_sent"]
)
def test_hosted_review_cannot_invent_outreach_states(hosted_review, status):
    client, lead, record, _ = hosted_review
    response = client.put(f"/api/leads/{lead.id}", json=payload(record, status=status))
    assert response.status_code == 422
    assert client.get("/api/leads").json()[0]["outreach"]["status"] == "needs_verification"


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "not a URL", "https://user:pass@example.com"]
)
def test_invalid_funds_sources_cannot_approve(hosted_review, url):
    client, lead, record, _ = hosted_review
    evidence = payload(record, status="approved", funds_gbp=1_000_000, fund_evidence_url=url)
    preview = client.post(f"/api/leads/{lead.id}/qualification", json=evidence).json()
    assert not preview["can_approve"]
    assert client.put(f"/api/leads/{lead.id}", json=evidence).status_code == 422


def test_unknown_firm_type_cannot_score_or_approve(hosted_review):
    client, lead, record, _ = hosted_review
    evidence = payload(
        record,
        status="approved",
        firm_type="Anything",
        funds_gbp=1_000_000,
        fund_evidence_url="https://example.com/evidence",
    )
    preview = client.post(f"/api/leads/{lead.id}/qualification", json=evidence).json()
    assert next(p for p in preview["components"] if p["label"] == "Firm type")["points"] == 0
    assert client.put(f"/api/leads/{lead.id}", json=evidence).status_code == 422


def test_repeat_saves_recompute_profile_score_without_drift(tmp_path):
    database = tmp_path / "review.sqlite3"
    lead, record, _ = seed(database, "bluqq-london-independent-traders.yaml", complete=True)
    assert record.score == 100
    with TestClient(create_review_app(database, review_only=True)) as client:
        for _ in range(3):
            result = client.put(
                f"/api/leads/{lead.id}", json=payload(record, status="ready_for_review")
            )
            assert result.status_code == 200, result.text
            assert result.json()["score"] == 100
        assert client.get("/api/summary").json()["qualified"] == 0
        assert (
            client.put(f"/api/leads/{lead.id}", json=payload(record, status="approved")).status_code
            == 200
        )
        assert client.get("/api/summary").json()["qualified"] == 1


def test_funds_policy_blocks_automatic_readiness_even_at_high_score(tmp_path):
    _, record, _ = seed(tmp_path / "high-score.sqlite3", complete=True)
    assert record.score >= 70
    assert record.status == OutreachStatus.NEEDS_VERIFICATION


def test_review_scope_and_missing_lead_are_enforced(tmp_path):
    database = tmp_path / "scoped.sqlite3"
    lead, record, _ = seed(database)
    with TestClient(create_review_app(database, uuid4())) as client:
        assert client.get(f"/api/leads/{lead.id}/qualification").status_code == 404
        assert client.put(f"/api/leads/{lead.id}", json=payload(record)).status_code == 404
        assert client.get(f"/api/leads/{uuid4()}/qualification").status_code == 404


def test_preview_requires_authentication_and_same_origin(hosted_review):
    client, lead, record, _ = hosted_review
    path = f"/api/leads/{lead.id}/qualification"
    assert (
        client.post(
            path, json=payload(record), headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    client.auth = None
    assert client.get(path).status_code == 401
    assert client.post(path, json=payload(record)).status_code == 401
