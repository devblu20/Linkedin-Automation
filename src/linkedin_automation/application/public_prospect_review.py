"""Adapt selected public-index evidence into the existing manual review workflow."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid5

from linkedin_automation.application.automatic_review import automatic_review_blockers
from linkedin_automation.domain.enums import OutreachStatus, RunStatus
from linkedin_automation.domain.models import (
    Lead,
    LeadCandidate,
    MatchEvidence,
    Observation,
    ResearchRun,
    RunCounters,
)
from linkedin_automation.domain.services import create_outreach_record
from linkedin_automation.infrastructure.public_profile_details import hydrate_public_details


def profile_fields(item):
    details = hydrate_public_details(item)
    values = {
        key: details[key]
        for key in ("location", "company", "industry", "about", "connections", "followers")
    }
    values["current_title"] = details["current_role"]
    values.update({key: tuple(details[key]) for key in ("experience", "education", "skills")})
    return values


def public_snapshot(item):
    text = item["public_title"] + "\nPublic search snippet: " + item["snippet"]
    for extra in item.get("additional_evidence", [])[:5]:
        text += "\nAdditional public index query: " + extra["query"] + "\n" + extra["snippet"]
    return text


def prepare_public_review(job, definition, selected_urls):
    """Keep campaign snapshots, stable IDs and unknown facts; never approve or queue."""
    if definition.version != 2:
        raise ValueError("A version 2 campaign is required for lead review")
    available = {row["profile_url"]: row for row in job["results"]}
    selected = list(dict.fromkeys(selected_urls))
    if not selected or any(url not in available for url in selected):
        raise ValueError("Choose profiles from this discovery run")
    run_id = uuid5(UUID(job["id"]), "public-prospect-review")
    observed = datetime.fromtimestamp(job["created"], UTC)
    serialized = json.dumps(
        definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    rows = []
    for url in selected:
        item = available[url]
        lead = Lead(
            id=uuid5(run_id, url),
            run_id=run_id,
            profile_url=url,
            full_name=item["name"],
            headline=item["public_title"],
            **profile_fields(item),
            evidence=MatchEvidence(
                matched=tuple(item["matched"]),
                excluded=tuple("required:" + value for value in item["missing"]),
            ),
            source_search="Brave public web index: " + item["query"],
            first_observed_at=observed,
            last_observed_at=observed,
            notes="Saved from public search for manual review. Public-index fields "
            "are not independently verified; they may be incomplete or outdated. "
            "Customer need is unverified. "
            "Check the source before qualification or outreach.",
            profile_snapshot=public_snapshot(item),
        )
        record = create_outreach_record(lead, definition)
        if item["missing"] or automatic_review_blockers(lead, definition):
            record = replace(record, status=OutreachStatus.NEEDS_VERIFICATION)
        if item["missing"]:
            lead = replace(
                lead,
                notes=lead.notes
                + " Missing campaign evidence: "
                + ", ".join(item["missing"])
                + ".",
            )
        rows.append((lead, record))
    run = ResearchRun(
        id=run_id,
        search_name=definition.name + " — Public prospect review",
        schema_version=definition.version,
        definition_json=serialized,
        definition_hash=hashlib.sha256(serialized.encode()).hexdigest(),
        status=RunStatus.COMPLETED,
        dry_run=False,
        created_at=observed,
        updated_at=observed,
        counters=RunCounters(observed=len(rows), accepted=len(rows)),
    )
    return run, rows


def prepare_public_collection(job, definition):
    """All prospects enter manual review; observation match decisions stay unchanged."""
    eligible = [row["profile_url"] for row in job["results"]]
    if eligible:
        run, rows = prepare_public_review(job, definition, eligible)
    else:
        serialized = json.dumps(
            definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        observed = datetime.fromtimestamp(job["created"], UTC)
        run = ResearchRun(
            id=uuid5(UUID(job["id"]), "public-prospect-review"),
            search_name=definition.name + " — Public prospect review",
            schema_version=definition.version,
            definition_json=serialized,
            definition_hash=hashlib.sha256(serialized.encode()).hexdigest(),
            status=RunStatus.COMPLETED,
            dry_run=False,
            created_at=observed,
            updated_at=observed,
        )
        rows = []
    observations = []
    for item in job["results"]:
        candidate = LeadCandidate(
            profile_url=item["profile_url"],
            full_name=item["name"],
            headline=item["public_title"],
            **profile_fields(item),
            source_search="Brave public web index: " + item["query"],
            profile_snapshot=public_snapshot(item),
            collected_at=run.created_at,
        )
        observations.append(
            Observation(
                id=uuid5(run.id, "observation:" + item["profile_url"]),
                run_id=run.id,
                candidate=candidate,
                accepted=not item["missing"],
                canonical_url=item["profile_url"],
                rejection_reason=";".join("required:" + value for value in item["missing"]) or None,
            )
        )
    run = replace(
        run,
        counters=RunCounters(
            observed=len(observations),
            accepted=sum(item.accepted for item in observations),
            rejected=sum(not item.accepted for item in observations),
        ),
    )
    return run, rows, observations
