"""Read-only Unipile V1 reply ingestion, scoped to the current workspace and account."""

import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from urllib.parse import quote, urlencode

from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.unipile_client import UnipileError


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Message timestamp needs a timezone")
    return result.astimezone(UTC).isoformat(timespec="milliseconds")


class ReplySync:
    def __init__(self, client, account, allowed, repository, store, ai):
        self.client, self.account, self.allowed = client, account, allowed
        self.repository, self.store, self.ai = repository, store, ai
        self.lock = Lock()
        self.last_attempt = 0
        self.identity_cache = {}

    def current(self):
        account = self.account()
        if (
            not self.allowed()
            or not account
            or account.get("status") != "connected"
            or self.client.settings.version != 1
        ):
            raise ValueError("Connect a supported LinkedIn account before syncing replies.")
        return account["account_id"]

    def profile_for(self, account, sender):
        key = (account, sender)
        if key in self.identity_cache:
            return self.identity_cache[key]
        profile = self.client.delivery_profile(account, sender)
        if profile.get("provider_id") != sender or profile.get("is_self"):
            return None
        url = profile.get("public_profile_url") or (
            "https://www.linkedin.com/in/" + profile.get("public_identifier", "")
        )
        url = canonicalize_linkedin_profile_url(url)
        self.identity_cache[key] = url
        return url

    def ingest(self, account, items, *, target=None):
        """Unknown profiles and group/system messages never become lead replies."""
        urls = {lead.profile_url for lead in self.repository.list_leads()}
        changed, chats = 0, {}
        for item in items:
            if self.current() != account:
                raise ValueError("LinkedIn account changed during reply sync")
            if (
                item.get("account_id") != account
                or item.get("is_sender") not in (False, True, 0, 1)
                or item.get("is_sender") is None
                or item.get("is_event")
                or item.get("hidden")
                or item.get("deleted")
                or not item.get("id")
                or not item.get("chat_id")
                or not item.get("sender_id")
            ):
                continue
            chat_id = item["chat_id"]
            if chat_id not in chats:
                chats[chat_id] = self.client.request("/api/v1/chats/" + quote(chat_id, safe=""))
            chat = chats[chat_id]
            outgoing = bool(item["is_sender"])
            sender = chat.get("attendee_provider_id")
            if (
                chat.get("account_id") != account
                or chat.get("type") != 0
                or not sender
                or (not outgoing and sender != item["sender_id"])
            ):
                continue
            url = self.profile_for(account, sender)
            if url not in urls or (target and url != target):
                continue
            content = item.get("text", "")
            if not isinstance(content, str) or len(content) > 32000:
                raise ValueError("Unsupported incoming message size")
            # Hold ALL incoming user replies, even attachment-only replies without text.
            changed += self.store.observe(
                account,
                url,
                {
                    "id": item["id"],
                    "chat": chat_id,
                    "sent_at": timestamp(item["timestamp"]),
                    "outgoing": outgoing,
                    "content": content,
                },
            )
        return changed

    def cycle(self, *, force=False):
        if not force and time.monotonic() - self.last_attempt < 60:
            return
        if not self.lock.acquire(blocking=False):
            return
        account = None
        try:
            self.last_attempt = time.monotonic()
            account = self.current()
            state = self.store.sync_state(account)
            params = {"account_id": account, "limit": 100}
            if state.get("cursor"):
                params["cursor"] = state["cursor"]
            if state.get("after_at"):
                params["after"] = state["after_at"]
            result = self.client.request("/api/v1/messages?" + urlencode(params))
            items = result.get("items")
            if not isinstance(items, list):
                raise ValueError("Invalid reply response")
            self.ingest(account, items)
            dates = [timestamp(x["timestamp"]) for x in items if x.get("account_id") == account]
            newest = max([state.get("newest") or "1970-01-01T00:00:00.000+00:00", *dates])
            cursor = result.get("cursor")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("Invalid reply cursor")
            after = state.get("after_at")
            if not cursor:
                after = (datetime.fromisoformat(newest) - timedelta(seconds=60)).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z"
                )
            self.store.save_sync(
                account,
                cursor=cursor,
                after_at=after,
                newest=newest,
                last_success=datetime.now(UTC).isoformat(),
                error=None,
            )
            self.classify_pending(account)
        except (ValueError, UnipileError, KeyError, TypeError):
            if account:
                self.store.save_sync(
                    account,
                    error="Reply sync needs attention. Follow-ups require a fresh reply check.",
                )
            if force:
                raise ValueError(
                    "Cannot sync replies. Check LinkedIn connection and Messaging Read permission."
                ) from None
        finally:
            self.lock.release()

    def classify_pending(self, account):
        if not self.ai.configured:
            return
        pending = [
            x
            for x in self.store.history(account=account)
            if not x["outgoing"] and not x["classification"]
        ][:5]
        for item in pending:
            if self.current() != account:
                return
            try:
                history = [
                    x
                    for x in self.store.history(item["profile_url"], account)
                    if x["sent_at"] <= item["sent_at"]
                ][-10:]
                label = self.ai.generate(
                    "classify",
                    {
                        "reply": item["content"],
                        "history": [
                            {"outgoing": bool(x["outgoing"]), "text": x["content"][:2000]}
                            for x in history
                        ],
                    },
                )["classification"]
                self.store.classify(account, item["id"], label, "AI", item["content"])
            except ValueError:
                # Keep the reply hold regardless of model/key failures. Retry only on next sync.
                return

    def before_send(self, account, profile_url, provider_id):
        """Fresh targeted read: failure stops this follow-up before any provider write."""
        if self.current() != account:
            raise ValueError("LinkedIn account changed; follow-up not sent.")
        result = self.client.request(
            "/api/v1/messages?"
            + urlencode(
                {
                    "account_id": account,
                    "sender_id": provider_id,
                    "limit": 100,
                }
            )
        )
        if not isinstance(result.get("items"), list):
            raise ValueError("Cannot verify replies; follow-up not sent.")
        self.ingest(account, result["items"], target=profile_url)
        if self.store.hold(profile_url, account):
            raise ValueError(
                "Recipient replied or opted out. Pending follow-ups are paused; not sent."
            )
