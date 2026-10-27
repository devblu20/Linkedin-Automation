from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.application.research_transfer import (
    export_research_transfer,
    prepare_research_transfer,
)
from linkedin_automation.application.run_transfer import MAX_TRANSFER_BYTES, RunTransfer
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.interfaces.hosted_site import create_hosted_app


def payload():
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    base = {"collected_at": "2026-10-27T00:00:00Z"}
    return {
        "version": 1,
        "run_id": str(uuid4()),
        "created_at": "2026-10-27T00:00:00Z",
        "definition": definition.model_dump(mode="json"),
        "profiles": [
            {
                **base,
                "profile_url": "https://www.linkedin.com/in/test-owner/",
                "full_name": "Example Owner",
                "headline": "Owner, self-employed independent trader",
                "location": "London",
                "self_employed": True,
            },
            {
                **base,
                "profile_url": "https://www.linkedin.com/in/test-nonmatch/",
                "full_name": "Example Nonmatch",
                "headline": "CEO and co-founder of a software company",
                "location": "London",
            },
        ],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    with TestClient(create_hosted_app()) as http:
        http.auth = ("reviewer", "test-password-at-least-24-characters")
        yield http


def test_collected_rejections_sync_without_becoming_leads(client):
    data = payload()
    dry = client.post("/api/import/research?dry_run=true", json=data)
    assert dry.status_code == 200 and dry.json()["validated"] == 2
    assert client.get("/api/observations").json()["counts"]["read"] == 0
    response = client.post("/api/import/research", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["observed"] == 2
    assert response.json()["accepted"] == response.json()["rejected"] == 1
    collected = client.get("/api/observations").json()
    assert collected["counts"] == {"read": 2, "accepted": 1, "rejected": 1}
    rejected = client.get("/api/observations?decision=rejected").json()["items"]
    assert len(rejected) == 1
    assert rejected[0]["reasons"] == ["Missing required keywords"]
    assert "Company" in rejected[0]["missing_fields"]
    assert "location:London" in rejected[0]["matching_evidence"]
    assert "profile_snapshot" not in rejected[0]["profile"]
    assert len(client.get("/api/leads").json()) == 1
    assert client.get("/api/leads").json()[0]["outreach"]["status"] == "needs_verification"
    run = client.get("/api/research/runs").json()[0]
    assert run["read"] == 2 and run["rejected"] == 1
    first = client.get("/api/observations?limit=1").json()
    assert len(first["items"]) == 1 and first["has_more"]
    second = client.get("/api/observations?limit=1&offset=1").json()
    assert second["items"][0]["id"] != first["items"][0]["id"] and not second["has_more"]


def test_research_retry_is_idempotent_preserves_review_and_rejects_changed_data(client):
    data = payload()
    assert client.post("/api/import/research", json=data).status_code == 200
    item = client.get("/api/leads").json()[0]
    review = {
        "status": "approved",
        "firm_type": item["outreach"]["firm_type"],
        "funds_gbp": None,
        "fund_evidence_url": "",
        "fund_evidence_notes": "",
        "connection_message": "Human edited",
        "follow_up_message": "Human follow-up",
    }
    assert client.put("/api/leads/" + item["lead"]["id"], json=review).status_code == 200
    assert client.post("/api/import/research", json=data).status_code == 200
    assert client.get("/api/observations").json()["counts"]["read"] == 2
    changed = deepcopy(data)
    changed["profiles"][1]["full_name"] = "Changed on retry"
    new = deepcopy(data["profiles"][0])
    new["profile_url"] = "https://www.linkedin.com/in/new-atomic-test/"
    changed["profiles"].insert(0, new)
    assert client.post("/api/import/research", json=changed).status_code == 422
    assert client.get("/api/observations").json()["counts"]["read"] == 2
    assert len(client.get("/api/leads").json()) == 1
    assert client.get("/api/leads").json()[0]["outreach"]["connection_message"] == "Human edited"
    accepted_only = deepcopy(data)
    accepted_only["profiles"] = accepted_only["profiles"][:1]
    assert client.post("/api/import/runs", json=accepted_only).status_code == 200
    assert client.get("/api/observations").json()["counts"] == {
        "read": 2,
        "accepted": 1,
        "rejected": 1,
    }
    changed = deepcopy(data)
    changed["profiles"][1]["full_name"] = "Changed on retry"
    assert client.post("/api/import/research", json=changed).status_code == 422
    assert client.get("/api/observations").json()["counts"]["read"] == 2


def test_invalid_research_is_atomic_and_private_fields_are_rejected(client):
    data = payload()
    data["profiles"][1]["profile_url"] = "https://evil.example/in/example/"
    assert client.post("/api/import/research", json=data).status_code == 422
    assert client.get("/api/research/runs").json() == []
    assert client.get("/api/observations").json()["items"] == []
    for field in ["status", "profile_snapshot", "password"]:
        data = payload()
        data["profiles"][0][field] = "injected"
        assert client.post("/api/import/research", json=data).status_code == 422
    assert (
        client.post(
            "/api/import/research",
            content=b" " * (MAX_TRANSFER_BYTES + 1),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    client.auth = None
    for path in ["/api/observations", "/api/research/runs"]:
        assert client.get(path).status_code == 401
    assert client.post("/api/import/research", json=payload()).status_code == 401


def test_research_export_excludes_snapshots_paths_and_review_state():
    run, _, observations = prepare_research_transfer(RunTransfer.model_validate(payload()))
    run = replace(run, report_path="C:/private/report.xlsx")
    observations[0] = replace(
        observations[0],
        candidate=replace(observations[0].candidate, profile_snapshot="private raw browser HTML"),
    )
    exported = export_research_transfer(run, observations).model_dump_json()
    assert "C:/private" not in exported
    assert "private raw browser HTML" not in exported
    assert "profile_snapshot" not in exported
    assert '"accepted"' not in exported
    assert '"rejection_reason"' not in exported
