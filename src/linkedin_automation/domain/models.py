"""Framework-independent records used across the research workflow."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from linkedin_automation.domain.enums import OutreachStatus, RunStatus, ensure_transition


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MatchEvidence:
    """Human-readable reasons why a candidate was accepted."""

    matched: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LeadCandidate:
    """A raw profile summary observed by an external collection adapter."""

    profile_url: str
    full_name: str
    headline: str = ""
    current_title: str = ""
    company: str = ""
    location: str = ""
    industry: str = ""
    company_size_min: int | None = None
    company_size_max: int | None = None
    self_employed: bool | None = None
    about: str = ""
    experience: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    connections: str = ""
    followers: str = ""
    profile_snapshot: str = ""
    source_search: str = ""
    collected_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Lead:
    """A normalized and accepted lead."""

    id: UUID
    run_id: UUID
    profile_url: str
    full_name: str
    headline: str
    current_title: str
    company: str
    location: str
    industry: str
    evidence: MatchEvidence
    source_search: str
    first_observed_at: datetime
    last_observed_at: datetime
    notes: str = ""
    company_size_min: int | None = None
    company_size_max: int | None = None
    self_employed: bool | None = None
    about: str = ""
    experience: tuple[str, ...] = ()
    education: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    connections: str = ""
    followers: str = ""
    profile_snapshot: str = ""


@dataclass(frozen=True, slots=True)
class Observation:
    """Immutable raw collection evidence for one candidate."""

    id: UUID
    run_id: UUID
    candidate: LeadCandidate
    accepted: bool
    canonical_url: str | None
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class RunCounters:
    """Reconciled counters for a research run."""

    observed: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicated: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class ResearchRun:
    """Durable aggregate describing one research execution."""

    id: UUID
    search_name: str
    schema_version: int
    definition_json: str
    definition_hash: str
    status: RunStatus
    dry_run: bool
    created_at: datetime
    updated_at: datetime
    counters: RunCounters = field(default_factory=RunCounters)
    checkpoint_page: int = 0
    report_path: str | None = None
    drive_file_id: str | None = None
    drive_url: str | None = None
    error_category: str | None = None
    error_message: str | None = None

    @classmethod
    def create(
        cls,
        *,
        search_name: str,
        schema_version: int,
        definition_json: str,
        definition_hash: str,
        dry_run: bool,
    ) -> ResearchRun:
        """Create a pending run with a stable UUID and UTC timestamps."""
        now = utc_now()
        return cls(
            id=uuid4(),
            search_name=search_name,
            schema_version=schema_version,
            definition_json=definition_json,
            definition_hash=definition_hash,
            status=RunStatus.PENDING,
            dry_run=dry_run,
            created_at=now,
            updated_at=now,
        )

    def transition(self, target: RunStatus) -> ResearchRun:
        """Return a copy in a valid next lifecycle state."""
        ensure_transition(self.status, target)
        return replace(self, status=target, updated_at=utc_now())


@dataclass(frozen=True, slots=True)
class CollectionBatch:
    """Candidates and checkpoint returned by a collection adapter."""

    candidates: tuple[LeadCandidate, ...]
    checkpoint_page: int
    complete: bool


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Identity of an uploaded report artifact."""

    file_id: str
    web_url: str


@dataclass(frozen=True, slots=True)
class OutreachRecord:
    """Qualification evidence, draft copy, and human-review state for one lead."""

    lead_id: UUID
    run_id: UUID
    firm_type: str = ""
    funds_gbp: int | None = None
    fund_evidence_url: str = ""
    fund_evidence_notes: str = ""
    score: int = 0
    status: OutreachStatus = OutreachStatus.NEEDS_VERIFICATION
    connection_message: str = ""
    follow_up_message: str = ""
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ConnectionRequest:
    """Auditable result of one explicitly authorized connection request."""

    id: UUID
    lead_id: UUID
    run_id: UUID
    status: str
    requested_at: datetime
    updated_at: datetime
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class SentMessage:
    """Auditable result of one explicitly authorized LinkedIn message."""

    id: UUID
    lead_id: UUID
    run_id: UUID
    content: str
    status: str
    sent_at: datetime
    error_message: str = ""
