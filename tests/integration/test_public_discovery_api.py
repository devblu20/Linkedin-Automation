from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.interfaces.hosted_site import create_hosted_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "never-expose-this-test-key")
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "0")
    monkeypatch.setenv("PERSONAL_BROWSER_MODE", "0")
    app = create_hosted_app(data_root=tmp_path, access_credentials=("owner", "x" * 30))
    calls = []

    def search(query, offset):
        calls.append((query, offset))
        return [
            {
                "url": f"https://www.linkedin.com/in/person-{len(calls)}-{number}/",
                "title": f"Person {number} - Recruitment agency owner | LinkedIn",
                "description": "Recruitment agency in London",
            }
            for number in range(20)
        ]

    app.state.public_discovery.provider_factory = lambda: SimpleNamespace(search=search)

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("linkedin_automation.interfaces.public_discovery.Thread", ImmediateThread)
    return app, calls


def test_authenticated_preview_for_any_existing_campaign_without_worker_or_outreach(setup):
    app, calls = setup
    with TestClient(app) as client:
        assert client.get("/api/public-discovery/settings").status_code == 401
        client.auth = ("owner", "x" * 30)
        before = client.get("/api/leads").json()
        search_before = client.get("/api/search/settings").json()["jobs"]
        body = client.get("/").text
        assert "Public-web prospects" in body and "Discover public profiles" in body
        assert "never-expose-this-test-key" not in body
        response = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 60,
                "max_requests": 3,
            },
        )
        assert response.status_code == 200
        job = response.json()
        assert job["found"] == 60 and job["persistent"] is False
        assert len(calls) == 3
        assert client.get("/api/leads").json() == before
        assert client.get("/api/search/settings").json()["jobs"] == search_before
        export = client.get("/api/public-discovery/jobs/" + job["id"] + "/export.csv")
        assert export.status_code == 409
        assert (
            client.get("/api/public-discovery/jobs/" + str(uuid4()) + "/export.csv").status_code
            == 404
        )


def test_config_errors_invalid_target_and_csrf_do_not_call_provider(setup, monkeypatch):
    app, calls = setup
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        payload = {"campaign": "bluqq-crm-recruitment.yaml", "target": 101}
        assert client.post("/api/public-discovery/jobs", json=payload).status_code == 422
        payload["target"] = 60
        assert (
            client.post(
                "/api/public-discovery/jobs", json=payload, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY")
        assert client.post("/api/public-discovery/jobs", json=payload).status_code == 503
        assert calls == []


def test_csv_enabled_only_for_permitted_persistent_runs(setup, monkeypatch):
    app, _ = setup
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 3,
            },
        ).json()
        assert job["found"] == 3 and job["persistent"]
        response = client.get("/api/public-discovery/jobs/" + job["id"] + "/export.csv")
        assert response.status_code == 200
        assert "Not verified" in response.text and "Brave public web index" in response.text
        assert len(response.text.splitlines()) == 4
        assert "never-expose-this-test-key" not in response.text
        monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "0")
        assert (
            client.get("/api/public-discovery/jobs/" + job["id"] + "/export.csv").status_code == 409
        )


def test_request_budget_validation_usage_and_preview_expiration(setup):
    app, calls = setup
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        payload = {"campaign": "bluqq-crm-recruitment.yaml", "target": 100, "max_requests": 0}
        assert client.post("/api/public-discovery/jobs", json=payload).status_code == 422
        payload["max_requests"] = 26
        assert client.post("/api/public-discovery/jobs", json=payload).status_code == 422
        assert calls == []
        payload["max_requests"] = 1
        job = client.post("/api/public-discovery/jobs", json=payload).json()
        assert job["api_calls"] == len(calls) == 1
        assert job["max_requests"] == 1
        assert job["expires_at"] > job["created"]
        state = client.get("/api/public-discovery/settings").json()
        assert state["usage"]["month_attempts"] == 1
        assert state["storage_enabled"] is False


