"""Transfer visible collected profiles; derive acceptance on the receiving server."""

import hashlib
import json
from dataclasses import asdict, replace
from uuid import uuid5

from linkedin_automation.application.automatic_review import automatic_profile_review
from linkedin_automation.application.run_transfer import ProfileTransfer, RunTransfer
from linkedin_automation.domain.enums import OutreachStatus, RunStatus
from linkedin_automation.domain.models import (
    Lead,
    Observation,
    OutreachRecord,
    ResearchRun,
    RunCounters,
    utc_now,
)
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import (
    canonicalize_linkedin_profile_url,
    create_outreach_record,
    evaluate_candidate,
    process_candidate,
)


def prepare_research_transfer(
    payload: RunTransfer,
    *,
    automatic: bool = False,
) -> tuple[ResearchRun, list[tuple[Lead, OutreachRecord]], list[Observation]]:
    if payload.definition.version != 2:
        raise ValueError("Cloud research requires a version 2 campaign")
    definition = payload.definition
    observations, rows, seen = [], [], set()
    for profile in payload.profiles:
        candidate = replace(
            profile.candidate(), profile_url=canonicalize_linkedin_profile_url(profile.profile_url)
        )
        if candidate.profile_url in seen:
            continue
        seen.add(candidate.profile_url)
        lead = process_candidate(candidate, definition, payload.run_id)
        evidence = evaluate_candidate(candidate, definition)
        observations.append(
            Observation(
                id=uuid5(payload.run_id, "observation:" + candidate.profile_url),
                run_id=payload.run_id,
                candidate=candidate,
                accepted=lead is not None,
                canonical_url=candidate.profile_url,
                rejection_reason=None
                if lead
                else ";".join(evidence.excluded) or "no_positive_criteria_matched",
            )
        )
        if lead is not None:
            lead = replace(lead, id=uuid5(payload.run_id, lead.profile_url))
            record = replace(
                create_outreach_record(lead, definition), status=OutreachStatus.NEEDS_VERIFICATION
            )
            if automatic:
                record = automatic_profile_review(lead, definition)
            rows.append((lead, record))
    serialized = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
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
        counters=RunCounters(
            observed=len(observations), accepted=len(rows), rejected=len(observations) - len(rows)
        ),
    )
    return run, rows, observations


def export_research_transfer(run: ResearchRun, observations: list[Observation]) -> RunTransfer:
    if run.dry_run or run.status != RunStatus.COMPLETED:
        raise ValueError("Only completed non-dry research can be synced")
    profiles = [
        ProfileTransfer.model_validate(
            {
                key: value
                for key, value in asdict(item.candidate).items()
                if key in ProfileTransfer.model_fields
            }
        )
        for item in observations
    ]
    return RunTransfer(
        run_id=run.id,
        created_at=run.created_at,
        definition=SearchDefinition.model_validate_json(run.definition_json),
        profiles=profiles,
    )
