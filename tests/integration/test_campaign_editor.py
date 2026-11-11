import sqlite3
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.application.campaigns import definition_hash, spec_from_template
from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog
from linkedin_automation.infrastructure.search_worker import validate_job
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.search_jobs import SearchJobs, configured_campaigns

TEMPLATE = "bluqq-london-independent-traders.yaml"


@pytest.fixture
def editor_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    with TestClient(create_hosted_app()) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client


def spec_payload():
    original = configured_campaigns(Path("config"))[TEMPLATE]
    spec = spec_from_template(TEMPLATE, original).model_dump(mode="json")
    spec.update(name="London independent traders", profile_limit=5, page_limit=3, daily_limit=25)
    return {"spec": spec, "expected_revision": None}


def test_crm_template_is_selectable_and_worker_keeps_its_non_trading_policy(editor_client):
    identifier = "bluqq-crm-recruitment.yaml"
    templates = editor_client.get("/api/campaign-templates").json()
    crm = next(x for x in templates if x["id"] == identifier)
    assert "recruitment" not in crm["spec"]["excluded_keywords"]
    assert "trading" not in crm["spec"]["keywords"]
    response = editor_client.post("/api/campaigns", json={"spec": crm["spec"]})
    assert response.status_code == 201, response.text
    saved = response.json()
    queued = editor_client.post(
        "/api/search/jobs", json={"campaign": saved["id"], "max_results": 1, "max_pages": 1}
    )
    assert queued.status_code == 200, queued.text
    job = editor_client.post("/api/search/claim", json={}).json()["job"]
    definition = validate_job(job, Path("config"))
    assert definition.qualification.require_fund_evidence is False
    assert "recruitment agency" in definition.qualification.firm_types
    assert "independent trader" not in definition.qualification.firm_types
    assert "customer relationships" in definition.outreach.message_template
    assert "trading" not in definition.outreach.message_template
    assert definition.outreach.manual_send_only is True
    assert definition.qualification.minimum_score == 45


def test_saved_campaign_template_cannot_silently_change(editor_client):
    saved = editor_client.post("/api/campaigns", json=spec_payload()).json()
    payload = spec_payload()
    payload["expected_revision"] = saved["revision"]
    payload["spec"]["template_id"] = "bluqq-crm-recruitment.yaml"
    changed = editor_client.put("/api/campaigns/" + saved["id"], json=payload)
    assert changed.status_code == 422
    assert "new campaign" in changed.json()["detail"]
    current = editor_client.get("/api/campaigns/" + saved["id"]).json()
    assert current["spec"]["template_id"] == TEMPLATE


def test_create_persist_edit_and_worker_uses_saved_rules(editor_client, tmp_path):
    before = Path("config", TEMPLATE).read_bytes()
    payload = spec_payload()
    created = editor_client.post("/api/campaigns", json=payload)
    assert created.status_code == 201, created.text
    campaign = created.json()
    identifier = campaign["id"]
    assert campaign["revision"] == 1
    persisted = CampaignCatalog(
        tmp_path / "campaigns.sqlite3", configured_campaigns(Path("config"))
    )
    assert persisted.get(identifier)["spec"].name == payload["spec"]["name"]
    assert any(
        x["id"] == identifier for x in editor_client.get("/api/search/settings").json()["campaigns"]
    )
    queued = editor_client.post(
        "/api/search/jobs", json={"campaign": identifier, "max_results": 1, "max_pages": 1}
    )
    assert queued.status_code == 200
    edited = deepcopy(payload)
    edited["expected_revision"] = 1
    edited["spec"]["keywords"] = ["options trading"]
    edited["spec"]["name"] = "London options traders"
    assert editor_client.put("/api/campaigns/" + identifier, json=edited).json()["revision"] == 2
    assert editor_client.put("/api/campaigns/" + identifier, json=edited).status_code == 409
    job = editor_client.post("/api/search/claim", json={}).json()["job"]
    definition = validate_job(job, Path("config"))
    assert definition.name == payload["spec"]["name"]
    assert definition.search.keywords.include == payload["spec"]["keywords"]
    template = configured_campaigns(Path("config"))[TEMPLATE]
    assert definition.qualification == template.qualification
    assert definition.outreach == template.outreach
    assert Path("config", TEMPLATE).read_bytes() == before
    assert editor_client.get("/api/campaigns/" + identifier).json()["spec"]["keywords"] == [
        "options trading"
    ]


def test_builtin_edit_is_overlay_and_worker_accepts_it(editor_client):
    payload = spec_payload()
    payload["expected_revision"] = 0
    payload["spec"]["name"] = "Edited London campaign"
    response = editor_client.put("/api/campaigns/" + TEMPLATE, json=payload)
    assert response.status_code == 200, response.text
    assert (
        editor_client.post(
            "/api/search/jobs", json={"campaign": TEMPLATE, "max_results": 1, "max_pages": 1}
        ).status_code
        == 200
    )
    job = editor_client.post("/api/search/claim", json={}).json()["job"]
    assert validate_job(job, Path("config")).name == "Edited London campaign"
    assert configured_campaigns(Path("config"))[TEMPLATE].name != "Edited London campaign"


