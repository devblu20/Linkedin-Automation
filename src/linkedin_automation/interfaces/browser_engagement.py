"""Single-owner browser reply bridge. No Unipile client or credentials are used."""

import base64
import json
import os
import secrets
import time
from datetime import UTC, datetime
from threading import Lock

from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.reply_sync import ReplySync


def personal_sender():
    if os.getenv("PERSONAL_BROWSER_MODE") != "1" or os.getenv("WEBSITE_CLOUD_MODE") == "1":
        return None
    value = os.getenv("PERSONAL_LINKEDIN_PROFILE_URL", "")
    return canonicalize_linkedin_profile_url(value) if value else None


class BrowserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=500)
    chat: str = Field(min_length=1, max_length=2048)
    sent_at: str = Field(max_length=100)
    outgoing: bool
    content: str = Field(max_length=32000)

    @field_validator("sent_at")
    @classmethod
    def aware_time(cls, value):
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("An observed message needs a timezone")
        return date.astimezone(UTC).isoformat()


class ConversationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_url: str = Field(max_length=2048)
    messages: list[BrowserMessage] = Field(default_factory=list, max_length=100)
    complete: bool
    incoming_detected: bool


class BrowserObservations(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sender: str = Field(max_length=2048)
    conversations: list[ConversationObservation] = Field(max_length=5)


class SyncCompletion(BrowserObservations):
    id: str = Field(min_length=48, max_length=48)
    token: str = Field(min_length=48, max_length=48)
    error: str = Field(default="", max_length=250)


class BrowserReplyBridge:
    def __init__(self, queue, repository, store, ai):
        self.queue, self.repository, self.store, self.ai = queue, repository, store, ai
        self.last_attempt = 0
        # Reuse the existing provider-independent AI classification and opt-out protection.
        self.classification_lock = Lock()
        with queue.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS browser_reply_jobs (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, token TEXT, status TEXT NOT NULL,
                targets TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL)""")
        with store.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS engagement_browser_order (
                account TEXT NOT NULL, profile_url TEXT NOT NULL, message_id TEXT NOT NULL,
                snapshot TEXT NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY(account,message_id))""")

    def current(self):
        sender = personal_sender()
        if not sender:
            raise ValueError("Configure the personal LinkedIn sender before browser reply sync.")
        return "browser:" + sender

    def classify_pending(self, account):
        with self.classification_lock:
            ReplySync.classify_pending(self, account)

    def cycle(self, *, force=False):
        sender = self.current()
        state = self.store.sync_state(sender)
        if not force and (state.get("error") or time.monotonic() - self.last_attempt < 300):
            return
        self.last_attempt = time.monotonic()
        eligible = {
            r.lead_id
            for r in self.repository.list_outreach()
            if r.status
            in {
                OutreachStatus.CONNECTED,
                OutreachStatus.MESSAGE_READY,
                OutreachStatus.MESSAGE_SENT,
            }
        }
        leads = [x for x in self.repository.list_leads() if x.id in eligible]
        # Rotate a bounded batch, so a larger lead list is not silently truncated forever.
        with self.queue.db() as db:
            expired = db.execute(
                "UPDATE browser_reply_jobs SET status='failed' "
                "WHERE status='running' AND started<?",
                (time.time() - 600,),
            ).rowcount
            if expired:
                self.store.save_sync(
                    sender,
                    cursor=None,
                    error="Browser reply check expired. "
                    "Inspect LinkedIn and click Sync replies to retry.",
                )
                return
            if db.execute(
                "SELECT 1 FROM browser_reply_jobs "
                "WHERE sender=? AND status IN ('queued','running')",
                (sender,),
            ).fetchone():
                return
            previous = db.execute(
                "SELECT COUNT(*) FROM browser_reply_jobs WHERE sender=? AND status='completed'",
                (sender,),
            ).fetchone()[0]
            offset = (previous * 5) % len(leads) if leads else 0
            targets = [
                {"profile_url": x.profile_url, "name": x.full_name}
                for x in (leads[offset:] + leads[:offset])[:5]
            ]
            if targets:
                db.execute(
                    "INSERT INTO browser_reply_jobs VALUES (?,?,NULL,'queued',?,?,NULL,NULL)",
                    (secrets.token_hex(24), sender, json.dumps(targets), time.time()),
                )
        self.store.save_sync(sender, cursor="queued" if targets else None, error=None)

    def claim(self):
        self.cycle()
        with self.queue.db() as db:
            row = db.execute(
                "SELECT * FROM browser_reply_jobs WHERE sender=? "
                "AND status='queued' ORDER BY created LIMIT 1",
                (self.current(),),
            ).fetchone()
            if not row:
                return None
            token = secrets.token_hex(24)
            db.execute(
                "UPDATE browser_reply_jobs SET status='running',started=?,token=? WHERE id=?",
                (time.time(), token, row["id"]),
            )
            return {
                "id": row["id"],
                "token": token,
                "sender": personal_sender(),
                "targets": json.loads(row["targets"]),
            }

    def ingest(self, payload):
        if canonicalize_linkedin_profile_url(payload.sender) != personal_sender():
            raise ValueError("Browser sender changed; observations were not imported.")
        allowed = {x.profile_url for x in self.repository.list_leads()}
        # Validate the entire batch before persisting anything.
        for conversation in payload.conversations:
            if canonicalize_linkedin_profile_url(conversation.profile_url) not in allowed:
                raise ValueError("Conversation does not belong to a saved lead.")
            if any(
                canonicalize_linkedin_profile_url(m.chat) != conversation.profile_url
                for m in conversation.messages
            ):
                raise ValueError("Message conversation identity differs from its lead.")
        account = self.current()
        complete = True
        for conversation in payload.conversations:
            if conversation.incoming_detected:
                self.store.pause(account, conversation.profile_url, "Recipient replied")
            for message in conversation.messages:
                self.store.observe(account, conversation.profile_url, message.model_dump())
            if conversation.complete:
                snapshot = secrets.token_hex(16)
                with self.store.db() as db:
                    db.executemany(
                        "INSERT INTO engagement_browser_order VALUES (?,?,?,?,?) "
                        "ON CONFLICT(account,message_id) DO UPDATE SET "
                        "snapshot=excluded.snapshot,position=excluded.position",
                        [
                            (account, conversation.profile_url, m.id, snapshot, position)
                            for position, m in enumerate(conversation.messages)
                        ],
                    )
            complete &= conversation.complete
        self.store.save_sync(
            account,
            error=None
            if complete
            else "Browser conversation layout needs inspection; follow-ups remain blocked.",
        )
        return complete

    def complete(self, payload):
        with self.queue.db() as db:
            row = db.execute(
                "SELECT * FROM browser_reply_jobs WHERE id=?", (payload.id,)
            ).fetchone()
            if (
                not row
                or row["status"] != "running"
                or row["sender"] != self.current()
                or not secrets.compare_digest(row["token"] or "", payload.token)
            ):
                raise ValueError("Invalid browser sync claim.")
            targets = {x["profile_url"] for x in json.loads(row["targets"])}
        urls = {c.profile_url for c in payload.conversations}
        if not urls <= targets:
            raise ValueError("Browser sync returned an unrequested conversation.")
        success = self.ingest(payload) and not payload.error and urls == targets
        with self.queue.db() as db:
            db.execute(
                "UPDATE browser_reply_jobs SET status=?,finished=?,token=NULL WHERE id=?",
                ("completed" if success else "failed", time.time(), payload.id),
            )
        self.store.save_sync(
            self.current(),
            cursor=None,
            last_success=datetime.now(UTC).isoformat()
            if success
            else self.store.sync_state(self.current()).get("last_success"),
            error=None
            if success
            else "Browser reply check could not finish. "
            "Inspect LinkedIn and click Sync replies to retry.",
        )