def test_selected_public_prospects_save_to_manual_review_once_without_outreach(setup, monkeypatch):
    app, calls = setup
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 3,
                "max_requests": 1,
            },
        ).json()
        url = job["results"][0]["profile_url"]
        route = "/api/public-discovery/jobs/" + job["id"] + "/save-leads"
        delivery_before = client.get("/api/outreach/jobs").json()
        response = client.post(route, json={"profile_urls": [url, url]})
        assert response.status_code == 200, response.text
        assert response.json()["inserted"] == 0  # completion already synced eligible leads
        assert response.json()["status"] == "needs_verification"
        assert response.json()["outreach_queued"] is False
        assert client.post(route, json={"profile_urls": [url]}).json()["skipped"] == 1
        assert client.get("/api/outreach/jobs").json() == delivery_before
        assert len(calls) == 1
        from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository

        repository = DatabaseResearchRepository(
            app.state.public_discovery.path.parent / "research.sqlite3"
        )
        try:
            leads = repository.list_leads()
            assert len(leads) == 3 and any(lead.profile_url == url for lead in leads)
            assert leads[0].company == leads[0].current_title == leads[0].location == ""
            assert "Public search snippet" in leads[0].profile_snapshot
        finally:
            repository.close()


def test_public_save_rejects_foreign_urls_preview_and_missing_evidence(setup, monkeypatch):
    app, _ = setup
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 1,
            },
        ).json()
        route = "/api/public-discovery/jobs/" + job["id"] + "/save-leads"
        assert (
            client.post(
                route, json={"profile_urls": [job["results"][0]["profile_url"]]}
            ).status_code
            == 409
        )
        monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
        # Switching the owner flag cannot turn an earlier preview into stored evidence.
        assert (
            client.post(
                route, json={"profile_urls": [job["results"][0]["profile_url"]]}
            ).status_code
            == 409
        )
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 1,
            },
        ).json()
        route = "/api/public-discovery/jobs/" + job["id"] + "/save-leads"
        assert (
            client.post(
                route, json={"profile_urls": ["https://www.linkedin.com/in/foreign/"]}
            ).status_code
            == 422
        )
        assert client.post(route, json={"profile_urls": []}).status_code == 422
        assert (
            client.post(
                route,
                json={"profile_urls": [job["results"][0]["profile_url"]]},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/public-discovery/jobs/" + str(uuid4()) + "/save-leads",
                json={"profile_urls": [job["results"][0]["profile_url"]]},
            ).status_code
            == 404
        )


def test_storage_discovery_auto_populates_collected_review_and_reports(setup, monkeypatch):
    app, _ = setup
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
    app.state.public_discovery.provider_factory = lambda: SimpleNamespace(
        search=lambda *args: [
            {
                "url": "https://www.linkedin.com/in/complete-one/",
                "title": "Complete Person - Recruitment agency Founder | LinkedIn",
                "description": "Recruitment agency in London",
            },
            {
                "url": "https://www.linkedin.com/in/incomplete-one/",
                "title": "Incomplete Person - Recruitment agency Founder | LinkedIn",
                "description": "Recruitment agency",
            },
        ]
    )
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        delivery_before = client.get("/api/outreach/jobs").json()
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 2,
                "max_requests": 1,
            },
        ).json()
        assert job["pipeline_synced"] is True
        leads = client.get("/api/leads").json()
        assert len(leads) == 2
        assert all(item["outreach"]["status"] == "needs_verification" for item in leads)
        assert all(isinstance(item["outreach"]["score"], int) for item in leads)
        collected = client.get("/api/observations").json()
        assert collected["counts"] == {"read": 2, "accepted": 1, "rejected": 1}
        assert all("Brave public web" in item["campaign"] for item in collected["items"])
        runs = client.get("/api/research/runs").json()
        assert runs[0]["read"] == 2 and runs[0]["accepted"] == 1
        for dataset, expected_rows in [("leads", 2), ("observations", 2)]:
            report = client.get("/api/exports/" + dataset + ".csv?run_id=" + runs[0]["id"])
            assert report.status_code == 200
            assert "Research source" in report.text and "Brave public web index" in report.text
            import csv
            import io

            assert len(list(csv.reader(io.StringIO(report.text)))) == expected_rows + 1
        sync = client.post("/api/public-discovery/jobs/" + job["id"] + "/sync-pipeline", json={})
        assert sync.status_code == 200 and sync.json()["inserted"] == 0
        assert client.get("/api/leads").json() == leads
        assert client.get("/api/observations").json()["counts"]["read"] == 2
        assert client.get("/api/outreach/jobs").json() == delivery_before


