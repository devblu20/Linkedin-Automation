"""Hosted access boundaries, persistence and isolation from existing databases."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from linkedin_automation.interfaces.hosted_site import create_hosted_app


def test_requires_strong_credentials(monkeypatch):
    monkeypatch.delenv("DASHBOARD_USERNAME", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        create_hosted_app()


def test_hosted_boundaries(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    # An inherited old app database must never be touched.
    monkeypatch.setenv(
        "LINKEDIN_AUTOMATION_DATABASE_URL", "postgresql://do-not-connect.invalid/old"
    )
    app = create_hosted_app()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        for path in ["/", "/api/leads", "/api/summary", "/api/search/settings"]:
            assert client.get(path).status_code == 401
        for path in ["/api/search/jobs", "/api/search/claim", "/api/search/heartbeat"]:
            assert client.post(path, json={}).status_code == 401
        assert client.get("/api/leads", auth=("reviewer", "wrong")).status_code == 401
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        assert client.get("/api/leads").json() == []
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Send approved" not in dashboard.text
        assert "Send ready messages" not in dashboard.text
        assert 'id="collectForm"' not in dashboard.text
        assert client.post("/api/collection/profile", json={}).status_code == 404
        assert "escapeHtml(l.full_name)" in dashboard.text
        assert dashboard.headers["cache-control"] == "no-store"
        for action in ["queue", "send-connections", "send-messages", "check-connections"]:
            assert client.post(f"/api/actions/{action}", json={}).status_code == 409
        assert (
            client.put(
                "/api/leads/test", json={}, headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )
        assert client.put("/api/leads/test", data={"status": "approved"}).status_code == 403
    assert (tmp_path / "research.sqlite3").exists()
    with TestClient(create_hosted_app()) as client:
        assert (
            client.get(
                "/api/leads", auth=("reviewer", "test-password-at-least-24-characters")
            ).json()
            == []
        )
