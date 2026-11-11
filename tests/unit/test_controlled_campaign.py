"""Controlled live checks cannot auto-approve contacts or inherit investor claims."""

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from linkedin_automation.application.automatic_review import automatic_profile_review
from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.domain.services import process_candidate, qualification_assessment
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_controlled_contact_requires_manual_test_category_and_conservative_limits():
    definition = YamlSearchDefinitionLoader().load(Path("config/zz-controlled-queue-test.yaml"))
    candidate = LeadCandidate(
        full_name="Example Person",
        profile_url="https://www.linkedin.com/in/test-example/",
        headline="Controlled outreach test",
        company="Visible Example Company",
        current_title="Developer",
        location="Visible Example City",
    )
    lead = process_candidate(candidate, definition, uuid4())
    assert lead is not None
    assert automatic_profile_review(lead, definition).status == OutreachStatus.NEEDS_VERIFICATION
    assessment = qualification_assessment(
        lead, definition, firm_type="authorized test contact", funds_gbp=None, fund_evidence_url=""
    )
    assert assessment["blockers"] == [] and assessment["require_fund_evidence"] is False
    assert definition.limits.max_daily_messages == definition.limits.max_daily_connections == 1
    assert definition.search.connection_degrees == ["1st"]


def test_required_company_and_industry_recognize_actual_matches():
    from linkedin_automation.domain.search_definition import SearchCriteria

    definition = YamlSearchDefinitionLoader().load(Path("config/zz-controlled-queue-test.yaml"))
    definition = definition.model_copy(
        update={
            "search": SearchCriteria(
                companies=["Visible Company"],
                industries=["Software"],
                required_dimensions=["companies", "industries"],
            )
        }
    )
    candidate = LeadCandidate(
        full_name="Example",
        profile_url="https://www.linkedin.com/in/example/",
        company="Visible Company",
        industry="Software",
    )
    assert process_candidate(candidate, definition, uuid4()) is not None
    assert (
        process_candidate(replace(candidate, company="Different Company"), definition, uuid4())
        is None
    )
    assert (
        process_candidate(replace(candidate, industry="Different Industry"), definition, uuid4())
        is None
    )
