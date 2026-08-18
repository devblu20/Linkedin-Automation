"""Pure lead normalization, matching, and deduplication policies."""

from __future__ import annotations

import re
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from linkedin_automation.domain.enums import OutreachStatus
from linkedin_automation.domain.models import (
    Lead,
    LeadCandidate,
    MatchEvidence,
    OutreachRecord,
    utc_now,
)
from linkedin_automation.domain.search_definition import SearchDefinition

_WHITESPACE = re.compile(r"\s+")
_LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}


class LeadProcessingError(ValueError):
    """A candidate cannot be safely normalized."""


def normalize_text(value: str) -> str:
    """Collapse external whitespace and remove surrounding space."""
    return _WHITESPACE.sub(" ", value).strip()


def canonicalize_linkedin_profile_url(value: str) -> str:
    """Return a canonical HTTPS LinkedIn `/in/` profile URL."""
    raw = value.strip()
    if not raw:
        raise LeadProcessingError("profile URL is empty")
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").casefold()
    if host not in _LINKEDIN_HOSTS:
        raise LeadProcessingError("profile URL must use linkedin.com")
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0].casefold() != "in":
        raise LeadProcessingError("profile URL must identify a LinkedIn /in/ profile")
    path = f"/in/{path_parts[1]}/"
    return urlunsplit(("https", "www.linkedin.com", path, "", ""))


def _contains(text: str, value: str) -> bool:
    return value.casefold() in text.casefold()


def _matched_values(text: str, values: list[str], label: str) -> list[str]:
    return [f"{label}:{value}" for value in values if _contains(text, value)]


def evaluate_candidate(candidate: LeadCandidate, definition: SearchDefinition) -> MatchEvidence:
    """Evaluate a candidate using conservative include/exclude substring rules."""
    searchable = " ".join(
        (
            candidate.full_name,
            candidate.headline,
            candidate.current_title,
            candidate.company,
            candidate.location,
            candidate.industry,
        )
    )
    criteria = definition.search
    excluded = [
        *_matched_values(searchable, criteria.titles.exclude, "title"),
        *_matched_values(searchable, criteria.keywords.exclude, "keyword"),
    ]
    qualification = definition.qualification
    if (
        qualification is not None
        and candidate.self_employed is not True
        and candidate.company_size_max is not None
        and candidate.company_size_max > qualification.company_size_max
    ):
        excluded.append(f"company_size:over_{qualification.company_size_max}")
    matched = [
        *_matched_values(searchable, criteria.titles.include, "title"),
        *_matched_values(candidate.location, criteria.locations, "location"),
        *_matched_values(candidate.industry, criteria.industries, "industry"),
        *_matched_values(candidate.company, criteria.companies, "company"),
        *_matched_values(searchable, criteria.keywords.include, "keyword"),
    ]
    matched_dimensions = {item.split(":", 1)[0] + "s" for item in matched}
    missing_required = [
        dimension
        for dimension in criteria.required_dimensions
        if dimension not in matched_dimensions
    ]
    excluded.extend(f"required:{dimension}" for dimension in missing_required)
    return MatchEvidence(matched=tuple(matched), excluded=tuple(excluded))


def _safe_format(template: str, lead: Lead) -> str:
    first_name = lead.full_name.split(maxsplit=1)[0] if lead.full_name else "there"
    return template.format(first_name=first_name, full_name=lead.full_name, company=lead.company)


