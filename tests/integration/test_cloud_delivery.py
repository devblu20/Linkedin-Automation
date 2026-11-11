"""Real queue/state transitions with a fake provider; never contact LinkedIn."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_qualification_review import seed

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.infrastructure.cloud_delivery import CloudDeliveryRunner
from linkedin_automation.infrastructure.persistence import SqliteResearchRepository
from linkedin_automation.infrastructure.unipile_client import UnipileError
from linkedin_automation.interfaces.outreach_delivery import AuthorizeDelivery, DeliveryQueue


@pytest.fixture
def cloud_delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUD_OUTREACH_ENABLED", "1")
    monkeypatch.setenv("AUTOMATIC_QUALIFICATION", "1")
    lead, _, _ = seed(
        tmp_path / "research.sqlite3",
        template="bluqq-london-independent-traders.yaml",
        complete=True,
    )
    repository = SqliteResearchRepository(tmp_path / "research.sqlite3")
    repository.initialize()
    record = repository.get_outreach(lead.id)
    repository.upsert_outreach(
        replace(
            record,
            connection_message="Hi Alex, open to connecting?",
            follow_up_message="Thanks for connecting, Alex.",
        )
    )
    queue = DeliveryQueue(tmp_path / "jobs.sqlite3", repository)
    assert queue.prepare() == 1
    sender = {"account_id": "private-sender", "member_id": "ACoSender123", "status": "connected"}

    class Provider:
        settings = SimpleNamespace(version=1)
        distance = "SECOND_DEGREE"
        calls = None

        def __init__(self):
            self.calls = []
            self.profile = {
                "public_profile_url": lead.profile_url,
                "provider_id": "ACoRecipient123",
                "is_self": False,
            }

        def account(self, identifier, **kwargs):
            assert identifier == sender["account_id"]
            return {"connection_params": {"im": {"id": "ACoSender123"}}}

        def delivery_profile(self, account_id, identifier):
            assert account_id == "private-sender" and identifier == "review-example"
            return {**self.profile, "network_distance": self.distance}

        def invite(self, account_id, provider_id, draft):
            self.calls.append(("invite", account_id, provider_id, draft))
            return {"object": "UserInvitationSent", "invitation_id": "invitation-receipt"}

        def send_message(self, account_id, provider_id, draft):
            self.calls.append(("message", account_id, provider_id, draft))
            return {
                "object": "ChatStarted",
                "chat_id": "chat-receipt",
                "message_id": "message-receipt",
            }

    provider = Provider()
    runner = CloudDeliveryRunner(provider, lambda: sender, lambda: True, queue, tmp_path)
    yield queue, runner, provider, sender, lead
    repository.close()


def enable(queue, sender, *, follow=False):
    job = next(j for j in queue.list() if j["status"] == "queued" and j["action"] != "check")
    queue.authorize(
        UUID(job["id"]),
        AuthorizeDelivery(confirmed=True, draft_hash=job["draft_hash"], include_follow_up=follow),
        cloud_account=sender,
    )
    return job


def due_check(queue):
    with queue.db() as db:
        db.execute("UPDATE delivery_jobs SET due=0 WHERE action='check' AND status='queued'")


@pytest.mark.parametrize("follow", [False, True])
def test_cloud_invite_acceptance_and_exact_follow_up_are_ordered(cloud_delivery, follow):
    queue, runner, provider, sender, lead = cloud_delivery
    runner.cycle()
    assert provider.calls == []
    enable(queue, sender, follow=follow)
    assert "cloud_account_id" not in queue.list()[0]["payload"]
    assert queue.claim() is None  # Desktop workers cannot claim cloud-authorized actions.
    runner.cycle()
    runner.cycle()
    assert len(provider.calls) == 1
    assert queue.repository.get_outreach(lead.id).status == OutreachStatus.CONNECTION_SENT
    due_check(queue)
    runner.cycle()
    assert len(provider.calls) == 1  # Acceptance still missing; no message sent.
    provider.distance = "FIRST_DEGREE"
    due_check(queue)
    runner.cycle()
    assert queue.repository.get_outreach(lead.id).status == OutreachStatus.CONNECTED
    if not follow:
        runner.cycle()
        assert len(provider.calls) == 1
        enable(queue, sender)
    runner.cycle()
    runner.cycle()
    assert [x[0] for x in provider.calls] == ["invite", "message"]
    assert provider.calls[-1][-1] == "Thanks for connecting, Alex."
    assert queue.repository.get_outreach(lead.id).status == OutreachStatus.MESSAGE_SENT


@pytest.mark.parametrize("failure", ["timeout", "no_receipt", "crash"])
def test_cloud_uncertain_write_is_never_repeated(cloud_delivery, failure):
    queue, runner, provider, sender, lead = cloud_delivery
    enable(queue, sender)

    def fail(*args):
        provider.calls.append(("write-attempt",))
        if failure == "crash":
            raise KeyboardInterrupt()
        if failure == "timeout":
            raise UnipileError("Provider timed out.")
        return {"object": "UserInvitationSent"}

    provider.invite = fail
    if failure == "crash":
        with pytest.raises(KeyboardInterrupt):
            runner.cycle()
    else:
        runner.cycle()
    restarted = CloudDeliveryRunner(
        provider, lambda: sender, lambda: True, queue, runner.journal.parent
    )
    restarted.cycle()
    restarted.cycle()
    assert len(provider.calls) == 1
    assert queue.list()[0]["status"] == "uncertain"
    assert queue.repository.get_outreach(lead.id).status == OutreachStatus.CONNECTION_QUEUED


def test_cloud_receipt_recovery_repeats_only_acknowledgement(cloud_delivery, monkeypatch):
    queue, runner, provider, sender, _ = cloud_delivery
    enable(queue, sender)
    complete = queue.complete
    monkeypatch.setattr(
        queue, "complete", lambda *args: (_ for _ in ()).throw(OSError("DB interrupted"))
    )
    with pytest.raises(OSError):
        runner.cycle()
    assert runner.journal.exists() and len(provider.calls) == 1
    monkeypatch.setattr(queue, "complete", complete)
    runner.cycle()
    runner.cycle()
    assert not runner.journal.exists() and len(provider.calls) == 1


@pytest.mark.parametrize("invalid", ["url", "self", "locked", "sender"])
def test_cloud_recipient_and_sender_must_match_before_write(cloud_delivery, invalid):
    queue, runner, provider, sender, _ = cloud_delivery
    enable(queue, sender)
    if invalid == "url":
        provider.profile["public_profile_url"] = "https://www.linkedin.com/in/someone-else/"
    elif invalid == "self":
        provider.profile["is_self"] = True
    elif invalid == "locked":
        provider.profile["locked"] = True
    else:
        provider.account = lambda *args, **kwargs: {
            "connection_params": {"im": {"id": "different-member"}}
        }
    runner.cycle()
    assert provider.calls == []
    assert queue.list()[0]["status"] == "failed"
    assert queue.list()[0]["result"]["outcome"] == "not_sent"


def test_cloud_old_or_other_account_authorization_does_not_enable_send(cloud_delivery):
    queue, runner, provider, sender, _ = cloud_delivery
    job = queue.list()[0]
    values = AuthorizeDelivery(confirmed=True, draft_hash=job["draft_hash"])
    queue.authorize(UUID(job["id"]), values)  # Older desktop authorization is insufficient.
    runner.cycle()
    assert provider.calls == []
    queue.authorize(
        UUID(job["id"]), values, cloud_account={**sender, "account_id": "other-account"}
    )
    runner.cycle()
    assert provider.calls == []


def test_cloud_sender_revocation_and_feature_switch_block_writes(cloud_delivery, monkeypatch):
    queue, runner, provider, sender, _ = cloud_delivery
    enable(queue, sender)
    monkeypatch.setenv("CLOUD_OUTREACH_ENABLED", "0")
    runner.cycle()
    monkeypatch.setenv("CLOUD_OUTREACH_ENABLED", "1")
    runner.allowed = lambda: False
    runner.cycle()
    assert provider.calls == [] and queue.list()[0]["status"] == "queued"


def test_cloud_message_cannot_send_when_acceptance_is_no_longer_confirmed(cloud_delivery):
    queue, runner, provider, sender, _ = cloud_delivery
    provider.distance = "FIRST_DEGREE"
    enable(queue, sender, follow=True)
    runner.cycle()  # Already connected: no invitation write.
    provider.distance = "SECOND_DEGREE"
    runner.cycle()
    assert provider.calls == []
    assert queue.list()[0]["result"]["outcome"] == "not_sent"