def install_browser_engagement(app, queue, repository, store):
    bridge = BrowserReplyBridge(queue, repository, store, app.state.message_ai)
    app.state.browser_engagement = app.state.reply_sync = bridge

    def worker(request):
        expected = (
            "Basic "
            + base64.b64encode(
                (os.environ["DASHBOARD_USERNAME"] + ":" + os.environ["DASHBOARD_PASSWORD"]).encode()
            ).decode()
        )
        if (
            getattr(app.state, "website_user", None)
            or getattr(request.state, "website_admin", False)
            or getattr(request.state, "worker_device", None)
            or not secrets.compare_digest(request.headers.get("authorization", ""), expected)
        ):
            raise HTTPException(403, "Owner browser worker credentials are required.")

    @app.post("/api/outreach/browser/settings")
    def settings(request: Request):
        worker(request)
        return {"sender": personal_sender()}

    @app.post("/api/outreach/browser/sync/claim")
    def claim(request: Request):
        worker(request)
        return {"job": bridge.claim()}

    @app.post("/api/outreach/browser/observations")
    def observations(payload: BrowserObservations, request: Request, background: BackgroundTasks):
        worker(request)
        try:
            complete = bridge.ingest(payload)
            background.add_task(bridge.classify_pending, bridge.current())
            return {
                "complete": complete,
                "blocked": any(
                    store.hold(c.profile_url, bridge.current()) for c in payload.conversations
                ),
                "reply_states": {
                    c.profile_url: {
                        "hold": store.hold(c.profile_url, bridge.current()),
                        "signature": store.reply_signature(c.profile_url, bridge.current()),
                    }
                    for c in payload.conversations
                },
            }
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/outreach/browser/sync/complete")
    def complete(payload: SyncCompletion, request: Request, background: BackgroundTasks):
        worker(request)
        try:
            bridge.complete(payload)
            background.add_task(bridge.classify_pending, bridge.current())
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