def create_outreach_record(lead: Lead, definition: SearchDefinition) -> OutreachRecord:
    """Create a conservative qualification record and editable outreach drafts."""
    qualification = definition.qualification
    if qualification is None:
        return OutreachRecord(lead_id=lead.id, run_id=lead.run_id)
    searchable = f"{lead.headline} {lead.current_title} {lead.company}".casefold()
    title_score = 0
    for index, title in enumerate(qualification.executive_title_priority):
        if title.casefold() in searchable:
            title_score = max(5, 15 - index)
            break
    firm_type = next(
        (value for value in qualification.firm_types if value.casefold() in searchable), ""
    )
    location_score = 15 if "london" in lead.location.casefold() else 0
    size_match = (
        lead.company_size_max is not None
        and lead.company_size_max <= qualification.company_size_max
        and (lead.company_size_min or 1) >= qualification.company_size_min
    )
    self_employed = lead.self_employed is True or any(
        value.casefold() in searchable for value in qualification.self_employment_keywords
    )
    score = min(
        100,
        title_score
        + (25 if firm_type else 0)
        + location_score
        + (20 if size_match else 0)
        + (25 if self_employed and qualification.prefer_self_employed else 0),
    )
    outreach = definition.outreach
    initial_status = (
        OutreachStatus.READY_FOR_REVIEW
        if score >= qualification.minimum_score
        else OutreachStatus.NEEDS_VERIFICATION
    )
    return OutreachRecord(
        lead_id=lead.id,
        run_id=lead.run_id,
        firm_type=firm_type,
        score=score,
        status=initial_status,
        connection_message=(
            _safe_format(outreach.connection_template, lead) if outreach.enabled else ""
        ),
        follow_up_message=(
            _safe_format(outreach.message_template, lead) if outreach.enabled else ""
        ),
    )


def qualify_outreach_record(
    record: OutreachRecord,
    definition: SearchDefinition,
    *,
    firm_type: str,
    funds_gbp: int | None,
    fund_evidence_url: str,
    fund_evidence_notes: str,
    connection_message: str,
    follow_up_message: str,
    requested_status: OutreachStatus,
) -> OutreachRecord:
    """Validate evidence, recalculate score, and enforce review-state safeguards."""
    qualification = definition.qualification
    if qualification is None:
        raise LeadProcessingError("run does not define qualification policy")
    evidence_valid = bool(fund_evidence_url.strip()) and funds_gbp is not None
    funds_match = (
        evidence_valid and funds_gbp is not None and funds_gbp >= qualification.minimum_funds_gbp
    )
    previous_funds_match = (
        record.funds_gbp is not None
        and bool(record.fund_evidence_url)
        and record.funds_gbp >= qualification.minimum_funds_gbp
    )
    base_score = record.score - (30 if previous_funds_match else 0)
    if bool(record.firm_type) != bool(firm_type.strip()):
        base_score += 25 if firm_type.strip() else -25
    base_score = min(70, max(0, base_score))
    score = min(100, base_score + (30 if funds_match else 0))
    if requested_status not in {OutreachStatus.NEEDS_VERIFICATION, OutreachStatus.REJECTED}:
        if qualification.require_fund_evidence and not funds_match:
            raise LeadProcessingError("verified minimum-funds evidence is required before approval")
        if score < qualification.minimum_score:
            raise LeadProcessingError("lead score is below the configured approval threshold")
    if requested_status == OutreachStatus.CONNECTION_QUEUED and record.status not in {
        OutreachStatus.READY_FOR_REVIEW,
        OutreachStatus.APPROVED,
        OutreachStatus.CONNECTION_QUEUED,
    }:
        raise LeadProcessingError("approve the lead before queueing a connection")
    if requested_status == OutreachStatus.CONNECTED and record.status not in {
        OutreachStatus.CONNECTION_SENT,
        OutreachStatus.CONNECTED,
    }:
        raise LeadProcessingError("record the connection request before marking it connected")
    if requested_status == OutreachStatus.MESSAGE_READY and record.status not in {
        OutreachStatus.CONNECTED,
        OutreachStatus.MESSAGE_READY,
    }:
        raise LeadProcessingError("record connection acceptance before preparing a message")
    if requested_status == OutreachStatus.MESSAGE_SENT and record.status not in {
        OutreachStatus.CONNECTED,
        OutreachStatus.MESSAGE_READY,
        OutreachStatus.MESSAGE_SENT,
    }:
        raise LeadProcessingError("record connection acceptance before marking a message sent")
    return replace(
        record,
        firm_type=normalize_text(firm_type),
        funds_gbp=funds_gbp,
        fund_evidence_url=normalize_text(fund_evidence_url),
        fund_evidence_notes=normalize_text(fund_evidence_notes),
        score=score,
        status=requested_status,
        connection_message=normalize_text(connection_message),
        follow_up_message=normalize_text(follow_up_message),
        updated_at=utc_now(),
    )