def test_explicit_public_fields_reach_review_collected_and_csv_without_auto_approval(
    setup, monkeypatch
):
    app, _ = setup
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
    app.state.public_discovery.provider_factory = lambda: SimpleNamespace(
        search=lambda *args: [
            {
                "url": "https://www.linkedin.com/in/public-details-one/",
                "title": "Public Person - Founder at Example recruitment agency | LinkedIn",
                "description": "Location: London · Skills: CRM, Python · "
                "Education: Example University · "
                "Experience: Founder at Example agency · 500+ connections and 2K followers",
            }
        ]
    )
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 1,
                "max_requests": 1,
            },
        ).json()
        item = job["results"][0]
        assert item["profile_details"]["location"] == "London"
        lead = client.get("/api/leads").json()[0]
        assert lead["lead"]["location"] == "London"
        assert lead["lead"]["company"] == "Example recruitment agency"
        assert lead["lead"]["skills"] == ["CRM", "Python"]
        assert lead["outreach"]["status"] == "ready_for_review"
        observed = client.get("/api/observations").json()["items"][0]["profile"]
        assert observed["education"] == ["Example University"]
        assert observed["followers"] == "2K"
        export = client.get("/api/public-discovery/jobs/" + job["id"] + "/export.csv")
        assert export.status_code == 200 and "Public location" in export.text
        assert "Example University" in export.text and "public_index_unverified" in export.text


def test_automatic_exact_profile_lookup_fills_review_without_authorizing_delivery(
    setup, monkeypatch
):
    app, _ = setup
    monkeypatch.setenv("BRAVE_SEARCH_STORAGE_ALLOWED", "1")
    calls = []

    def search(query, offset):
        calls.append(query)
        item = {
            "url": "https://www.linkedin.com/in/automatic-person/",
            "title": "Automatic Person - Recruitment agency Founder | LinkedIn",
            "description": "Recruitment agency in London",
        }
        if len(calls) > 1:
            item.update(
                title="Automatic Person - Founder at Public Agency | LinkedIn",
                description="Location: London · Skills: CRM, Python",
            )
        return [item]

    app.state.public_discovery.provider_factory = lambda: SimpleNamespace(search=search)
    with TestClient(app) as client:
        client.auth = ("owner", "x" * 30)
        delivery = client.get("/api/outreach/jobs").json()
        job = client.post(
            "/api/public-discovery/jobs",
            json={
                "campaign": "bluqq-crm-recruitment.yaml",
                "target": 1,
                "max_requests": 2,
            },
        ).json()
        assert job["status"] == "completed" and job["api_calls"] == len(calls) == 2
        assert len(job["results"]) == 1 and len(job["results"][0]["additional_evidence"]) == 1
        item = client.get("/api/leads").json()[0]
        assert item["lead"]["current_title"] == "Founder"
        assert item["lead"]["company"] == "Public Agency"
        assert item["lead"]["skills"] == ["CRM", "Python"]
        assert "Additional public index query" in item["lead"]["profile_snapshot"]
        assert item["outreach"]["status"] == "ready_for_review"
        assert client.get("/api/outreach/jobs").json() == delivery
