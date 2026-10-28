"""Automatic review from explicit profile facts and the saved campaign policy."""

import re
from dataclasses import replace

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import Lead, OutreachRecord
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import create_outreach_record


def automatic_review_blockers(lead: Lead, definition: SearchDefinition) -> list[str]:
    blockers = []
    if not lead.current_title or not lead.company or not lead.location:
        blockers.append(
            "Current role, company and location must be visible before automatic approval"
        )
    policy = definition.qualification
    role = lead.current_title.casefold()
    target = bool(
        policy and any(title.casefold() in role for title in policy.executive_title_priority)
    )
    independent = (
        r"\bindependent(?:\s+(?:quant|stock|crypto|options|futures|forex|financial))"
        r"{0,2}\s+trader\b"
    )
    target = target or bool(re.search(independent, role))
    current_identity = (
        lead.self_employed is True
        and re.search(independent, lead.headline, re.I)
        and not re.search(r"\b(?:former|previous|past|ex-)\b", lead.headline, re.I)
    )
    if not target and not current_identity:
        blockers.append(
            "A current target role or explicit current independent-trader identity "
            "is not established"
        )
    if policy and policy.require_fund_evidence:
        blockers.append("This campaign requires human verification of funds evidence")
    return blockers


def automatic_profile_review(lead: Lead, definition: SearchDefinition) -> OutreachRecord:
    record = create_outreach_record(lead, definition)
    # Funds-required campaigns cannot auto-approve an unverified balance. An AUM
    # amount in a biography must never become personal or available trading funds.
    if record.status == OutreachStatus.READY_FOR_REVIEW and not automatic_review_blockers(
        lead, definition
    ):
        return replace(
            record,
            status=OutreachStatus.APPROVED,
            fund_evidence_notes=(
                "Automatically approved from visible profile evidence and the saved "
                f"campaign rules. Profile source: {lead.profile_url}. "
                "No personal funds or institutional AUM have been inferred."
            ),
        )
    return replace(record, status=OutreachStatus.NEEDS_VERIFICATION)
