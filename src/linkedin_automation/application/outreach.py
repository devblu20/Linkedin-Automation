"""Human-reviewed firm qualification and outreach use cases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from linkedin_automation.application.exceptions import RunNotFoundError
from linkedin_automation.application.ports.repository import ResearchRepository
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import Lead, OutreachRecord
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import qualify_outreach_record


@dataclass(frozen=True, slots=True)
class OutreachItem:
    """A lead joined with its qualification and outreach state."""

    lead: Lead
    outreach: OutreachRecord


class OutreachReview:
    """List and update review records without performing LinkedIn actions."""

    def __init__(self, repository: ResearchRepository) -> None:
        self._repository = repository

    def list(self, run_id: UUID | None = None) -> list[OutreachItem]:
        leads = {lead.id: lead for lead in self._repository.list_leads(run_id)}
        return [
            OutreachItem(leads[record.lead_id], record)
            for record in self._repository.list_outreach(run_id)
            if record.lead_id in leads
        ]

    def update(
        self,
        lead_id: UUID,
        *,
        firm_type: str,
        funds_gbp: int | None,
        fund_evidence_url: str,
        fund_evidence_notes: str,
        connection_message: str,
        follow_up_message: str,
        status: OutreachStatus,
    ) -> OutreachRecord:
        lead = self._repository.get_lead(lead_id)
        record = self._repository.get_outreach(lead_id)
        if lead is None or record is None:
            raise RunNotFoundError(f"outreach lead not found: {lead_id}")
        run = self._repository.get_run(lead.run_id)
        if run is None:
            raise RunNotFoundError(f"run not found: {lead.run_id}")
        definition = SearchDefinition.model_validate_json(run.definition_json)
        updated = qualify_outreach_record(
            record,
            definition,
            firm_type=firm_type,
            funds_gbp=funds_gbp,
            fund_evidence_url=fund_evidence_url,
            fund_evidence_notes=fund_evidence_notes,
            connection_message=connection_message,
            follow_up_message=follow_up_message,
            requested_status=status,
        )
        self._repository.upsert_outreach(updated)
        return updated
