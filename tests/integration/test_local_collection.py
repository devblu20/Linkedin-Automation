from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.application.exceptions import LimitedVisibilityError
from linkedin_automation.domain.models import CollectionBatch, LeadCandidate
from linkedin_automation.interfaces.review_site import create_review_app


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    source = Path("config/bluqq-london-prop-family-offices.yaml")
    (config / "campaign.yaml").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (config / "invalid.yaml").write_text("broken: [")
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    return create_review_app(tmp_path / "local.sqlite3", enable_collection=True, config_dir=config)


def payload(**updates):
    return {
        "profile_url": "https://www.linkedin.com/in/example/",
        "campaign": "campaign.yaml",
        **updates,
    }


@pytest.mark.parametrize(
    "headline,location,accepted",
    [
        ("Founder of a family office", "London", 1),
        ("Civil Engineering Student", "Nigeria", 0),
    ],
)
def test_collection_matches_saves_reviews_and_reports(
    local_app, monkeypatch, headline, location, accepted
):
    class Collector:
        def __init__(self, *args):
            pass

        def collect(self, *args, **kwargs):
            candidate = LeadCandidate(
                profile_url="https://www.linkedin.com/in/example/",
                full_name="Example Founder",
                headline=headline,
                location=location,
            )
            return CollectionBatch((candidate,), checkpoint_page=1, complete=True)

    monkeypatch.setattr(
        "linkedin_automation.interfaces.local_collection.DirectProfileCollector", Collector
    )
    with TestClient(local_app) as client:
        assert "collectForm" in client.get("/").text
        assert [x["id"] for x in client.get("/api/collection/campaigns").json()] == [
            "campaign.yaml"
        ]
        response = client.post("/api/collection/profile", json=payload())
        assert response.status_code == 200
        assert response.json()["observed"] == 1
        assert response.json()["accepted"] == accepted
        assert response.json()["rejected"] == 1 - accepted
        assert response.json()["report"].endswith(".xlsx")
        rows = client.get("/api/leads").json()
        assert len(rows) == accepted
        assert client.get("/api/summary").json()["fetched"] == accepted
        if accepted:
            assert rows[0]["lead"]["full_name"] == "Example Founder"
            assert rows[0]["outreach"]["status"] == "needs_verification"


def test_collection_rejects_unsafe_requests_and_campaign_paths(local_app):
    with TestClient(local_app) as client:
        for item in [
            payload(campaign="../outside.yaml"),
            payload(profile_url="https://example.com/in/a"),
        ]:
            assert client.post("/api/collection/profile", json=item).status_code == 422
        assert (
            client.post(
                "/api/collection/profile",
                json=payload(),
                headers={"Origin": "https://other.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/collection/profile", json=payload(), headers={"Sec-Fetch-Site": "cross-site"}
            ).status_code
            == 403
        )


def test_collection_failure_releases_browser_slot(local_app, monkeypatch):
    class Collector:
        def __init__(self, *args):
            pass

        def collect(self, *args, **kwargs):
            raise LimitedVisibilityError("Profile unavailable")

    monkeypatch.setattr(
        "linkedin_automation.interfaces.local_collection.DirectProfileCollector", Collector
    )
    with TestClient(local_app) as client:
        for _ in range(2):
            response = client.post("/api/collection/profile", json=payload())
            assert response.status_code == 422
            assert "Profile unavailable" in response.json()["detail"]
        assert client.get("/api/leads").json() == []


def test_collection_is_disabled_by_default(tmp_path):
    with TestClient(create_review_app(tmp_path / "disabled.sqlite3")) as client:
        assert "collectForm" not in client.get("/").text
        assert client.post("/api/collection/profile", json=payload()).status_code == 404
