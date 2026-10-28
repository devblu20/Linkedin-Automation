from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.application.exceptions import LimitedVisibilityError
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


def test_completion_retry_requires_identical_result_and_original_token(tmp_path):
    jobs = SearchJobs(tmp_path / "jobs.sqlite3")
    jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    job = jobs.claim()
    result = SearchCompletion(token=job["token"], status="completed", message="Finished")
    jobs.complete(UUID(job["id"]), result)
    before = jobs.status()
    SearchJobs(jobs.path).complete(UUID(job["id"]), result)
    assert jobs.status() == before
    for changed in [
        result.model_copy(update={"message": "Changed"}),
        result.model_copy(update={"token": "0" * 48}),
    ]:
        with pytest.raises(ValueError):
            jobs.complete(UUID(job["id"]), changed)
    with jobs.connection() as db:
        row = db.execute("SELECT token,completion_hash FROM search_jobs").fetchone()
        assert row["token"] is None
        assert job["token"] not in row["completion_hash"]


def test_resume_uses_original_rules_checkpoint_and_budget(tmp_path):
    jobs = SearchJobs(tmp_path / "jobs.sqlite3")
    jobs.enqueue(search_payload(), configured_campaigns(Path("config")))
    first = jobs.claim()
    jobs.complete(
        UUID(first["id"]),
        SearchCompletion(token=first["token"], status="failed", resume_available=True),
    )
    queued = jobs.resume(UUID(first["id"]))
    with pytest.raises(ValueError):
        jobs.resume(UUID(first["id"]))
    resumed = jobs.claim()
    assert resumed["id"] == queued["id"] != first["id"]
    assert resumed["checkpoint_id"] == first["id"]
    assert resumed["definition"] == first["definition"]
    assert resumed["token"] != first["token"]
    jobs.complete(UUID(resumed["id"]), SearchCompletion(token=resumed["token"], status="completed"))
    with pytest.raises(ValueError):
        jobs.resume(UUID(resumed["id"]))
    with pytest.raises(ValueError):
        jobs.resume(UUID(first["id"]))


@pytest.mark.parametrize("outcome", ["matched", "rejected", "empty", "restricted"])
@pytest.mark.parametrize("lost_upload_response", [False, True])
def test_worker_claim_search_save_sync_and_status(
    tmp_path, monkeypatch, outcome, lost_upload_response
):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path / "cloud"))
    app = create_hosted_app()
    monkeypatch.setenv(
        "LINKEDIN_AUTOMATION_DATABASE_URL", f"sqlite:///{(tmp_path / 'local.sqlite3').as_posix()}"
    )
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "reports"))

    collections, uploads = [], []

    class Collector:
        def __init__(self, **kwargs):
            pass

        def collect(self, definition, **kwargs):
            collections.append(True)
            assert definition.limits.max_results == 1
            if outcome == "restricted":
                raise LimitedVisibilityError(
                    "Restricted profile", pages_visited=1, hidden_result_cards=10
                )
            if outcome == "empty":
                return CollectionBatch((), checkpoint_page=1, complete=True)
            candidate = LeadCandidate(
                profile_url="https://www.linkedin.com/in/example/",
                full_name="Example Founder",
                headline="Student" if outcome == "rejected" else "Founder of a family office",
                location="Nigeria" if outcome == "rejected" else "London",
            )
            return CollectionBatch(
                (candidate,),
                checkpoint_page=1,
                complete=True,
                pages_visited=1,
                hidden_result_cards=9,
            )

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

        def sync(payload, *args, **kwargs):
            endpoint = (
                "/api/import/research" if kwargs.get("include_observations") else "/api/import/runs"
            )
            response = http.post(endpoint, json=payload.model_dump(mode="json"))
            assert response.status_code == 200, response.text
            uploads.append(True)
            if lost_upload_response and len(uploads) == 1:
                raise ValueError("Upload response lost after commit")
            return response.json()

        client.request = request
        monkeypatch.setattr("linkedin_automation.infrastructure.search_worker.sync_transfer", sync)
        if lost_upload_response and outcome in {"matched", "rejected"}:
            with pytest.raises(ValueError):
                run_worker(client, Path("config"), once=True)
        run_worker(client, Path("config"), once=True)
        assert len(collections) == 1
        state = http.get("/api/search/settings").json()
        assert state["jobs"][0]["status"] == ("failed" if outcome == "restricted" else "completed")
        assert state["jobs"][0]["result"]["accepted"] == int(outcome == "matched")
        assert state["jobs"][0]["requested"] == {"profiles": 1, "pages": 1}
        leads = http.get("/api/leads").json()
        observations = http.get("/api/observations").json()["items"]
        assert len(observations) == int(outcome in {"matched", "rejected"})
        if outcome == "rejected":
            assert observations[0]["decision"] == "rejected"
        if outcome == "matched":
            assert state["jobs"][0]["result"]["pages_visited"] == 1
            assert state["jobs"][0]["result"]["hidden_result_cards"] == 9
            assert leads[0]["lead"]["full_name"] == "Example Founder"
            assert leads[0]["outreach"]["status"] == "needs_verification"
        else:
            assert leads == []
            if outcome == "restricted":
                assert state["jobs"][0]["result"]["hidden_result_cards"] == 10


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
