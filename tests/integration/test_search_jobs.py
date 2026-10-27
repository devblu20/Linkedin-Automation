from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.domain.models import CollectionBatch, LeadCandidate
from linkedin_automation.infrastructure.search_worker import (
    DashboardClient,
    run_worker,
    validate_job,
)
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.search_jobs import (
    SearchCompletion,
    SearchJobs,
    SearchRequest,
    configured_campaigns,
)


def search_payload():
    return SearchRequest(
        campaign="bluqq-london-prop-family-offices.yaml", max_results=1, max_pages=1
    )


def test_jobs_persist_claim_once_and_require_token(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    jobs = SearchJobs(path)
    created = jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    with pytest.raises(ValueError, match="already"):
        jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    jobs = SearchJobs(path)
    job = jobs.claim()
    assert job["id"] == created["id"]
    assert jobs.claim() is None
    assert "token" not in jobs.status()["jobs"][0]
    assert validate_job(job, Path("config")).limits.max_results == 1
    with pytest.raises(ValueError, match="token"):
        jobs.complete(UUID(job["id"]), SearchCompletion(token="0" * 48, status="completed"))
    with pytest.raises(ValueError, match="counters"):
        jobs.complete(
            UUID(job["id"]), SearchCompletion(token=job["token"], status="completed", observed=2)
        )
    jobs.complete(UUID(job["id"]), SearchCompletion(token=job["token"], status="completed"))
    assert SearchJobs(path).status()["jobs"][0]["status"] == "completed"


@pytest.mark.parametrize("change", ["campaign", "definition", "daily", "count"])
def test_worker_rejects_changed_campaign_and_limits(tmp_path, change):
    jobs = SearchJobs(tmp_path / "jobs.sqlite3")
    jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    job = deepcopy(jobs.claim())
    if change == "campaign":
        job["campaign"] = "../outside.yaml"
    elif change == "definition":
        job["definition"]["search"]["keywords"]["include"] = ["unapproved"]
    elif change == "daily":
        job["definition"]["limits"]["max_daily_results"] = 1
    else:
        job["definition"]["limits"]["max_results"] = 26
    with pytest.raises(ValueError):
        validate_job(job, Path("config"))


def test_expired_jobs_fail_without_reexecution(tmp_path):
    jobs = SearchJobs(tmp_path / "jobs.sqlite3")
    jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    with jobs.connection() as db:
        db.execute("UPDATE search_jobs SET created=0")
    assert jobs.claim() is None
    assert jobs.status()["jobs"][0]["status"] == "failed"


def test_worker_claim_search_save_sync_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path / "cloud"))
    app = create_hosted_app()
    monkeypatch.setenv(
        "LINKEDIN_AUTOMATION_DATABASE_URL", f"sqlite:///{(tmp_path / 'local.sqlite3').as_posix()}"
    )
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "reports"))

    class Collector:
        def __init__(self, **kwargs):
            pass

        def collect(self, definition, **kwargs):
            assert definition.limits.max_results == 1
            candidate = LeadCandidate(
                profile_url="https://www.linkedin.com/in/example/",
                full_name="Example Founder",
                headline="Founder of a family office",
                location="London",
            )
            return CollectionBatch((candidate,), checkpoint_page=1, complete=True)

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.search_worker.PlaywrightLinkedInCollector", Collector
    )
    with TestClient(app) as http:
        http.auth = ("reviewer", "test-password-at-least-24-characters")
        assert 'id="searchForm"' in http.get("/").text
        assert 'id="collectUrl"' not in http.get("/").text
        response = http.post("/api/search/jobs", json=search_payload().model_dump())
        assert response.status_code == 200
        client = DashboardClient("http://127.0.0.1", "reviewer", "test")

        def request(path, payload=None):
            response = http.post(path, json=payload or {})
            assert response.status_code == 200, response.text
            return response.json()

        def sync(payload, *args):
            response = http.post("/api/import/runs", json=payload.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            return response.json()

        client.request = request
        monkeypatch.setattr("linkedin_automation.infrastructure.search_worker.sync_transfer", sync)
        run_worker(client, Path("config"), once=True)
        state = http.get("/api/search/settings").json()
        assert state["jobs"][0]["status"] == "completed"
        assert state["jobs"][0]["result"]["accepted"] == 1
        leads = http.get("/api/leads").json()
        assert leads[0]["lead"]["full_name"] == "Example Founder"
        assert leads[0]["outreach"]["status"] == "needs_verification"


@pytest.mark.parametrize(
    "url",
    [
        "https://other:secret@example.com",
        "http://example.com",
        "https://example.com/path",
        "https://example.com?token=1",
    ],
)
def test_worker_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        DashboardClient(url, "reviewer", "secret")
