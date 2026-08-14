"""Tests for pure lead normalization, matching, and lifecycle policies."""

from pathlib import Path
from uuid import uuid4

import pytest

from linkedin_automation.domain.enums import OutreachStatus, RunStateError, RunStatus
from linkedin_automation.domain.models import LeadCandidate, ResearchRun
from linkedin_automation.domain.services import (
    LeadProcessingError,
    canonicalize_linkedin_profile_url,
    create_outreach_record,
    process_candidate,
    qualify_outreach_record,
)
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_canonicalizes_linkedin_profile_url() -> None:
    assert (
        canonicalize_linkedin_profile_url("http://linkedin.com/in/Ada-Example/?trk=search#details")
        == "https://www.linkedin.com/in/Ada-Example/"
    )


@pytest.mark.parametrize("url", ["", "https://example.com/in/person", "linkedin.com/company/acme"])
def test_rejects_non_profile_urls(url: str) -> None:
    with pytest.raises(LeadProcessingError):
        canonicalize_linkedin_profile_url(url)


def test_processes_matching_candidate_and_rejects_exclusion() -> None:
    definition = YamlSearchDefinitionLoader().load(Path("config/search.example.yaml"))
    run_id = uuid4()
    accepted = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/ada-example",
            full_name=" Ada  Example ",
            headline="AI Engineer working on LLM systems",
            location="India",
        ),
        definition,
        run_id,
    )
    excluded = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/student-example",
            full_name="Student Example",
            headline="AI Engineer Intern",
            location="India",
        ),
        definition,
        run_id,
    )
    assert accepted is not None
    assert accepted.full_name == "Ada Example"
    assert "title:AI Engineer" in accepted.evidence.matched
    assert excluded is None


def test_enforces_run_state_transitions() -> None:
    run = ResearchRun.create(
        search_name="test",
        schema_version=1,
        definition_json="{}",
        definition_hash="0" * 64,
        dry_run=False,
    )
    assert run.transition(RunStatus.COLLECTING).status == RunStatus.COLLECTING
    with pytest.raises(RunStateError):
        run.transition(RunStatus.COMPLETED)


def test_strict_bluqq_matching_and_evidence_gated_outreach() -> None:
    definition = YamlSearchDefinitionLoader().load(
        Path("config/bluqq-london-prop-family-offices.yaml")
    )
    run_id = uuid4()
    location_only = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/london-analyst",
            full_name="London Analyst",
            headline="Investment analyst at a family office",
            location="London",
        ),
        definition,
        run_id,
    )
    lead = process_candidate(
        LeadCandidate(
            profile_url="https://linkedin.com/in/founder-example",
            full_name="Alex Founder",
            headline="Founder of a family office",
            current_title="Founder",
            company="Example Capital",
            location="London",
        ),
        definition,
        run_id,
    )
    assert location_only is None
    assert lead is not None
    record = create_outreach_record(lead, definition)
    assert record.score == 55
    assert "BluQQ" in record.connection_message
    with pytest.raises(LeadProcessingError, match="minimum-funds"):
        qualify_outreach_record(
            record,
            definition,
            firm_type="family office",
            funds_gbp=None,
            fund_evidence_url="",
            fund_evidence_notes="",
            connection_message=record.connection_message,
            follow_up_message=record.follow_up_message,
            requested_status=OutreachStatus.APPROVED,
        )
    approved = qualify_outreach_record(
        record,
        definition,
        firm_type="family office",
        funds_gbp=1_000_000,
        fund_evidence_url="https://example.com/funds",
        fund_evidence_notes="Public filing",
        connection_message=record.connection_message,
        follow_up_message=record.follow_up_message,
        requested_status=OutreachStatus.APPROVED,
    )
    assert approved.score == 85
    with pytest.raises(LeadProcessingError, match="connection request"):
        qualify_outreach_record(
            approved,
            definition,
            firm_type=approved.firm_type,
            funds_gbp=approved.funds_gbp,
            fund_evidence_url=approved.fund_evidence_url,
            fund_evidence_notes=approved.fund_evidence_notes,
            connection_message=approved.connection_message,
            follow_up_message=approved.follow_up_message,
            requested_status=OutreachStatus.CONNECTED,
        )
