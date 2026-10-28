"""Temporary-database delivery tests; no requests are sent to LinkedIn."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_qualification_review import seed

from linkedin_automation.interfaces.hosted_site import create_hosted_app


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "reviewer")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-password-at-least-24-characters")
    monkeypatch.setenv("HOSTED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOMATIC_QUALIFICATION", "1")
    monkeypatch.setenv("AUTOMATIC_QUEUE", "1")
    lead, _, _ = seed(
        tmp_path / "research.sqlite3",
        template="bluqq-london-independent-traders.yaml",
        complete=True,
    )
    app = create_hosted_app()
    with TestClient(app) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client, lead


def prepare(client):
    response = client.post("/api/outreach/prepare", json={})
    assert response.status_code == 200, response.text
    return client.get("/api/outreach/jobs").json()["jobs"][0]


def authorize(client, job, follow=True):
    response = client.post(
        "/api/outreach/jobs/" + job["id"] + "/authorize",
        json={"confirmed": True, "draft_hash": job["draft_hash"], "include_follow_up": follow},
    )
    assert response.status_code == 200, response.text


def claim(client):
    response = client.post("/api/outreach/claim", json={})
    assert response.status_code == 200, response.text
    return response.json()["job"]


def complete(client, job, outcome):
    response = client.post(
        "/api/outreach/jobs/" + job["id"] + "/complete",
        json={
            "token": job["token"],
            "outcome": outcome,
            "proof": "Local fixture confirmation, not a live send",
        },
    )
    assert response.status_code == 200, response.text


def test_delivery_pipeline_is_ordered_and_idempotent(delivery):
    client, lead = delivery
    job = prepare(client)
    assert client.get("/api/leads").json()[0]["outreach"]["status"] == "connection_queued"
    assert claim(client) is None
    assert (
        client.post(
            "/api/outreach/jobs/" + job["id"] + "/authorize",
            json={"confirmed": True, "draft_hash": "0" * 64},
        ).status_code
        == 409
    )
    authorize(client, job)
    invitation = claim(client)
    assert invitation["action"] == "connection"
    assert (
        client.post(
            "/api/leads/" + str(lead.id) + "/activities",
            json={
                "id": str(uuid4()),
                "action": "connection_sent",
                "expected_status": "connection_queued",
                "note": "Must not race with automatic delivery",
                "confirmed": True,
            },
        ).status_code
        == 409
    )
    complete(client, invitation, "pending")
    complete(client, invitation, "pending")
    assert len(client.get("/api/summary").json()["connection_requests"]) == 1
    check = client.get("/api/outreach/jobs").json()["jobs"][0]
    assert check["action"] == "check" and claim(client) is None
    client.post("/api/outreach/jobs/" + check["id"] + "/check-now", json={})
    complete(client, claim(client), "not_connected")
    check = client.get("/api/outreach/jobs").json()["jobs"][0]
    client.post("/api/outreach/jobs/" + check["id"] + "/check-now", json={})
    complete(client, claim(client), "connected")
    message = claim(client)
    assert message["action"] == "message"
    assert client.get("/api/leads").json()[0]["outreach"]["status"] == "message_ready"
    assert client.get("/api/summary").json()["message_sent"] == 0
    complete(client, message, "message_sent")
    complete(client, message, "message_sent")
    summary = client.get("/api/summary").json()
    assert summary["connection_sent"] == summary["connected"] == summary["message_sent"] == 1
    assert len(summary["messages"]) == 1
    assert summary["connection_requests"][0]["status"] == "accepted"


def test_uncertain_delivery_blocks_retry_until_user_checks(delivery):
    client, _ = delivery
    job = prepare(client)
    authorize(client, job)
    complete(client, claim(client), "uncertain")
    assert claim(client) is None
    assert client.get("/api/summary").json()["connection_sent"] == 0
    endpoint = "/api/outreach/jobs/" + job["id"] + "/resolve-not-sent"
    assert (
        client.post(
            endpoint, json={"confirmed": False, "note": "Checked profile carefully"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint,
            json={
                "confirmed": True,
                "note": "Checked LinkedIn; no pending invitation or sent note",
            },
        ).status_code
        == 200
    )
    assert client.get("/api/leads").json()[0]["outreach"]["status"] == "approved"
    replacement = prepare(client)
    assert replacement["id"] != job["id"]
    assert not replacement["authorized"] and claim(client) is None


def test_existing_connection_does_not_create_a_fake_sent_invitation(delivery):
    client, _ = delivery
    job = prepare(client)
    authorize(client, job, follow=False)
    complete(client, claim(client), "connected")
    summary = client.get("/api/summary").json()
    assert summary["connected"] == 1 and summary["connection_sent"] == 0
    assert claim(client) is None
    message = client.get("/api/outreach/jobs").json()["jobs"][0]
    assert message["action"] == "message" and not message["authorized"]