def process_candidate(
    candidate: LeadCandidate, definition: SearchDefinition, run_id: UUID
) -> Lead | None:
    """Normalize and accept a matching candidate, or return None."""
    evidence = evaluate_candidate(candidate, definition)
    if evidence.excluded or not evidence.matched:
        return None
    profile_url = canonicalize_linkedin_profile_url(candidate.profile_url)
    full_name = normalize_text(candidate.full_name)
    if not full_name:
        raise LeadProcessingError("candidate full name is empty")
    qualification = definition.qualification
    searchable = f"{candidate.headline} {candidate.current_title} {candidate.company}".casefold()
    inferred_self_employed = candidate.self_employed
    if inferred_self_employed is None and qualification is not None:
        inferred_self_employed = any(
            keyword.casefold() in searchable for keyword in qualification.self_employment_keywords
        )
    return Lead(
        id=uuid4(),
        run_id=run_id,
        profile_url=profile_url,
        full_name=full_name,
        headline=normalize_text(candidate.headline),
        current_title=normalize_text(candidate.current_title),
        company=normalize_text(candidate.company),
        location=normalize_text(candidate.location),
        industry=normalize_text(candidate.industry),
        company_size_min=candidate.company_size_min,
        company_size_max=candidate.company_size_max,
        self_employed=inferred_self_employed,
        about=normalize_text(candidate.about),
        experience=tuple(
            normalize_text(value) for value in candidate.experience if normalize_text(value)
        ),
        education=tuple(
            normalize_text(value) for value in candidate.education if normalize_text(value)
        ),
        skills=tuple(normalize_text(value) for value in candidate.skills if normalize_text(value)),
        connections=normalize_text(candidate.connections),
        followers=normalize_text(candidate.followers),
        profile_snapshot=normalize_text(candidate.profile_snapshot),
        evidence=evidence,
        source_search=normalize_text(candidate.source_search),
        first_observed_at=candidate.collected_at,
        last_observed_at=candidate.collected_at,
    )


def merge_duplicate(existing: Lead, incoming: Lead) -> Lead:
    """Merge duplicate observations deterministically, preserving first identity."""
    if existing.profile_url != incoming.profile_url:
        raise LeadProcessingError("only leads with the same canonical URL can be merged")
    return replace(
        existing,
        full_name=incoming.full_name or existing.full_name,
        headline=incoming.headline or existing.headline,
        current_title=incoming.current_title or existing.current_title,
        company=incoming.company or existing.company,
        location=incoming.location or existing.location,
        industry=incoming.industry or existing.industry,
        company_size_min=incoming.company_size_min or existing.company_size_min,
        company_size_max=incoming.company_size_max or existing.company_size_max,
        self_employed=(
            incoming.self_employed if incoming.self_employed is not None else existing.self_employed
        ),
        about=incoming.about or existing.about,
        experience=incoming.experience or existing.experience,
        education=incoming.education or existing.education,
        skills=incoming.skills or existing.skills,
        connections=incoming.connections or existing.connections,
        followers=incoming.followers or existing.followers,
        profile_snapshot=incoming.profile_snapshot or existing.profile_snapshot,
        evidence=MatchEvidence(
            matched=tuple(dict.fromkeys((*existing.evidence.matched, *incoming.evidence.matched))),
            excluded=(),
        ),
        source_search=incoming.source_search or existing.source_search,
        first_observed_at=min(existing.first_observed_at, incoming.first_observed_at),
        last_observed_at=max(existing.last_observed_at, incoming.last_observed_at),
    )
