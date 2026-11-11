"""Personal browser delivery and reply sync with real databases; no live sends."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_outreach_delivery import authorize, claim, complete, prepare
from test_qualification_review import seed

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import SentMessage
from linkedin_automation.infrastructure.access_auth import PREFIX, AccessSettings
from linkedin_automation.interfaces.access_gateway import create_access_gateway
from linkedin_automation.interfaces.hosted_site import create_hosted_app

SENDER = "https://www.linkedin.com/in/owner-example/"


@pytest.fixture
def browser_app(tmp_path, monkeypatch):
    for key, value in {
        "DASHBOARD_USERNAME": "reviewer",
        "DASHBOARD_PASSWORD": "test-password-at-least-24-characters",
        "HOSTED_DATA_DIR": str(tmp_path),
        "AUTOMATIC_QUALIFICATION": "1",
        "AUTOMATIC_QUEUE": "1",
        "PERSONAL_BROWSER_MODE": "1",
        "WEBSITE_CLOUD_MODE": "0",
        "PERSONAL_LINKEDIN_PROFILE_URL": SENDER,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("UNIPILE_API_KEY", raising=False)
    lead, _, _ = seed(tmp_path / "research.sqlite3", "bluqq-london-independent-traders.yaml", True)
    app = create_hosted_app()
    app.state.browser_engagement.ai = type(
        "AI",
        (),
        {
            "configured": True,
            "generate": lambda self, task, data: {"classification": "Interested"},
        },
    )()
    with TestClient(app) as client:
        client.auth = ("reviewer", "test-password-at-least-24-characters")
        yield client, app, lead


def observation(lead, text="Yes, tell me more", **updates):
    value = {
        "sender": SENDER,
        "conversations": [
            {
                "profile_url": lead.profile_url,
                "complete": True,
                "incoming_detected": True,
                "messages": [
                    {
                        "id": "observed-1",
                        "chat": lead.profile_url,
                        "sent_at": "2026-10-30T12:00:00+00:00",
                        "outgoing": False,
                        "content": text,
                    }
                ],
            }
        ],
    }
    value.update(updates)
    return value


def test_browser_sender_pinning_and_existing_ordered_delivery(browser_app, monkeypatch):
    client, _app, lead = browser_app
    job = prepare(client)
    assert claim(client) is None
    authorize(client, job)
    monkeypatch.setenv("PERSONAL_LINKEDIN_PROFILE_URL", "https://www.linkedin.com/in/different/")
    assert claim(client) is None
    monkeypatch.setenv("PERSONAL_LINKEDIN_PROFILE_URL", SENDER)
    invitation = claim(client)
    assert invitation["browser_sender_profile"] == SENDER
    complete(client, invitation, "pending")
    check = client.get("/api/outreach/jobs").json()["jobs"][0]
    client.post(f"/api/outreach/jobs/{check['id']}/check-now", json={})
    complete(client, claim(client), "connected")
    reply = client.post("/api/outreach/browser/observations", json=observation(lead, "No thanks"))
    assert reply.status_code == 200 and reply.json()["blocked"]
    assert claim(client) is None
    saved = client.get("/api/enhancements/replies").json()
    assert saved[0]["classification"] == "Not Interested"
    assert saved[0]["followups_paused"]
    assert client.get("/api/enhancements/analytics").json()["connections_accepted"] == 1


def test_sync_ingestion_dedup_classification_and_worker_auth(browser_app):
    client, app, lead = browser_app
    queue = app.state.delivery_queue
    record = queue.repository.get_outreach(lead.id)
    queue.repository.upsert_outreach(replace(record, status=OutreachStatus.CONNECTED))
    assert client.post("/api/enhancements/replies/sync", json={}).status_code == 200
    sync = client.post("/api/outreach/browser/sync/claim", json={}).json()["job"]
    assert sync and sync["targets"][0]["profile_url"] == lead.profile_url
    body = {**observation(lead), "id": sync["id"], "token": sync["token"], "error": ""}
    assert (
        client.post(
            "/api/outreach/browser/sync/complete", json={**body, "token": "0" * 48}
        ).status_code
        == 409
    )
    assert client.post("/api/outreach/browser/sync/complete", json=body).status_code == 200
    assert (
        client.post("/api/outreach/browser/observations", json=observation(lead)).status_code == 200
    )
    replies = client.get("/api/enhancements/replies").json()
    assert len(replies) == 1 and replies[0]["classification"] == "Interested"
    assert client.get("/api/enhancements/status").json()["last_sync"]
    assert client.get("/api/enhancements/status").json()["reply_sync_available"]
    assert (
        client.post("/api/outreach/browser/settings", json={}, auth=("wrong", "wrong")).status_code
        == 401
    )
    app.state.website_user = {"email": "private@example.com"}
    assert client.post("/api/outreach/browser/settings", json={}).status_code == 403


def test_unrecognized_sender_target_and_dates_are_rejected(browser_app):
    client, app, lead = browser_app
    assert (
        client.post(
            "/api/outreach/browser/observations",
            json=observation(lead, sender="https://www.linkedin.com/in/other/"),
        ).status_code
        == 409
    )
    payload = observation(lead)
    payload["conversations"][0]["profile_url"] = "https://www.linkedin.com/in/unknown/"
    assert client.post("/api/outreach/browser/observations", json=payload).status_code == 409
    payload = observation(lead)
    payload["conversations"][0]["messages"][0]["sent_at"] = "2026-10-30T12:00:00"
    assert client.post("/api/outreach/browser/observations", json=payload).status_code == 422
    assert not app.state.engagement.history()


def test_incomplete_attachment_reply_still_creates_durable_hold(browser_app):
    client, app, lead = browser_app
    payload = observation(lead)
    payload["conversations"][0].update(complete=False, messages=[])
    result = client.post("/api/outreach/browser/observations", json=payload)
    assert result.status_code == 200
    assert result.json()["complete"] is False and result.json()["blocked"] is True
    assert app.state.engagement.hold(lead.profile_url, "browser:" + SENDER) == "Recipient replied"
    assert not app.state.engagement.history()


def test_personal_bridge_is_not_installed_for_private_apps(browser_app, tmp_path):
    _, _, _ = browser_app
    app = create_hosted_app(
        data_root=tmp_path / "private", access_credentials=("private", "x" * 32)
    )
    assert not hasattr(app.state, "browser_engagement")


def test_old_unpinned_authorization_is_not_reused(browser_app):
    client, app, _ = browser_app
    job = prepare(client)
    # A previously authorized legacy job must be reviewed for this sender again.
    with app.state.delivery_queue.db() as db:
        db.execute("UPDATE delivery_jobs SET authorized=1 WHERE id=?", (job["id"],))
    assert claim(client) is None
    authorize(client, job)
    assert claim(client)["browser_sender_profile"] == SENDER


def test_cloud_sender_approval_cannot_be_silently_rebound(browser_app):
    client, app, _ = browser_app
    job = prepare(client)
    with app.state.delivery_queue.db() as db:
        row = db.execute("SELECT payload FROM delivery_jobs WHERE id=?", (job["id"],)).fetchone()
        payload = json.loads(row["payload"])
        payload.update(cloud_account_id="previous-provider-account", cloud_member_id="old-member")
        db.execute(
            "UPDATE delivery_jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"])
        )
    result = client.post(
        f"/api/outreach/jobs/{job['id']}/authorize",
        json={
            "confirmed": True,
            "draft_hash": job["draft_hash"],
            "include_follow_up": True,
        },
    )
    assert result.status_code == 409
    assert claim(client) is None


def test_admin_browser_session_cannot_impersonate_local_worker(browser_app, tmp_path):
    _, app, _ = browser_app
    origin = "https://www.bluqq.com"
    gateway = create_access_gateway(
        app, root=tmp_path, settings=AccessSettings(origin, frozenset(), "", "")
    )
    with TestClient(gateway, base_url=origin) as browser:
        assert (
            browser.post(
                PREFIX + "/api/auth/admin/login",
                json={
                    "username": "reviewer",
                    "password": "test-password-at-least-24-characters",
                },
            ).status_code
            == 200
        )
        response = browser.post(
            PREFIX + "/api/outreach/browser/observations",
            json={
                "sender": SENDER,
                "conversations": [],
            },
        )
        assert response.status_code == 403


def test_reviewed_response_keeps_hold_and_invalidates_on_new_reply(browser_app):
    client, app, lead = browser_app
    repository = app.state.delivery_queue.repository
    record = repository.get_outreach(lead.id)
    repository.upsert_outreach(replace(record, status=OutreachStatus.CONNECTED))
    job = prepare(client)
    assert job["action"] == "message"
    client.post("/api/outreach/browser/observations", json=observation(lead, "Ok"))
    values = {"confirmed": True, "draft_hash": job["draft_hash"], "reviewed_reply": True}
    assert client.post(f"/api/outreach/jobs/{job['id']}/authorize", json=values).status_code == 200
    new_reply = observation(lead, "One more question")
    new_reply["conversations"][0]["messages"][0]["id"] = "new-reply"
    client.post("/api/outreach/browser/observations", json=new_reply)
    assert claim(client) is None
    assert client.post(f"/api/outreach/jobs/{job['id']}/authorize", json=values).status_code == 200
    approved = claim(client)
    assert approved["reviewed_reply_signature"]
    complete(client, approved, "message_sent")
    assert app.state.engagement.hold(lead.profile_url, "browser:" + SENDER) == "Recipient replied"
    assert claim(client) is None


def test_optout_cannot_be_overridden_by_reviewed_response(browser_app):
    client, app, lead = browser_app
    repository = app.state.delivery_queue.repository
    record = repository.get_outreach(lead.id)
    repository.upsert_outreach(replace(record, status=OutreachStatus.CONNECTED))
    job = prepare(client)
    client.post(
        "/api/outreach/browser/observations", json=observation(lead, "Please stop messaging me")
    )
    result = client.post(
        f"/api/outreach/jobs/{job['id']}/authorize",
        json={
            "confirmed": True,
            "draft_hash": job["draft_hash"],
            "reviewed_reply": True,
        },
    )
    assert result.status_code == 409
    assert claim(client) is None


def test_reply_rate_uses_verified_order_not_guessed_timestamp_seconds(browser_app):
    client, app, lead = browser_app
    text = "Exactly confirmed app message"
    app.state.delivery_queue.repository.add_sent_message(
        SentMessage(
            uuid4(),
            lead.id,
            lead.run_id,
            text,
            "sent",
            datetime(2026, 10, 30, 12, 0, 40, tzinfo=UTC),
        )
    )
    base = {"chat": lead.profile_url, "sent_at": "2026-10-30T12:00:00+00:00"}
    messages = [
        {**base, "id": "before", "content": "Earlier reply", "outgoing": False},
        {**base, "id": "sent", "content": text, "outgoing": True},
        {**base, "id": "after", "content": "Received", "outgoing": False},
    ]
    body = {
        "sender": SENDER,
        "conversations": [
            {
                "profile_url": lead.profile_url,
                "complete": True,
                "incoming_detected": True,
                "messages": messages,
            }
        ],
    }
    assert client.post("/api/outreach/browser/observations", json=body).status_code == 200
    assert client.get("/api/enhancements/analytics").json()["message_reply_rate"] == 100
    assert not app.state.engagement.observed_reply_after(
        lead.profile_url, "browser:" + SENDER, "before", text
    )
    assert not app.state.engagement.observed_reply_after(
        lead.profile_url, "other-account", "after", text
    )
    body["conversations"][0]["messages"] = messages[:2]
    client.post("/api/outreach/browser/observations", json=body)
    assert client.get("/api/enhancements/analytics").json()["message_reply_rate"] == 0
    body["conversations"][0]["messages"] = [
        *messages[:2],
        {**messages[1], "id": "duplicate"},
        messages[2],
    ]
    client.post("/api/outreach/browser/observations", json=body)
    assert client.get("/api/enhancements/analytics").json()["message_reply_rate"] == 0
