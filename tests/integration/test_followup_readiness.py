"""Scheduled queue/read-back scenarios in temporary databases; no browser or provider writes."""

import time

import pytest
from test_browser_engagement import SENDER, observation
from test_browser_engagement import browser_app as browser_app
from test_outreach_delivery import authorize, claim, complete, prepare

from linkedin_automation.infrastructure.engagement_store import EngagementStore
from linkedin_automation.infrastructure.message_ai import AIUnavailable


def pending_message(client):
    invitation = prepare(client)
    authorize(client, invitation)
    complete(client, claim(client), "connected")
    return next(
        x for x in client.get("/api/outreach/jobs").json()["jobs"] if x["action"] == "message"
    )


def scheduled_message(client, app):
    message = pending_message(client)
    due = time.time() + 3600
    with app.state.delivery_queue.db() as db:
        db.execute("UPDATE delivery_jobs SET due=? WHERE id=?", (due, message["id"]))
    assert claim(client) is None
    return message, due


def test_scheduled_message_waits_until_due_and_only_completes_once(browser_app, monkeypatch):
    client, app, _lead = browser_app
    message, due = scheduled_message(client, app)
    monkeypatch.setattr(
        "linkedin_automation.interfaces.outreach_delivery.time.time", lambda: due + 1
    )
    claimed = claim(client)
    assert claimed["id"] == message["id"]
    assert claim(client) is None
    complete(client, claimed, "message_sent")
    complete(client, claimed, "message_sent")
    assert client.get("/api/summary").json()["message_sent"] == 1
    assert claim(client) is None


@pytest.mark.parametrize(
    "label,text",
    [
        ("Interested", "Yes, I am interested. Please send more details."),
        ("Not Interested", "No thanks. Please stop messaging me."),
        ("Question", "What is your pricing?"),
        ("Meeting Request", "Can we schedule a call next Tuesday?"),
        ("General Reply", "Thanks for the update."),
    ],
)
def test_each_reply_intent_pauses_due_followup_and_hold_survives_restart(
    browser_app, monkeypatch, label, text
):
    client, app, lead = browser_app
    _message, due = scheduled_message(client, app)
    app.state.browser_engagement.ai.generate = lambda task, data: {"classification": label}
    reply = client.post("/api/outreach/browser/observations", json=observation(lead, text))
    assert reply.status_code == 200 and reply.json()["blocked"]
    monkeypatch.setattr(
        "linkedin_automation.interfaces.outreach_delivery.time.time", lambda: due + 1
    )
    assert claim(client) is None
    reply = client.get("/api/enhancements/replies").json()[0]
    assert reply["classification"] == label and reply["followups_paused"]
    reopened = EngagementStore(app.state.delivery_queue.engagement.path)
    assert reopened.hold(lead.profile_url, "browser:" + SENDER)
    assert client.get("/api/summary").json()["message_sent"] == 0


def test_reply_pause_does_not_depend_on_ai_availability(browser_app, monkeypatch):
    client, app, lead = browser_app
    _message, due = scheduled_message(client, app)

    def offline(*args):
        raise AIUnavailable("Synthetic provider outage")

    app.state.browser_engagement.ai.generate = offline
    assert (
        client.post(
            "/api/outreach/browser/observations",
            json=observation(lead, "A reply even while AI is down"),
        ).status_code
        == 200
    )
    monkeypatch.setattr(
        "linkedin_automation.interfaces.outreach_delivery.time.time", lambda: due + 1
    )
    assert claim(client) is None
    assert app.state.delivery_queue.engagement.hold(lead.profile_url, "browser:" + SENDER)


def test_cancelled_future_message_stays_cancelled_after_due(browser_app, monkeypatch):
    client, app, _lead = browser_app
    message, due = scheduled_message(client, app)
    response = client.post("/api/outreach/jobs/" + message["id"] + "/cancel", json={})
    assert response.status_code == 200
    monkeypatch.setattr(
        "linkedin_automation.interfaces.outreach_delivery.time.time", lambda: due + 1
    )
    assert claim(client) is None
    assert client.get("/api/summary").json()["message_sent"] == 0


def test_sender_reconnect_does_not_reuse_other_accounts_authorization(browser_app, monkeypatch):
    client, app, _lead = browser_app
    _message, due = scheduled_message(client, app)
    monkeypatch.setattr(
        "linkedin_automation.interfaces.outreach_delivery.time.time", lambda: due + 1
    )
    monkeypatch.setenv("PERSONAL_LINKEDIN_PROFILE_URL", "https://www.linkedin.com/in/new-account/")
    assert claim(client) is None
    assert client.get("/api/summary").json()["message_sent"] == 0
