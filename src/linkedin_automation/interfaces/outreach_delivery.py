"""Durable, explicitly authorized delivery work; never infer a successful send."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from linkedin_automation.application.automatic_review import automatic_review_blockers
from linkedin_automation.application.outreach import OutreachReview
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import utc_now
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.infrastructure.persistence.repository import (
    ConnectionRequestRow,
    OutreachRow,
    SentMessageRow,
)


def draft_hash(content):
    return hashlib.sha256(content.encode()).hexdigest()


class AuthorizeDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_hash: str = Field(min_length=64, max_length=64)
    confirmed: bool
    include_follow_up: bool = False


class DeliveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=48, max_length=48)
    outcome: str = Field(pattern="^(pending|connected|not_connected|message_sent|uncertain)$")
    proof: str = Field(min_length=10, max_length=500)


class NotSentResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool
    note: str = Field(min_length=10, max_length=1000)


class DeliveryQueue:
    def __init__(self, path: Path, repository):
        self.path, self.repository = path, repository
        self.review = OutreachReview(repository)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS delivery_jobs (
                id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, profile_url TEXT NOT NULL,
                action TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                authorized INTEGER NOT NULL DEFAULT 0, token TEXT, created REAL NOT NULL,
                due REAL NOT NULL, started REAL, finished REAL, result TEXT,
                completion_hash TEXT)""")

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as db:
            db.row_factory = sqlite3.Row
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db

    def prepare(self):
        count = 0
        leads = {x.id: x for x in self.repository.list_leads()}
        already = {
            x.profile_url
            for x in leads.values()
            if (r := self.repository.get_outreach(x.id))
            and r.status
            in {
                OutreachStatus.CONNECTION_SENT,
                OutreachStatus.CONNECTED,
                OutreachStatus.MESSAGE_READY,
                OutreachStatus.MESSAGE_SENT,
            }
        }
        for record in self.repository.list_outreach():
            lead = leads[record.lead_id]
            definition = SearchDefinition.model_validate_json(
                self.repository.get_run(lead.run_id).definition_json
            )
            if (
                record.status == OutreachStatus.READY_FOR_REVIEW
                and os.getenv("AUTOMATIC_QUALIFICATION", "0") == "1"
                and lead.company
                and lead.current_title
                and definition.qualification
                and not definition.qualification.require_fund_evidence
                and not self.review.assessment(lead.id)["blockers"]
                and not automatic_review_blockers(lead, definition)
            ):
                record = replace(record, status=OutreachStatus.APPROVED, updated_at=utc_now())
                self.repository.upsert_outreach(record)
            if record.status not in {
                OutreachStatus.APPROVED,
                OutreachStatus.CONNECTED,
                OutreachStatus.MESSAGE_READY,
            }:
                continue
            connection = record.status == OutreachStatus.APPROVED
            if (connection and lead.profile_url in already) or self.review.assessment(lead.id)[
                "blockers"
            ]:
                continue
            if (
                not definition.outreach.enabled
                or not (
                    record.connection_message if connection else record.follow_up_message
                ).strip()
            ):
                continue
            payload = {
                "name": lead.full_name,
                "profile_url": lead.profile_url,
                "draft": record.connection_message,
                "follow_up": record.follow_up_message,
                "follow_up_authorized": False,
                "run_id": str(lead.run_id),
                "daily_connections": definition.limits.max_daily_connections,
                "daily_messages": definition.limits.max_daily_messages,
                "follow_up_hash": draft_hash(record.follow_up_message),
                "origin_created": time.time(),
            }
            action = "connection" if connection else "message"
            if not connection:
                payload["draft"] = record.follow_up_message
            with self.db() as db:
                if db.execute(
                    "SELECT 1 FROM delivery_jobs WHERE profile_url=? "
                    "AND action=? AND status!='cancelled'",
                    (lead.profile_url, action),
                ).fetchone():
                    continue
                self._insert(db, lead.id, action, payload, False)
            if connection:
                self.repository.upsert_outreach(
                    replace(record, status=OutreachStatus.CONNECTION_QUEUED, updated_at=utc_now())
                )
            count += 1
        return count

    def _insert(self, db, lead_id, action, payload, authorized, due=None):
        identifier = str(uuid4())
        db.execute(
            "INSERT INTO delivery_jobs "
            "(id,lead_id,profile_url,action,payload,status,authorized,created,due) "
            "VALUES (?,?,?,?,?,'queued',?,?,?)",
            (
                identifier,
                str(lead_id),
                payload["profile_url"],
                action,
                json.dumps(payload),
                int(authorized),
                time.time(),
                due or time.time(),
            ),
        )
        return identifier

    def active_for(self, lead_id):
        with self.db() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM delivery_jobs WHERE lead_id=? AND authorized=1 "
                    "AND status IN ('queued','running') AND action!='check'",
                    (str(lead_id),),
                ).fetchone()
            )

    def list(self):
        with self.db() as db:
            db.execute(
                "UPDATE delivery_jobs SET status='uncertain',result=? "
                "WHERE status='running' AND started<?",
                (
                    json.dumps({"proof": "Worker interrupted. Verify LinkedIn before any retry."}),
                    time.time() - 1800,
                ),
            )
            return [
                {
                    "id": r["id"],
                    "lead_id": r["lead_id"],
                    "action": r["action"],
                    "status": r["status"],
                    "authorized": bool(r["authorized"]),
                    "payload": json.loads(r["payload"]),
                    "result": json.loads(r["result"]) if r["result"] else None,
                    "draft_hash": draft_hash(json.loads(r["payload"])["draft"]),
                }
                for r in db.execute("SELECT * FROM delivery_jobs ORDER BY created DESC LIMIT 100")
            ]

    def authorize(self, identifier, values):
        if not values.confirmed:
            raise ValueError(
                "Confirm the named recipient and exact message before enabling delivery"
            )
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM delivery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None or row["status"] != "queued" or row["action"] == "check":
                raise ValueError("Delivery is no longer awaiting authorization")
            payload = json.loads(row["payload"])
            if draft_hash(payload["draft"]) != values.draft_hash:
                raise ValueError("Draft changed; refresh and review it again")
            record = self.repository.get_outreach(UUID(row["lead_id"]))
            actual = (
                record.connection_message
                if row["action"] == "connection"
                else record.follow_up_message
            )
            if draft_hash(actual) != values.draft_hash:
                raise ValueError("Saved draft changed after queueing; cancel and prepare again")
            payload["follow_up_authorized"] = values.include_follow_up
            db.execute(
                "UPDATE delivery_jobs SET authorized=1,payload=? WHERE id=?",
                (json.dumps(payload), str(identifier)),
            )

    def cancel(self, identifier):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM delivery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if row is None or row["status"] != "queued":
                raise ValueError("Only an unstarted delivery can be cancelled")
            db.execute("UPDATE delivery_jobs SET status='cancelled' WHERE id=?", (str(identifier),))
            record = self.repository.get_outreach(UUID(row["lead_id"]))
            if row["action"] == "connection" and record.status == OutreachStatus.CONNECTION_QUEUED:
                self.repository.upsert_outreach(
                    replace(record, status=OutreachStatus.APPROVED, updated_at=utc_now())
                )
            elif row["action"] == "message" and record.status == OutreachStatus.MESSAGE_READY:
                self.repository.upsert_outreach(
                    replace(record, status=OutreachStatus.CONNECTED, updated_at=utc_now())
                )

    def claim(self):
        with self.db() as db:
            db.execute(
                "UPDATE delivery_jobs SET status='uncertain',result=? "
                "WHERE status='running' AND started<?",
                (
                    json.dumps({"proof": "Delivery worker interrupted. Verify LinkedIn manually."}),
                    time.time() - 1800,
                ),
            )
            self._resolve_manual(db)
            if db.execute("SELECT 1 FROM delivery_jobs WHERE status='uncertain'").fetchone():
                return None
            if (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_jobs'"
                ).fetchone()
                and db.execute("SELECT 1 FROM search_jobs WHERE status='running'").fetchone()
            ):
                return None
            if db.execute("SELECT 1 FROM delivery_jobs WHERE status='running'").fetchone():
                return None
            row = db.execute(
                "SELECT * FROM delivery_jobs WHERE status='queued' AND authorized=1 AND due<=? "
                "ORDER BY created LIMIT 1",
                (time.time(),),
            ).fetchone()
            if not row:
                return None
            payload = json.loads(row["payload"])
            record = self.repository.get_outreach(UUID(row["lead_id"]))
            expected = {
                "connection": {OutreachStatus.CONNECTION_QUEUED},
                "check": {OutreachStatus.CONNECTION_SENT},
                "message": {OutreachStatus.CONNECTED, OutreachStatus.MESSAGE_READY},
            }[row["action"]]
            actual = (
                record.connection_message
                if row["action"] == "connection"
                else record.follow_up_message
            )
            if record.status not in expected or (
                row["action"] != "check" and actual != payload["draft"]
            ):
                db.execute(
                    "UPDATE delivery_jobs SET status='cancelled',result=? WHERE id=?",
                    (json.dumps({"proof": "Lead state or draft changed; not sent."}), row["id"]),
                )
                return None
            start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
            if (
                row["action"] == "connection"
                and self.repository.count_connection_requests_since(start)
                >= payload["daily_connections"]
            ):
                return None
            if (
                row["action"] == "message"
                and self.repository.count_sent_messages_since(start) >= payload["daily_messages"]
            ):
                return None
            token = secrets.token_hex(24)
            db.execute(
                "UPDATE delivery_jobs SET status='running',started=?,token=? WHERE id=?",
                (time.time(), token, row["id"]),
            )
            if row["action"] == "message":
                self.repository.upsert_outreach(
                    replace(record, status=OutreachStatus.MESSAGE_READY, updated_at=utc_now())
                )
            return {
                "id": row["id"],
                "lead_id": row["lead_id"],
                "action": row["action"],
                "token": token,
                **payload,
            }

    def complete(self, identifier, result):
        signature = draft_hash(result.model_dump_json())
        with self.db() as db:
            job = db.execute(
                "SELECT * FROM delivery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if job and secrets.compare_digest(job["completion_hash"] or "", signature):
                return
            if (
                not job
                or job["status"] not in {"running", "uncertain"}
                or not secrets.compare_digest(job["token"] or "", result.token)
            ):
                raise ValueError("Invalid delivery claim")
            allowed = {
                "connection": {"pending", "connected", "uncertain"},
                "check": {"connected", "not_connected", "uncertain"},
                "message": {"message_sent", "uncertain"},
            }[job["action"]]
            if result.outcome not in allowed:
                raise ValueError("Outcome does not match the claimed action")
            payload = json.loads(job["payload"])
            if result.outcome != "uncertain":
                self._apply_confirmation(job, payload, result)
            db.execute(
                "UPDATE delivery_jobs SET status=?,finished=?,result=?,completion_hash=?,"
                "token=NULL WHERE id=?",
                (
                    "uncertain" if result.outcome == "uncertain" else "completed",
                    time.time(),
                    result.model_dump_json(exclude={"token"}),
                    signature,
                    str(identifier),
                ),
            )
            if result.outcome in {"pending", "not_connected"}:
                if time.time() - payload.get("origin_created", job["created"]) < 14 * 86400:
                    self._insert(
                        db, UUID(job["lead_id"]), "check", payload, True, time.time() + 21600
                    )
            elif result.outcome == "connected":
                record = self.repository.get_outreach(UUID(job["lead_id"]))
                if record.follow_up_message:
                    follow = {**payload, "draft": record.follow_up_message}
                    approved = (
                        payload["follow_up_authorized"]
                        and draft_hash(record.follow_up_message) == payload["follow_up_hash"]
                    )
                    self._insert(db, UUID(job["lead_id"]), "message", follow, approved)

    def _resolve_manual(self, db):
        for job in db.execute("SELECT * FROM delivery_jobs WHERE status='uncertain'").fetchall():
            record = self.repository.get_outreach(UUID(job["lead_id"]))
            events = self.repository.list_manual_activities(UUID(job["lead_id"]))
            action = (
                "connection_sent"
                if job["action"] == "connection"
                else "message_sent"
                if job["action"] == "message"
                else "connected"
            )
            if not any(
                item["action"] == action and item["created_at"].timestamp() >= job["created"]
                for item in events
            ):
                continue
            db.execute(
                "UPDATE delivery_jobs SET status='resolved',token=NULL,result=? WHERE id=?",
                (
                    json.dumps({"proof": "Outcome recorded manually; not verified by the app."}),
                    job["id"],
                ),
            )
            if job["action"] == "connection" and record.status == OutreachStatus.CONNECTION_SENT:
                self._insert(
                    db,
                    UUID(job["lead_id"]),
                    "check",
                    json.loads(job["payload"]),
                    True,
                    time.time() + 21600,
                )
            elif (
                job["action"] in {"connection", "check"}
                and record.status == OutreachStatus.CONNECTED
            ):
                payload = json.loads(job["payload"])
                payload["draft"] = record.follow_up_message
                authorized = (
                    payload["follow_up_authorized"]
                    and draft_hash(record.follow_up_message) == payload["follow_up_hash"]
                )
                self._insert(db, UUID(job["lead_id"]), "message", payload, authorized)

    def resolve_not_sent(self, identifier, values):
        if not values.confirmed or len(values.note.strip()) < 10:
            raise ValueError("Confirm that you checked LinkedIn and that no send occurred")
        with self.db() as db:
            job = db.execute(
                "SELECT * FROM delivery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if (
                not job
                or job["status"] != "uncertain"
                or job["action"] not in {"connection", "message"}
            ):
                raise ValueError("Only an unconfirmed sending action can be resolved as not sent")
            record = self.repository.get_outreach(UUID(job["lead_id"]))
            expected = {
                "connection": {OutreachStatus.CONNECTION_QUEUED},
                "message": {OutreachStatus.MESSAGE_READY, OutreachStatus.CONNECTED},
            }[job["action"]]
            if record.status not in expected:
                raise ValueError("An actual outcome has already been recorded; refresh the queue")
            db.execute(
                "UPDATE delivery_jobs SET status='cancelled',token=NULL,result=? WHERE id=?",
                (
                    json.dumps(
                        {
                            "proof": "Reviewer confirmed no send occurred: " + values.note,
                            "recorded_manually": True,
                        }
                    ),
                    str(identifier),
                ),
            )
            status = (
                OutreachStatus.APPROVED
                if job["action"] == "connection"
                else OutreachStatus.CONNECTED
            )
            self.repository.upsert_outreach(replace(record, status=status, updated_at=utc_now()))

    def _apply_confirmation(self, job, payload, result):
        # Stable receipt IDs make a crash between the two database commits safe.
        receipt = str(uuid5(UUID(job["id"]), "delivery-receipt"))
        with Session(self.repository._engine) as session:
            if self.repository._engine.dialect.name == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(OutreachRow, job["lead_id"])
            now = utc_now()
            if result.outcome == "pending":
                existing = session.scalar(
                    select(ConnectionRequestRow)
                    .where(
                        ConnectionRequestRow.lead_id == row.lead_id,
                        ConnectionRequestRow.status.in_(["sent", "accepted"]),
                    )
                    .limit(1)
                )
                if not session.get(ConnectionRequestRow, receipt) and not existing:
                    session.add(
                        ConnectionRequestRow(
                            id=receipt,
                            lead_id=row.lead_id,
                            run_id=row.run_id,
                            status="sent",
                            requested_at=now,
                            updated_at=now,
                            error_message="",
                        )
                    )
                if row.status == OutreachStatus.CONNECTION_QUEUED:
                    row.status = OutreachStatus.CONNECTION_SENT
            elif result.outcome == "connected":
                request = session.scalar(
                    select(ConnectionRequestRow)
                    .where(
                        ConnectionRequestRow.lead_id == row.lead_id,
                        ConnectionRequestRow.status == "sent",
                    )
                    .order_by(ConnectionRequestRow.requested_at.desc())
                    .limit(1)
                )
                if request:
                    request.status = "accepted"
                    request.updated_at = now
                if row.status in {OutreachStatus.CONNECTION_SENT, OutreachStatus.CONNECTION_QUEUED}:
                    row.status = OutreachStatus.CONNECTED
            elif result.outcome == "message_sent":
                existing = session.scalar(
                    select(SentMessageRow)
                    .where(SentMessageRow.lead_id == row.lead_id, SentMessageRow.status == "sent")
                    .limit(1)
                )
                if not session.get(SentMessageRow, receipt) and not existing:
                    session.add(
                        SentMessageRow(
                            id=receipt,
                            lead_id=row.lead_id,
                            run_id=row.run_id,
                            content=payload["draft"],
                            status="sent",
                            sent_at=now,
                            error_message="",
                        )
                    )
                if row.status in {OutreachStatus.MESSAGE_READY, OutreachStatus.CONNECTED}:
                    row.status = OutreachStatus.MESSAGE_SENT
            row.updated_at = now
            session.commit()


def install_delivery(app, root, repository):
    queue = DeliveryQueue(root / "search-jobs.sqlite3", repository)
    app.state.delivery_queue = queue

    @app.get("/api/outreach/jobs")
    def jobs():
        return {
            "jobs": queue.list(),
            "sending_requires_authorization": True,
            "automatic_qualification": os.getenv("AUTOMATIC_QUALIFICATION", "0") == "1",
            "automatic_queue": os.getenv("AUTOMATIC_QUEUE", "0") == "1",
        }

    @app.post("/api/outreach/prepare")
    def prepare():
        return {"prepared": queue.prepare()}

    @app.post("/api/outreach/jobs/{identifier}/authorize")
    def authorize(identifier: UUID, payload: AuthorizeDelivery):
        try:
            queue.authorize(identifier, payload)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/outreach/jobs/{identifier}/cancel")
    def cancel(identifier: UUID):
        try:
            queue.cancel(identifier)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/outreach/jobs/{identifier}/check-now")
    def check_now(identifier: UUID):
        with queue.db() as db:
            row = db.execute(
                "SELECT * FROM delivery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
            if not row or row["action"] != "check" or row["status"] not in {"queued", "uncertain"}:
                raise HTTPException(409, "No pending acceptance check")
            db.execute(
                "UPDATE delivery_jobs SET due=?,status='queued',token=NULL WHERE id=?",
                (time.time(), str(identifier)),
            )
        return {"ok": True}

    @app.post("/api/outreach/jobs/{identifier}/resolve-not-sent")
    def resolve_not_sent(identifier: UUID, payload: NotSentResolution):
        try:
            queue.resolve_not_sent(identifier, payload)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/outreach/claim")
    def claim():
        return {"job": queue.claim()}

    @app.post("/api/outreach/jobs/{identifier}/complete")
    def complete(identifier: UUID, payload: DeliveryResult):
        try:
            queue.complete(identifier, payload)
            return {"ok": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    return queue
