"""Real temporary DBs exercise import atomicity, idempotency and review preservation."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.application.run_transfer import (
    MAX_TRANSFER_BYTES,
    RunTransfer,
    export_transfer,
    prepare_transfer,
)
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.run_sync import sync_transfer
from linkedin_automation.interfaces.hosted_site import create_hosted_app


def sample_payload() -> dict:
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    return {
        "version": 1,
        "run_id": str(uuid4()),
        "created_at": "2026-10-27T00:00:00Z",
        "definition": definition.model_dump(mode="json"),
        "profiles": [
            {
                "profile_url": "https://www.linkedin.com/in/synthetic-transfer-test/",
                "full_name": "Synthetic Test Founder",
                "headline": "Founder, family office",
                "current_title": "Founder",
                "company": "Synthetic Example Family Office",
                "location": "London",
                "collected_at": "2026-10-27T00:00:00Z",
            }
        ],
    }


@pytest.fixture
def hosted_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    for name in ["DATABASE_URL", "ARTIFACT_DIR", "BROWSER_DATA", "SCREENSHOT_DIR"]:
        monkeypatch.setenv(f"LINKEDIN_AUTOMATION_{name}", "unused-test-value")
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client


def test_import_retries_preserve_review_and_dry_run_is_read_only(hosted_client):
    payload = sample_payload()
    dry = hosted_client.post("/api/import/runs?dry_run=true", json=payload)
    assert dry.status_code == 200 and dry.json()["validated"] == 1
    assert hosted_client.get("/api/leads").json() == []
    first = hosted_client.post("/api/import/runs", json=payload)
    assert first.status_code == 200 and first.json()["inserted"] == 1
    item = hosted_client.get("/api/leads").json()[0]
    assert item["outreach"]["status"] == "needs_verification"
    update = {
        "firm_type": "family office",
        "funds_gbp": 1_000_000,
        "fund_evidence_url": "https://example.com/evidence",
        "fund_evidence_notes": "Reviewed",
        "connection_message": "Human edited draft",
        "follow_up_message": "Human follow-up",
        "status": "approved",
    }
    assert hosted_client.put(f"/api/leads/{item['lead']['id']}", json=update).status_code == 200
    again = hosted_client.post("/api/import/runs", json=payload)
    assert again.json()["inserted"] == 0 and again.json()["skipped"] == 1
    saved = hosted_client.get("/api/leads").json()
    assert len(saved) == 1
    assert saved[0]["outreach"]["connection_message"] == "Human edited draft"
    assert saved[0]["outreach"]["status"] == "approved"
    payload["definition"]["name"] = "conflicting-campaign"
    assert hosted_client.post("/api/import/runs", json=payload).status_code == 422
    assert len(hosted_client.get("/api/leads").json()) == 1


def test_invalid_batches_and_injected_status_do_not_write(hosted_client):
    payload = sample_payload()
    invalid = dict(payload["profiles"][0], profile_url="https://evil.example/profile")
    payload["profiles"].append(invalid)
    assert hosted_client.post("/api/import/runs", json=payload).status_code == 422
    assert hosted_client.get("/api/leads").json() == []
    payload["profiles"].pop()
    payload["profiles"][0]["status"] = "message_sent"
    assert hosted_client.post("/api/import/runs", json=payload).status_code == 422
    assert hosted_client.get("/api/leads").json() == []
    assert (
        hosted_client.post(
            "/api/import/runs",
            content=b" " * (MAX_TRANSFER_BYTES + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    hosted_client.auth = None
    assert hosted_client.post("/api/import/runs", json=sample_payload()).status_code == 401


def test_export_omits_local_paths_snapshots_and_outreach_state():
    payload = RunTransfer.model_validate(sample_payload())
    run, rows = prepare_transfer(payload)
    run = replace(run, report_path="C:/private/report.xlsx", drive_url="https://private.example")
    lead = replace(rows[0][0], notes="private reviewer notes", profile_snapshot="raw page snapshot")
    exported = export_transfer(run, [lead])
    serialized = exported.model_dump_json()
    for private in [
        "private reviewer notes",
        "private/report",
        "private.example",
        "raw page snapshot",
        "report_path",
        "message_sent",
    ]:
        assert private not in serialized
    assert prepare_transfer(exported)[0].definition_hash == run.definition_hash


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "https://user:pass@public.example",
        "https://public.example/other",
        "https://public.example?token=secret",
    ],
)
def test_sync_refuses_unsafe_destinations(url):
    with pytest.raises(ValueError, match="HTTPS origin"):
        sync_transfer(RunTransfer.model_validate(sample_payload()), url, "user", "password")
