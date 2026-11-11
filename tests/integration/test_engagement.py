"""New features against real SQLite/review/queue APIs; providers are fakes, no sends."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_cloud_delivery import cloud_delivery as cloud_delivery
from test_cloud_delivery import due_check, enable
from test_qualification_review import payload, seed

from linkedin_automation.application.engagement import analytics, suggest
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import ConnectionRequest, SentMessage
from linkedin_automation.infrastructure.engagement_store import EngagementStore
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.infrastructure.reply_sync import ReplySync
from linkedin_automation.interfaces.hosted_site import create_hosted_app
from linkedin_automation.interfaces.outreach_delivery import AuthorizeDelivery, DeliveryQueue


class AI:
    configured = True
    selection: ClassVar[dict] = {"fact": "current_title", "question": 0, "followup": 0}

    def generate(self, task, data):
        self.data = data
        return self.selection if task == "draft" else {"classification": "Interested"}


@pytest.fixture
def setup(tmp_path):
    lead, record, _ = seed(tmp_path / "research.sqlite3", complete=True)
    repository = SqliteResearchRepository(tmp_path / "research.sqlite3")
    repository.initialize()
    store = EngagementStore(tmp_path / "engagement.sqlite3")
    yield lead, record, repository, store
    repository.close()


def message(text="Yes, tell me more", **updates):
    return {
        "id": "reply-1",
        "chat": "chat-1",
        "sent_at": "2026-10-30T12:00:00.000+00:00",
        "outgoing": False,
        "content": text,
        **updates,
    }


def test_grounded_personalization_is_editable_and_does_not_write(setup):
    lead, record, repository, _ = setup
    ai = AI()
    result = suggest(lead, [], ai)
    assert lead.current_title in result["draft"]
    assert result["facts_used"] == {"current_title": lead.current_title}
    assert result["review_required"]
    assert "profile_snapshot" not in ai.data["profile_facts"]
    assert repository.get_outreach(lead.id) == record
    ai.selection = {"fact": "company", "question": 0, "followup": 0}
    with pytest.raises(ValueError, match="not available"):
        suggest(replace(lead, company=""), [], ai)


def test_followup_requires_history_and_avoids_repetition(setup):
    lead, _, _, _ = setup
    with pytest.raises(ValueError, match="existing conversation"):
        suggest(lead, [], AI(), kind="follow_up")
    history = [message("Hello Alex", outgoing=True)]
    result = suggest(lead, history, AI(), kind="follow_up")
    with pytest.raises(ValueError, match="repeats"):
        suggest(lead, history, AI(), kind="follow_up", existing=result["draft"])
    with pytest.raises(ValueError, match="disinterest"):
        suggest(lead, [*history, message("No thanks")], AI(), kind="follow_up")


def test_reply_hold_and_dedup_are_durable_without_ai(setup):
    lead, _, _, store = setup
    assert store.observe("sender", lead.profile_url, message())
    assert not store.observe("sender", lead.profile_url, message())
    assert len(store.history()) == 1
    assert EngagementStore(store.path).hold(lead.profile_url, "sender") == "Recipient replied"
    assert store.hold(lead.profile_url, "other-sender") is None
    store.observe("sender", lead.profile_url, message("Please stop messaging me"))
    assert store.history()[0]["classification"] == "Not Interested"
    store.classify("sender", "reply-1", "Interested", "AI", "Please stop messaging me")
    assert store.history()[0]["classification"] == "Not Interested"
    store.observe("sender", lead.profile_url, message("Thanks", id="reply-2"))
    assert "disinterest" in store.hold(lead.profile_url, "sender")


def test_outgoing_does_not_hold_and_stale_classification_is_ignored(setup):
    lead, _, _, store = setup
    store.observe("sender", lead.profile_url, message(outgoing=True))
    assert store.hold(lead.profile_url) is None
    store.observe("sender", lead.profile_url, message("Question?", id="reply-2"))
    store.classify("sender", "reply-2", "Interested", "AI", "old text")
    assert store.history()[-1]["classification"] is None


def test_analytics_zero_denominators_and_unique_later_replies(setup):
    lead, _, repository, store = setup
    assert analytics(repository, store)["message_reply_rate"] == 0
    at = datetime(2026, 10, 30, 11, tzinfo=UTC)
    repository.add_connection_request(
        ConnectionRequest(uuid4(), lead.id, lead.run_id, "accepted", at, at)
    )
    for _ in range(2):
        repository.add_sent_message(SentMessage(uuid4(), lead.id, lead.run_id, "Hello", "sent", at))
    store.observe("sender", lead.profile_url, message())
    store.observe("sender", lead.profile_url, message(id="reply-2"))
    store.observe("sender", "https://www.linkedin.com/in/unknown", message(id="unrelated"))
    result = analytics(repository, store)
    assert result["connection_requests_sent"] == result["connections_accepted"] == 1
    assert result["connection_acceptance_rate"] == result["message_reply_rate"] == 100
    assert result["messages_sent"] == result["replies_received"] == 2
    assert analytics(repository, store, account="other")["replies_received"] == 0
    assert analytics(repository, store, uuid4())["messages_sent"] == 0


class Provider:
    settings = SimpleNamespace(version=1)

    def __init__(self, lead):
        self.lead, self.requests = lead, []
        self.items = [self.raw()]
        self.cursor = None
        self.chat_type = 0
        self.profile_url = lead.profile_url

    def raw(self, **updates):
        return {
            "id": "reply-1",
            "account_id": "sender",
            "chat_id": "chat-1",
            "sender_id": "ACoRecipient123",
            "is_sender": False,
            "text": "Interested",
            "timestamp": "2026-10-30T12:00:00.000Z",
            **updates,
        }

    def delivery_profile(self, account, sender):
        return {"provider_id": sender, "public_profile_url": self.profile_url}

    def request(self, path):
        self.requests.append(path)
        if "/chats/" in path:
            return {
                "account_id": "sender",
                "type": self.chat_type,
                "attendee_provider_id": "ACoRecipient123",
            }
        return {"items": self.items, "cursor": self.cursor}


def sync_for(lead, repository, store, ai=None):
    provider = Provider(lead)
    sync = ReplySync(
        provider,
        lambda: {"account_id": "sender", "status": "connected"},
        lambda: True,
        repository,
        store,
        ai or AI(),
    )
    return sync, provider


def test_sync_reads_only_and_classifies_actual_incoming(setup):
    lead, _, repository, store = setup
    sync, provider = sync_for(lead, repository, store)
    sync.cycle(force=True)
    sync.cycle(force=True)
    assert len(store.history()) == 1
    assert store.history()[0]["classification"] == "Interested"
    assert store.hold(lead.profile_url, "sender")
    assert store.sync_state("sender")["last_success"]
    assert all(x.startswith(("/api/v1/messages?", "/api/v1/chats/")) for x in provider.requests)


@pytest.mark.parametrize("case", ["other_account", "group", "unknown_profile", "system", "hidden"])
def test_unrelated_or_system_messages_do_not_become_replies(setup, case):
    lead, _, repository, store = setup
    sync, provider = sync_for(lead, repository, store)
    if case == "other_account":
        provider.items[0]["account_id"] = "someone-else"
    elif case == "group":
        provider.chat_type = 1
    elif case == "unknown_profile":
        provider.profile_url = "https://www.linkedin.com/in/unrelated"
    else:
        provider.items[0]["is_event" if case == "system" else "hidden"] = True
    sync.cycle(force=True)
    assert store.history() == []


def test_sync_pagination_checkpoint_survives_restart(setup):
    lead, _, repository, store = setup
    sync, provider = sync_for(lead, repository, store)
    provider.cursor = "next-page"
    sync.cycle(force=True)
    assert store.sync_state("sender")["after_at"] is None
    sync2, provider2 = sync_for(lead, repository, EngagementStore(store.path))
    provider2.items = []
    sync2.cycle(force=True)
    assert "cursor=next-page" in provider2.requests[0]
    assert store.sync_state("sender")["after_at"]


def test_fresh_presend_check_blocks_reply_even_without_ai(setup):
    lead, _, repository, store = setup
    sync, _ = sync_for(lead, repository, store, SimpleNamespace(configured=False))
    with pytest.raises(ValueError, match="paused"):
        sync.before_send("sender", lead.profile_url, "ACoRecipient123")
    assert store.history()[0]["classification"] is None


def test_reply_pauses_queued_followup_without_changing_connection_status(setup, tmp_path):
    lead, record, repository, store = setup
    repository.upsert_outreach(
        replace(
            record,
            status=OutreachStatus.CONNECTED,
            firm_type="family office",
            funds_gbp=1_000_000,
            fund_evidence_url="https://example.com/filing",
            follow_up_message="Thank you for connecting",
        )
    )
    queue = DeliveryQueue(tmp_path / "jobs.sqlite3", repository)
    queue.prepare()
    job = queue.list()[0]
    sender = {"account_id": "sender", "member_id": "ACoSender123"}
    queue.authorize(
        UUID(job["id"]),
        AuthorizeDelivery(confirmed=True, draft_hash=job["draft_hash"]),
        cloud_account=sender,
    )
    store.observe("sender", lead.profile_url, message())
    assert queue.claim(cloud_account=sender) is None
    assert queue.list()[0]["followup_hold"] == "Recipient replied"
    assert queue.list()[0]["status"] == "queued"
    assert repository.get_outreach(lead.id).status == OutreachStatus.CONNECTED


def test_new_endpoints_preserve_existing_drafts_and_auth(setup, tmp_path, monkeypatch):
    lead, record, repository, _ = setup
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_hosted_app(data_root=tmp_path, access_credentials=("test", "a" * 24))
    with TestClient(app) as client:
        assert client.get("/api/enhancements/analytics").status_code == 401
        client.auth = ("test", "a" * 24)
        url = f"/api/enhancements/leads/{lead.id}/draft"
        assert client.post(url, json={"kind": "connection"}).status_code == 503
        assert (
            client.post(
                f"/api/enhancements/leads/{uuid4()}/draft", json={"kind": "connection"}
            ).status_code
            == 404
        )
        app.state.message_ai = AI()
        assert client.post(url, json={"kind": "connection"}).status_code == 200
        assert repository.get_outreach(lead.id) == record
        assert client.get("/api/enhancements/analytics").status_code == 200
        html = client.get("/").text
        assert "AI draft assistance" in html and "Incoming replies" in html
        assert "Campaign analytics" in html
        assert client.put(f"/api/leads/{lead.id}", json=payload(record)).status_code == 200
        assert client.post("/api/enhancements/replies/sync", json={}).status_code == 409


def test_two_workspaces_cannot_read_each_others_replies(setup, tmp_path):
    lead, _, _, store = setup
    store.observe("sender", lead.profile_url, message("private reply"))
    other = tmp_path / "other"
    app = create_hosted_app(data_root=other, access_credentials=("test", "a" * 24))
    with TestClient(app) as client:
        client.auth = ("test", "a" * 24)
        assert client.get("/api/enhancements/replies").json() == []
        assert client.get(f"/api/enhancements/leads/{lead.id}/conversation").status_code == 404


@pytest.mark.parametrize("case", ["reply_during_profile_read", "reply_read_unavailable"])
def test_inflight_cloud_followup_is_stopped_before_provider_write(cloud_delivery, case):
    queue, runner, provider, sender, lead = cloud_delivery
    enable(queue, sender, follow=True)
    runner.cycle()
    provider.distance = "FIRST_DEGREE"
    due_check(queue)
    runner.cycle()
    if case == "reply_during_profile_read":
        original = provider.delivery_profile

        def profile(*args):
            queue.engagement.observe(sender["account_id"], lead.profile_url, message())
            return original(*args)

        provider.delivery_profile = profile
    else:
        class UnavailableSync:
            def before_send(self, *args):
                raise ValueError("Cannot verify replies; follow-up not sent.")

        queue.reply_sync = UnavailableSync()
    runner.cycle()
    assert not any(call[0] == "message" for call in provider.calls)
    failed = [x for x in queue.list() if x["action"] == "message"]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert queue.repository.list_sent_messages() == []
