"""End-to-end application orchestration over injected ports."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from linkedin_automation.application.exceptions import (
    CollectionError,
    CollectionLimitError,
    ReportError,
    RunNotFoundError,
    StorageError,
)
from linkedin_automation.application.ports import (
    ArtifactStorage,
    LeadCollector,
    ReportWriter,
    ResearchRepository,
    SearchDefinitionLoader,
)
from linkedin_automation.domain.enums import RunStatus
from linkedin_automation.domain.models import Observation, ResearchRun, RunCounters, utc_now
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.domain.services import (
    LeadProcessingError,
    canonicalize_linkedin_profile_url,
    create_outreach_record,
    process_candidate,
)


@dataclass(frozen=True, slots=True)
class RunResult:
    """Compact application result suitable for any interface."""

    run: ResearchRun
    lead_count: int


class ResearchWorkflow:
    """Coordinate collection, processing, reporting, upload, and recovery."""

    def __init__(
        self,
        *,
        loader: SearchDefinitionLoader,
        repository: ResearchRepository,
        collector: LeadCollector,
        reporter: ReportWriter,
        storage: ArtifactStorage | None = None,
    ) -> None:
        self._loader = loader
        self._repository = repository
        self._collector = collector
        self._reporter = reporter
        self._storage = storage
        self._repository.initialize()

    def close(self) -> None:
        """Release resources owned by injected local adapters."""
        self._repository.close()

    def start(self, search_file: Path, *, dry_run: bool, upload: bool) -> RunResult:
        definition = self._loader.load(search_file)
        serialized = json.dumps(
            definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        run = ResearchRun.create(
            search_name=definition.name,
            schema_version=definition.version,
            definition_json=serialized,
            definition_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            dry_run=dry_run,
        )
        self._repository.add_run(run)
        return self._execute(run, definition, start_page=1, upload=upload)

    def resume(self, run_id: UUID, *, upload: bool) -> RunResult:
        run = self.get_run(run_id)
        if run.status not in {RunStatus.COLLECTING, RunStatus.PAUSED, RunStatus.FAILED}:
            raise RunNotFoundError(f"run {run_id} is {run.status.value} and cannot be resumed")
        if run.status == RunStatus.COLLECTING:
            run = run.transition(RunStatus.PAUSED)
            self._repository.save_run(run)
        definition = SearchDefinition.model_validate_json(run.definition_json)
        if run.status == RunStatus.FAILED and run.error_category == "StorageError":
            completed = self.upload(run.id)
            return RunResult(completed, len(self._repository.list_leads(run.id)))
        if run.status == RunStatus.FAILED and run.error_category == "ReportError":
            reporting = run.transition(RunStatus.REPORTING)
            self._repository.save_run(reporting)
            try:
                path = self._reporter.write(
                    reporting,
                    self._repository.list_leads(run.id),
                    self._repository.list_outreach(run.id),
                    definition.output.file_name,
                )
            except ReportError as error:
                error.run_id = str(reporting.id)
                failed = replace(
                    reporting.transition(RunStatus.FAILED),
                    error_category=error.__class__.__name__,
                    error_message=str(error),
                )
                self._repository.save_run(failed)
                raise
            reporting = replace(reporting, report_path=str(path), updated_at=utc_now())
            self._repository.save_run(reporting)
            completed = self.upload(run.id) if upload else reporting.transition(RunStatus.COMPLETED)
            self._repository.save_run(completed)
            return RunResult(completed, len(self._repository.list_leads(run.id)))
        return self._execute(
            run,
            definition,
            start_page=max(1, run.checkpoint_page + 1),
            upload=upload,
        )

    def get_run(self, run_id: UUID) -> ResearchRun:
        run = self._repository.get_run(run_id)
        if run is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return run

    def export(self, run_id: UUID) -> Path:
        run = self.get_run(run_id)
        definition = SearchDefinition.model_validate_json(run.definition_json)
        leads = self._repository.list_leads(run.id)
        path = self._reporter.write(
            run, leads, self._repository.list_outreach(run.id), definition.output.file_name
        )
        self._repository.save_run(replace(run, report_path=str(path), updated_at=utc_now()))
        return path

    def upload(self, run_id: UUID) -> ResearchRun:
        run = self.get_run(run_id)
        if run.drive_file_id and run.drive_url:
            return run
        if self._storage is None:
            raise StorageError("Google Drive storage is not configured")
        definition = SearchDefinition.model_validate_json(run.definition_json)
        report_path = Path(run.report_path) if run.report_path else self.export(run.id)
        if run.status != RunStatus.UPLOADING:
            run = run.transition(RunStatus.UPLOADING)
        self._repository.save_run(run)
        try:
            folder_id = os.getenv(definition.output.drive_folder_id_env, "").strip()
            if not folder_id:
                raise StorageError(
                    f"environment variable {definition.output.drive_folder_id_env} is not set"
                )
            artifact = self._storage.upload(
                report_path, folder_id=folder_id, idempotency_key=str(run.id)
            )
        except StorageError as error:
            error.run_id = str(run.id)
            failed = replace(
                run.transition(RunStatus.FAILED),
                error_category=error.__class__.__name__,
                error_message=str(error),
            )
            self._repository.save_run(failed)
            raise
        completed = replace(
            run.transition(RunStatus.COMPLETED),
            drive_file_id=artifact.file_id,
            drive_url=artifact.web_url,
            error_category=None,
            error_message=None,
        )
        self._repository.save_run(completed)
        return completed

    def _execute(
        self,
        run: ResearchRun,
        definition: SearchDefinition,
        *,
        start_page: int,
        upload: bool,
    ) -> RunResult:
        collecting = run.transition(RunStatus.COLLECTING)
        self._repository.save_run(collecting)
        try:
            today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            already_observed = self._repository.count_observations_since(today)
            remaining_daily = definition.limits.max_daily_results - already_observed
            if remaining_daily <= 0:
                raise CollectionLimitError("configured daily collection limit has been reached")
            effective_limits = definition.limits.model_copy(
                update={"max_results": min(definition.limits.max_results, remaining_daily)}
            )
            effective_definition = definition.model_copy(update={"limits": effective_limits})
            batch = self._collector.collect(
                effective_definition, start_page=start_page, dry_run=collecting.dry_run
            )
        except CollectionError as error:
            error.run_id = str(collecting.id)
            paused = replace(
                collecting.transition(RunStatus.PAUSED),
                checkpoint_page=max(collecting.checkpoint_page, error.checkpoint_page),
                error_category=error.__class__.__name__,
                error_message=str(error),
            )
            self._repository.save_run(paused)
            raise

        counters = collecting.counters
        processing = replace(
            collecting.transition(RunStatus.PROCESSING),
            checkpoint_page=batch.checkpoint_page,
            error_category=None,
            error_message=None,
        )
        for candidate in batch.candidates[: effective_limits.max_results]:
            accepted = False
            canonical_url: str | None = None
            rejection: str | None = None
            lead = None
            try:
                canonical_url = canonicalize_linkedin_profile_url(candidate.profile_url)
                lead = process_candidate(candidate, definition, processing.id)
                accepted = lead is not None
                if not accepted:
                    rejection = "criteria_not_matched_or_excluded"
            except LeadProcessingError as error:
                rejection = str(error)
            self._repository.add_observation(
                Observation(
                    id=uuid4(),
                    run_id=processing.id,
                    candidate=candidate,
                    accepted=accepted,
                    canonical_url=canonical_url,
                    rejection_reason=rejection,
                )
            )
            inserted = self._repository.upsert_lead(lead) if lead is not None else False
            if lead is not None and inserted and definition.qualification is not None:
                self._repository.upsert_outreach(create_outreach_record(lead, definition))
            counters = RunCounters(
                observed=counters.observed + 1,
                accepted=counters.accepted + int(accepted and inserted),
                rejected=counters.rejected + int(not accepted),
                duplicated=counters.duplicated + int(accepted and not inserted),
                failed=counters.failed,
            )
        processing = replace(processing, counters=counters, updated_at=utc_now())
        self._repository.save_run(processing)
        reporting = processing.transition(RunStatus.REPORTING)
        self._repository.save_run(reporting)

        if reporting.dry_run:
            completed = reporting.transition(RunStatus.COMPLETED)
        else:
            leads = self._repository.list_leads(reporting.id)
            report_snapshot = reporting.transition(RunStatus.COMPLETED)
            try:
                path = self._reporter.write(
                    report_snapshot,
                    leads,
                    self._repository.list_outreach(reporting.id),
                    definition.output.file_name,
                )
            except ReportError as error:
                error.run_id = str(reporting.id)
                failed = replace(
                    reporting.transition(RunStatus.FAILED),
                    error_category=error.__class__.__name__,
                    error_message=str(error),
                )
                self._repository.save_run(failed)
                raise
            completed = replace(report_snapshot, report_path=str(path), updated_at=utc_now())
            if upload:
                self._repository.save_run(completed)
                completed = self.upload(completed.id)
        self._repository.save_run(completed)
        return RunResult(run=completed, lead_count=len(self._repository.list_leads(completed.id)))
