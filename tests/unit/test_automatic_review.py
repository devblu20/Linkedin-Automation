"""Automatic approval relies on complete, current role facts, never biography AUM."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from linkedin_automation.application.automatic_review import automatic_profile_review
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.domain.services import process_candidate
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def trader():
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-independent-traders.yaml")
    )
    lead = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/automatic-example",
            full_name="Example Trader",
            headline="Independent Quant Trader",
            current_title="Independent Quant Trader",
            company="Example Trading",
            location="London",
            self_employed=True,
        ),
        definition,
        uuid4(),
    )
    assert lead is not None
    return lead, definition


def test_complete_current_trader_can_auto_approve_without_inventing_funds():
    lead, definition = trader()
    record = automatic_profile_review(lead, definition)
    assert record.status == OutreachStatus.APPROVED
    assert record.funds_gbp is None and not record.fund_evidence_url
    assert "Automatically approved" in record.fund_evidence_notes


def test_missing_or_former_role_does_not_auto_approve():
    lead, definition = trader()
    assert (
        automatic_profile_review(replace(lead, current_title=""), definition).status
        == OutreachStatus.NEEDS_VERIFICATION
    )
    former = replace(
        lead,
        headline="Former Independent Quant Trader",
        current_title="Programme Manager",
        self_employed=None,
    )
    assert automatic_profile_review(former, definition).status == OutreachStatus.NEEDS_VERIFICATION


def test_funds_required_policy_cannot_use_institutional_aum_as_personal_money():
    lead, definition = trader()
    policy = definition.qualification.model_copy(
        update={"require_fund_evidence": True, "minimum_funds_gbp": 1_000_000}
    )
    definition = definition.model_copy(update={"qualification": policy})
    lead = replace(lead, about="Institutional portfolio with EUR 400m AUM")
    result = automatic_profile_review(lead, definition)
    assert result.status == OutreachStatus.NEEDS_VERIFICATION and result.funds_gbp is None
