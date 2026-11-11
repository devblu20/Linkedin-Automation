"""Authorized cloud delivery with durable receipts and no automatic resend."""

import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.unipile_client import UnipileError
from linkedin_automation.interfaces.outreach_delivery import DeliveryResult


def cloud_sending_enabled(client):
    return bool(
        client and client.settings.version == 1 and os.getenv("CLOUD_OUTREACH_ENABLED") == "1"
    )


class CloudDeliveryRunner:
    def __init__(self, client, account, allowed, queue, root, *, scope=None):
        self.client, self.account, self.allowed, self.queue = client, account, allowed, queue
        self.scope = scope
        self.journal = Path(root) / "cloud-delivery-receipt.json"

    def save(self, value):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.journal)

    def acknowledge(self, value):
        job = value["job"]
        result = value.get("result") or {
            "outcome": "uncertain",
            "proof": "Cloud process interrupted. Verify LinkedIn; automatic resend is blocked.",
        }
        self.queue.complete(UUID(job["id"]), DeliveryResult(token=job["token"], **result))
        self.journal.unlink()

    def cycle(self):
        if not cloud_sending_enabled(self.client):
            return
        if self.journal.exists():
            # Only replay acknowledgement, never the provider write, after a crash.
            self.acknowledge(json.loads(self.journal.read_text(encoding="utf-8")))
            return
        if not self.allowed():
            return
        account = self.account()
        if not account or account["status"] != "connected":
            return
        job = self.queue.claim(cloud_account=account)
        if not job:
            return
        pending = {"job": job}
        # Persist the claim before any network call; interrupted outcomes are uncertain.
        self.save(pending)
        attempted = False
        try:
            if not self.allowed() or self.account()["account_id"] != account["account_id"]:
                raise ValueError("LinkedIn connection or workspace access changed; not sent.")
            live_account = self.client.account(account["account_id"], scope_id=self.scope)
            if (
                live_account.get("connection_params", {}).get("im", {}).get("id")
                != account["member_id"]
            ):
                raise ValueError("Connected sender identity changed; reconnect and review again.")
            target = canonicalize_linkedin_profile_url(job["profile_url"])
            identifier = unquote(urlsplit(target).path.strip("/").split("/")[-1])
            profile = self.client.delivery_profile(account["account_id"], identifier)
            actual_url = profile.get("public_profile_url") or (
                "https://www.linkedin.com/in/" + profile.get("public_identifier", "")
            )
            if canonicalize_linkedin_profile_url(actual_url) != target:
                raise ValueError("Recipient profile does not match the reviewed URL; not sent.")
            provider_id = profile.get("provider_id", "")
            if (
                not isinstance(provider_id, str)
                or not re.fullmatch(r"ACo[A-Za-z0-9_-]{3,250}", provider_id)
                or profile.get("is_self") is True
                or provider_id == account["member_id"]
                or profile.get("locked") is True
            ):
                raise ValueError(
                    "Recipient identity is unavailable, locked or your own account; not sent."
                )
            connected = profile.get("network_distance") == "FIRST_DEGREE"
            invitation = profile.get("invitation") or {}
            if job["action"] == "check":
                result = {
                    "outcome": "connected" if connected else "not_connected",
                    "proof": "Provider profile confirms first-degree connection."
                    if connected
                    else "Provider profile does not show a first-degree connection yet.",
                }
            elif job["action"] == "connection" and connected:
                result = {
                    "outcome": "connected",
                    "proof": "Already first-degree connected; no invitation sent.",
                }
            elif (
                job["action"] == "connection"
                and invitation.get("type") == "SENT"
                and invitation.get("status") == "PENDING"
            ):
                result = {
                    "outcome": "pending",
                    "proof": "Provider confirms an existing pending invitation; no duplicate sent.",
                }
            else:
                if not self.allowed():
                    raise ValueError("Workspace access was revoked; not sent.")
                if self.queue.review.assessment(UUID(job["lead_id"]))["blockers"]:
                    raise ValueError("Lead no longer meets approval requirements; not sent.")
                if job["action"] == "connection":
                    if invitation.get("status") == "PENDING":
                        raise ValueError(
                            "A received invitation is pending; review it on LinkedIn first."
                        )
                    if not 1 <= len(job["draft"]) <= 300:
                        raise ValueError(
                            "Invitation note must contain 1-300 characters. "
                            "Edit and review it; not sent."
                        )
                    attempted = True
                    receipt = self.client.invite(account["account_id"], provider_id, job["draft"])
                    if receipt.get("object") != "UserInvitationSent" or not receipt.get(
                        "invitation_id"
                    ):
                        raise ValueError("Invitation response did not include a valid receipt.")
                    result = {
                        "outcome": "pending",
                        "proof": "Unipile confirmed the invitation was sent; receipt "
                        + str(receipt["invitation_id"])[:120],
                    }
                else:
                    if not connected:
                        raise ValueError(
                            "First-degree acceptance is not confirmed; message not sent."
                        )
                    if not job["draft"].strip() or len(job["draft"]) > 8000:
                        raise ValueError("Message is empty or too long; not sent.")
                    # Reply/opt-out protection is independent of AI. No provider send is
                    # attempted if the fresh read fails or a recipient hold exists.
                    if self.queue.reply_sync:
                        self.queue.reply_sync.before_send(
                            account["account_id"], target, provider_id
                        )
                    if self.queue.engagement.hold(target, account["account_id"]):
                        raise ValueError(
                            "Recipient replied or opted out; follow-up paused, not sent."
                        )
                    attempted = True
                    receipt = self.client.send_message(
                        account["account_id"], provider_id, job["draft"]
                    )
                    if (
                        receipt.get("object") != "ChatStarted"
                        or not receipt.get("message_id")
                        or not receipt.get("chat_id")
                    ):
                        raise ValueError("Message response did not include a valid receipt.")
                    result = {
                        "outcome": "message_sent",
                        "proof": "Unipile confirmed the message was sent; receipt "
                        + str(receipt["message_id"])[:120],
                    }
        except Exception as error:
            # A write timeout/error may follow a successful send. Never retry it.
            result = {
                "outcome": "uncertain" if attempted else "not_sent",
                "proof": (
                    str(error)[:300]
                    if isinstance(error, (ValueError, UnipileError))
                    else "Cloud delivery could not complete."
                )
                + (
                    " Verify LinkedIn before retrying; automatic resend blocked."
                    if attempted
                    else " No send request was made."
                ),
            }
        pending["result"] = result
        self.save(pending)
        self.acknowledge(pending)
