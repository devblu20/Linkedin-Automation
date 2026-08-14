"""Explicit-click LinkedIn outreach orchestration with durable audit logs."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from linkedin_automation.application.exceptions import RunNotFoundError, WorkflowError
from linkedin_automation.application.ports.repository import ResearchRepository
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import ConnectionRequest, SentMessage, utc_now
from linkedin_automation.domain.search_definition import SearchDefinition


class LinkedInOutreachPort(Protocol):
    def send_connection(self, profile_url: str, note: str) -> None: ...
    def connection_is_accepted(self, profile_url: str) -> bool: ...
    def send_message(self, profile_url: str, content: str) -> None: ...


class OutboundOutreach:
    """Each send method handles only IDs selected by the current explicit request."""

    def __init__(self, repository: ResearchRepository, browser: LinkedInOutreachPort) -> None:
        self.repository, self.browser = repository, browser

    def queue(self, lead_ids: list[UUID]) -> int:
        changed = 0
        for lead_id in lead_ids:
            record = self._record(lead_id)
            if record.status in {OutreachStatus.READY_FOR_REVIEW, OutreachStatus.APPROVED}:
                self.repository.upsert_outreach(replace(record,
                    status=OutreachStatus.CONNECTION_QUEUED, updated_at=utc_now()))
                changed += 1
        return changed

    def send_connections(self, lead_ids: list[UUID]) -> int:
        sent = 0
        for lead_id in lead_ids:
            lead, record, definition = self._context(lead_id)
            if record.status != OutreachStatus.CONNECTION_QUEUED:
                continue
            self._limit(self.repository.count_connection_requests_since(self._day_start()),
                        definition.limits.max_daily_connections, "connection request")
            now = utc_now()
            try:
                self.browser.send_connection(lead.profile_url, record.connection_message)
                status, error = "sent", ""
            except Exception as exc:
                status, error = "failed", str(exc)
            self.repository.add_connection_request(ConnectionRequest(
                uuid4(), lead.id, lead.run_id, status, now, utc_now(), error))
            if error:
                raise WorkflowError(error)
            self.repository.upsert_outreach(replace(record,
                status=OutreachStatus.CONNECTION_SENT, updated_at=utc_now()))
            sent += 1
        return sent

    def check_connections(self, run_id: UUID) -> int:
        accepted = 0
        for record in self.repository.list_outreach(run_id):
            if record.status != OutreachStatus.CONNECTION_SENT:
                continue
            lead = self.repository.get_lead(record.lead_id)
            if lead and self.browser.connection_is_accepted(lead.profile_url):
                self.repository.upsert_outreach(replace(record,
                    status=OutreachStatus.MESSAGE_READY, updated_at=utc_now()))
                accepted += 1
        return accepted

    def send_messages(self, lead_ids: list[UUID]) -> int:
        sent = 0
        for lead_id in lead_ids:
            lead, record, definition = self._context(lead_id)
            if record.status != OutreachStatus.MESSAGE_READY:
                continue
            self._limit(self.repository.count_sent_messages_since(self._day_start()),
                        definition.limits.max_daily_messages, "message")
            content, now = record.follow_up_message.strip(), utc_now()
            if not content:
                raise WorkflowError("message draft is empty")
            try:
                self.browser.send_message(lead.profile_url, content)
                status, error = "sent", ""
            except Exception as exc:
                status, error = "failed", str(exc)
            self.repository.add_sent_message(SentMessage(
                uuid4(), lead.id, lead.run_id, content, status, now, error))
            if error:
                raise WorkflowError(error)
            self.repository.upsert_outreach(replace(record,
                status=OutreachStatus.MESSAGE_SENT, updated_at=utc_now()))
            sent += 1
        return sent

    def _record(self, lead_id: UUID):
        record = self.repository.get_outreach(lead_id)
        if record is None:
            raise RunNotFoundError(f"outreach lead not found: {lead_id}")
        return record

    def _context(self, lead_id: UUID):
        lead, record = self.repository.get_lead(lead_id), self._record(lead_id)
        if lead is None:
            raise RunNotFoundError(f"lead not found: {lead_id}")
        run = self.repository.get_run(lead.run_id)
        if run is None:
            raise RunNotFoundError(f"run not found: {lead.run_id}")
        return lead, record, SearchDefinition.model_validate_json(run.definition_json)

    @staticmethod
    def _day_start() -> datetime:
        return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _limit(current: int, maximum: int, action: str) -> None:
        if current >= maximum:
            raise WorkflowError(f"daily {action} limit reached ({maximum})")