@pytest.mark.parametrize(
    "field,value",
    [
        ("daily_limit", 500),
        ("profile_limit", 101),
        ("page_limit", 6),
        ("template_id", "../private.yaml"),
    ],
)
def test_campaign_limits_and_template_validation(editor_client, field, value):
    payload = spec_payload()
    payload["spec"][field] = value
    assert editor_client.post("/api/campaigns", json=payload).status_code == 422


def test_hundred_profile_campaign_reaches_worker_and_accepts_completion(editor_client):
    payload = spec_payload()
    payload["spec"].update(profile_limit=100, daily_limit=100, page_limit=5)
    response = editor_client.post("/api/campaigns", json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    option = next(
        item
        for item in editor_client.get("/api/search/settings").json()["campaigns"]
        if item["id"] == identifier
    )
    assert option["profile_limit"] == 100
    queued = editor_client.post(
        "/api/search/jobs", json={"campaign": identifier, "max_results": 100, "max_pages": 5}
    )
    assert queued.status_code == 200, queued.text
    job = editor_client.post("/api/search/claim", json={}).json()["job"]
    definition = validate_job(job, Path("config"))
    assert definition.limits.max_results == 100
    assert definition.limits.max_daily_results == 100
    template = configured_campaigns(Path("config"))[TEMPLATE]
    assert definition.limits.max_daily_connections == template.limits.max_daily_connections
    assert definition.limits.max_daily_messages == template.limits.max_daily_messages
    completed = editor_client.post(
        f"/api/search/jobs/{job['id']}/complete",
        json={
            "token": job["token"], "status": "completed", "observed": 100,
            "accepted": 20, "rejected": 80, "pages_visited": 5,
            "rejection_counts": {"Missing required keywords": 80},
        },
    )
    assert completed.status_code == 200, completed.text


def test_larger_request_still_respects_saved_campaign_limit(editor_client):
    saved = editor_client.post("/api/campaigns", json=spec_payload()).json()
    response = editor_client.post(
        "/api/search/jobs", json={"campaign": saved["id"], "max_results": 100, "max_pages": 3}
    )
    assert response.status_code == 409
    assert "exceeds this campaign" in response.json()["detail"]


def test_required_empty_field_and_policy_injection_are_rejected(editor_client):
    payload = spec_payload()
    payload["spec"]["locations"] = []
    assert editor_client.post("/api/campaigns", json=payload).status_code == 422
    payload = spec_payload()
    payload["spec"]["outreach"] = {"enabled": True}
    assert editor_client.post("/api/campaigns", json=payload).status_code == 422


def test_campaign_access_and_origin_boundaries(editor_client):
    assert (
        editor_client.post(
            "/api/campaigns", json=spec_payload(), headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    editor_client.auth = None
    assert editor_client.get("/api/campaign-templates").status_code == 401
    assert editor_client.get("/api/campaigns/" + TEMPLATE).status_code == 401
    assert editor_client.post("/api/campaigns", json=spec_payload()).status_code == 401


@pytest.mark.parametrize("tamper", ["qualification", "daily", "path"])
def test_worker_rejects_unsafe_saved_campaign_changes(editor_client, tamper):
    saved = editor_client.post("/api/campaigns", json=spec_payload()).json()
    editor_client.post(
        "/api/search/jobs", json={"campaign": saved["id"], "max_results": 1, "max_pages": 1}
    )
    job = editor_client.post("/api/search/claim", json={}).json()["job"]
    if tamper == "qualification":
        job["definition"]["qualification"]["minimum_score"] = 0
    elif tamper == "daily":
        job["policy"]["spec"]["daily_limit"] = 500
    else:
        job["definition"]["output"]["file_name"] = "../private.xlsx"
    with pytest.raises(ValueError):
        validate_job(job, Path("config"))


def test_job_schema_upgrade_preserves_legacy_jobs(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    template = configured_campaigns(Path("config"))[TEMPLATE]
    definition = template.model_copy(
        update={"limits": template.limits.model_copy(update={"max_results": 1, "max_pages": 1})}
    )
    identifier = str(uuid4())
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE search_jobs (id TEXT PRIMARY KEY,campaign TEXT,name TEXT,"
            "definition TEXT,base_hash TEXT,status TEXT,token TEXT,created REAL,"
            "started REAL,finished REAL,result TEXT)"
        )
        db.execute(
            "INSERT INTO search_jobs (id,campaign,name,definition,base_hash,status,created) "
            "VALUES (?,?,?,?,?,'queued',?)",
            (
                identifier,
                TEMPLATE,
                template.name,
                definition.model_dump_json(),
                definition_hash(template),
                time.time(),
            ),
        )
    jobs = SearchJobs(path)
    job = jobs.claim()
    assert job["id"] == identifier and job["policy"] is None
    assert validate_job(job, Path("config")).limits.max_results == 1
    SearchJobs(path)
