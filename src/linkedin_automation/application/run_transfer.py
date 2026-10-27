"""Bounded transfer contract: profile data only, never credentials or approvals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from typing import Literal
from uuid import UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from linkedin_automation.domain.enums import OutreachStatus, RunStatus
from linkedin_automation.domain.models import (
    Lead,
    LeadCandidate,
    OutreachRecord,
    ResearchRun,
    RunCounters,
    utc_now,
)
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import create_outreach_record, process_candidate

MAX_TRANSFER_BYTES = 2 * 1024 * 1024


class ProfileTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=16_000)

    profile_url: str = Field(max_length=1024)
    full_name: str = Field(min_length=1, max_length=255)
    headline: str = ""
    current_title: str = ""
    company: str = ""
    location: str = ""
    industry: str = ""
    company_size_min: int | None = Field(default=None, ge=1)
    company_size_max: int | None = Field(default=None, ge=1)
    self_employed: bool | None = None
    about: str = ""
    experience: list[str] = Field(default_factory=list, max_length=100)
    education: list[str] = Field(default_factory=list, max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=100)
    connections: str = ""
    followers: str = ""
    source_search: str = ""
    collected_at: AwareDatetime

    def candidate(self) -> LeadCandidate:
        values = self.model_dump()
        for key in ("experience", "education", "skills"):
            values[key] = tuple(values[key])
        return LeadCandidate(**values)


class RunTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    run_id: UUID
    created_at: AwareDatetime
    definition: SearchDefinition
    profiles: list[ProfileTransfer] = Field(min_length=1, max_length=250)


def prepare_transfer(payload: RunTransfer) -> tuple[ResearchRun, list[tuple[Lead, OutreachRecord]]]:
    """Validate the entire batch before writes; recompute all qualification on import."""
    definition = payload.definition
    if definition.version != 2:
        raise ValueError("Cloud review requires a version 2 qualification campaign")
    serialized = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    rows: list[tuple[Lead, OutreachRecord]] = []
    seen: set[str] = set()
    for profile in payload.profiles:
        lead = process_candidate(profile.candidate(), definition, payload.run_id)
        if lead is None:
            raise ValueError("A transferred profile does not match the campaign rules")
        if lead.profile_url in seen:
            continue
        seen.add(lead.profile_url)
        lead = replace(lead, id=uuid5(payload.run_id, lead.profile_url))
        record = replace(
            create_outreach_record(lead, definition), status=OutreachStatus.NEEDS_VERIFICATION
        )
        rows.append((lead, record))
    run = ResearchRun(
        id=payload.run_id,
        search_name=definition.name,
        schema_version=definition.version,
        definition_json=serialized,
        definition_hash=hashlib.sha256(serialized.encode()).hexdigest(),
        status=RunStatus.COMPLETED,
        dry_run=False,
        created_at=payload.created_at,
        updated_at=utc_now(),
        counters=RunCounters(observed=len(rows), accepted=len(rows)),
    )
    return run, rows


def export_transfer(run: ResearchRun, leads: list[Lead]) -> RunTransfer:
    if run.dry_run or run.status != RunStatus.COMPLETED:
        raise ValueError("Only a completed, non-dry research run can be synced")
    profiles = []
    for lead in leads:
        values = asdict(lead)
        allowed = set(ProfileTransfer.model_fields) - {"collected_at"}
        values = {key: value for key, value in values.items() if key in allowed}
        profiles.append(ProfileTransfer(**values, collected_at=lead.last_observed_at))
    return RunTransfer(
        run_id=run.id,
        created_at=run.created_at,
        definition=SearchDefinition.model_validate_json(run.definition_json),
        profiles=profiles,
    )
