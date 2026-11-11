from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog
from linkedin_automation.infrastructure.search_worker import resolve_job_definition, validate_job
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.search_jobs import configured_campaigns


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client


def manual_payload(client):
    templates = client.get("/api/campaign-templates").json()
    spec = next(x["spec"] for x in templates if x["id"] == "bluqq-crm-recruitment.yaml")
    return {
        "name": "My manual CRM template",
        "spec": spec,
        "qualification": {
            "firm_types": ["recruitment agency"],
            "executive_title_priority": ["Founder", "Owner"],
            "minimum_score": 45,
            "require_fund_evidence": False,
            "minimum_funds_gbp": 1,
            "company_size_min": 1,
            "company_size_max": 500,
        },
        "connection_template": "Hi {first_name}, open to connecting?",
        "message_template": "Thanks {full_name}, can we discuss CRM at {company}?",
    }


def test_manual_template_persists_and_worker_trusts_separate_authenticated_lookup(client, tmp_path):
    templates_before = client.get("/api/campaign-templates").json()
    payload = manual_payload(client)
    response = client.post("/api/campaign-templates", json=payload)
    assert response.status_code == 201, response.text
    created = response.json()
    identifier = created["id"]
    assert identifier.startswith("custom-template-")
    catalog = CampaignCatalog(tmp_path / "campaigns.sqlite3", configured_campaigns(Path("config")))
    assert catalog.templates[identifier].qualification.company_size_max == 500
    after = client.get("/api/campaign-templates").json()
    assert [x for x in after if x["id"] != identifier] == templates_before
    saved = client.post("/api/campaigns", json={"spec": created["spec"]}).json()
    queued = client.post(
        "/api/search/jobs", json={"campaign": saved["id"], "max_results": 1, "max_pages": 1}
    )
    assert queued.status_code == 200, queued.text
    job = client.post("/api/search/claim", json={}).json()["job"]
    with pytest.raises(ValueError):
        validate_job(job, Path("config"))

    class Worker:
        def request(self, path, body):
            assert path == "/api/search/template-definition"
            assert body == {"id": identifier}
            result = client.post(path, json=body)
            assert result.status_code == 200
            return result.json()

    definition = resolve_job_definition(Worker(), job, Path("config"))
    assert definition.qualification.minimum_score == 45
    assert definition.qualification.require_fund_evidence is False
    assert definition.outreach.manual_send_only is True
    assert definition.outreach.message_template == payload["message_template"]
    tampered = deepcopy(job)
    tampered["definition"]["qualification"]["minimum_score"] = 0
    with pytest.raises(ValueError):
        resolve_job_definition(Worker(), tampered, Path("config"))
    isolated = CampaignCatalog(tmp_path / "other" / "campaigns.sqlite3", {})
    assert identifier not in isolated.templates


@pytest.mark.parametrize(
    "field,value",
    [
        ("connection_template", "{company.__class__}"),
        ("message_template", "{first_name!r}"),
        ("message_template", "{company:>100000}"),
        ("message_template", "{unknown}"),
        ("message_template", "{"),
        ("name", " " * 3),
        ("name", "X" * 101),
    ],
)
def test_manual_template_rejects_invalid_drafts_and_names(client, field, value):
    payload = manual_payload(client)
    payload[field] = value
    assert client.post("/api/campaign-templates", json=payload).status_code == 422


def test_policy_validation_authentication_and_limits(client):
    payload = manual_payload(client)
    payload["qualification"]["company_size_min"] = 600
    assert client.post("/api/campaign-templates", json=payload).status_code == 422
    payload = manual_payload(client)
    payload["qualification"]["firm_types"] = []
    assert client.post("/api/campaign-templates", json=payload).status_code == 422
    payload = manual_payload(client)
    payload["spec"]["daily_limit"] = 500
    assert client.post("/api/campaign-templates", json=payload).status_code == 422
    payload = manual_payload(client)
    payload["limits"] = {"max_daily_messages": 500}
    assert client.post("/api/campaign-templates", json=payload).status_code == 422
    payload = manual_payload(client)
    assert (
        client.post(
            "/api/campaign-templates", json=payload, headers={"Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    client.auth = None
    assert client.post("/api/campaign-templates", json=payload).status_code == 401
    assert (
        client.post("/api/search/template-definition", json={"id": "anything"}).status_code == 401
    )
