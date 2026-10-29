"""Worker state is observable; dashboard controls cannot launch server commands."""

import sqlite3
import time

from fastapi.testclient import TestClient

from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.search_jobs import SearchJobs, WorkerHeartbeat


def test_legacy_worker_schema_upgrade_preserves_seen_and_claim_metadata(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE browser_worker (id INTEGER PRIMARY KEY, seen REAL)")
        db.execute("INSERT INTO browser_worker VALUES (1,?)", (time.time(),))
    store = SearchJobs(path)
    assert store.status()["worker_online"]
    assert not store.status()["worker_managed"]
    with store.connection() as db:
        store.record_heartbeat(db, WorkerHeartbeat(managed=True, autostart=True))
    assert store.claim() is None
    status = store.status()
    assert status["worker_managed"] and status["worker_autostart"]
    with store.connection() as db:
        db.execute("UPDATE browser_worker SET seen=?", (time.time() - 60,))
    assert not store.status()["worker_online"]


def test_worker_controls_and_authenticated_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    with TestClient(create_hosted_app()) as client:
        assert client.post("/api/search/heartbeat", json={"managed": True}).status_code == 401
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        assert (
            client.post("/api/search/claim", json={"managed": True, "autostart": True}).status_code
            == 200
        )
        state = client.get("/api/search/settings").json()
        assert state["worker_managed"] and state["worker_autostart"]
        # Old clients can still heartbeat, while unexpected execution parameters are rejected.
        assert client.post("/api/search/heartbeat", json={}).status_code == 200
        assert (
            client.post("/api/search/heartbeat", json={"command": "arbitrary"}).status_code == 422
        )
        html = client.get("/linkedin-automation/").text
        assert 'href="bluqq-linkedin-worker://start"' in html
        assert "cannot wake a switched-off or sleeping PC" in html
        assert 'id="workerSetupDialog"' in html
        assert client.post("/api/worker/start", json={}).status_code == 404
