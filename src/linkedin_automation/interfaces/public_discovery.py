"""Workspace-isolated public prospects. No LinkedIn visits, approval or sending."""

import copy
import csv
import io
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from threading import RLock, Thread
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from linkedin_automation.infrastructure.public_detail_checks import discover_and_check
from linkedin_automation.infrastructure.public_discovery import (
    BraveSearch,
    DiscoveryError,
)
from linkedin_automation.infrastructure.public_profile_details import hydrate_public_details


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign: str = Field(min_length=1, max_length=150)
    target: int = Field(default=60, ge=1, le=100)
    max_requests: int = Field(default=25, ge=1, le=25)


class SaveProspectsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_urls: list[str] = Field(min_length=1, max_length=100)


def readiness():
    key_ready = bool(os.getenv("BRAVE_SEARCH_API_KEY", "").strip())
    storage_ready = os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") == "1"
    return {
        "configured": key_ready,
        "key_configured": key_ready,
        "storage_enabled": storage_ready,
        "message": "Public discovery ready. No LinkedIn login or worker required."
        if key_ready and storage_ready
        else "Preview only: results are temporary (30 minutes), not saved or exported. "
        "Storage rights must be confirmed to enable database/CSV saving."
        if key_ready
        else "App owner must configure BRAVE_SEARCH_API_KEY.",
    }


class PublicDiscovery:
    def __init__(self, root, catalog, provider_factory=None):
        self.root = root
        self.path = root / "public-discovery.sqlite3"
        self.catalog = catalog
        self.transient = {}
        self.lock = RLock()
        self.provider_factory = provider_factory or (
            lambda: BraveSearch(os.getenv("BRAVE_SEARCH_API_KEY", ""))
        )
        with closing(self.connection()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS public_discovery_jobs (
                id TEXT PRIMARY KEY, campaign TEXT NOT NULL, name TEXT NOT NULL,
                definition TEXT NOT NULL, target INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
                results TEXT NOT NULL DEFAULT '[]', message TEXT NOT NULL DEFAULT '')""")
            db.commit()
            columns = {row[1] for row in db.execute("PRAGMA table_info(public_discovery_jobs)")}
            if "max_requests" not in columns:
                db.execute(
                    "ALTER TABLE public_discovery_jobs "
                    "ADD COLUMN max_requests INTEGER NOT NULL DEFAULT 25"
                )
            if "pipeline_synced" not in columns:
                db.execute(
                    "ALTER TABLE public_discovery_jobs "
                    "ADD COLUMN pipeline_synced INTEGER NOT NULL DEFAULT 0"
                )
            # Aggregate request counts only: no queries, identities or result content.
            db.execute(
                "CREATE TABLE IF NOT EXISTS public_discovery_usage "
                "(month TEXT PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0)"
            )
            db.commit()

    def usage(self):
        month = datetime.now(UTC).strftime("%Y-%m")
        with closing(self.connection()) as db:
            row = db.execute(
                "SELECT attempts FROM public_discovery_usage WHERE month=?", (month,)
            ).fetchone()
            total = db.execute(
                "SELECT COALESCE(SUM(attempts),0) FROM public_discovery_usage"
            ).fetchone()[0]
        return {
            "month_utc": month,
            "month_attempts": row[0] if row else 0,
            "total_attempts": total,
            "scope": "This workspace, since usage tracking was enabled. "
            "Not the Brave account balance.",
        }

    @staticmethod
    def _record_attempts(db, count):
        if count > 0:
            db.execute(
                "INSERT INTO public_discovery_usage(month,attempts) VALUES (?,?) "
                "ON CONFLICT(month) DO UPDATE SET attempts=attempts+excluded.attempts",
                (datetime.now(UTC).strftime("%Y-%m"), count),
            )

    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, payload):
        with self.lock:
            return self._enqueue(payload)

    def _enqueue(self, payload):
        definition, _ = self.catalog.resolve(payload.campaign)
        if not definition.search.positive_count:
            raise ValueError("Add campaign search criteria first.")
        identifier, now = str(uuid4()), time.time()
        self._expire()
        if any(job["status"] == "running" for job in self.transient.values()):
            raise ValueError("Public discovery is already running in this workspace.")
        with closing(self.connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE public_discovery_jobs SET status='failed',message=? "
                "WHERE status='running' AND updated < ?",
                (
                    "Public discovery interrupted. Partial results retained; no automatic retry.",
                    now - 1800,
                ),
            )
            if db.execute("SELECT 1 FROM public_discovery_jobs WHERE status='running'").fetchone():
                raise ValueError("Public discovery is already running in this workspace.")
            if os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") != "1":
                db.commit()
                if len(self.transient) >= 10:
                    oldest = min(self.transient, key=lambda key: self.transient[key]["created"])
                    del self.transient[oldest]
                self.transient[identifier] = {
                    "id": identifier,
                    "campaign": payload.campaign,
                    "name": definition.name,
                    "target": payload.target,
                    "status": "running",
                    "created": now,
                    "api_calls": 0,
                    "results": [],
                    "found": 0,
                    "message": "",
                    "persistent": False,
                    "max_requests": payload.max_requests,
                    "expires_at": now + 1800,
                }
                return identifier, definition
            db.execute(
                "INSERT INTO public_discovery_jobs "
                "(id,campaign,name,definition,target,status,created,updated,max_requests) "
                "VALUES (?,?,?,?,?,'running',?,?,?)",
                (
                    identifier,
                    payload.campaign,
                    definition.name,
                    definition.model_dump_json(),
                    payload.target,
                    now,
                    now,
                    payload.max_requests,
                ),
            )
            db.commit()
        return identifier, definition

    def status(self, identifier=None):
        with self.lock:
            self._expire()
            if identifier in self.transient:
                return copy.deepcopy(self.transient[identifier])
            transient = max(self.transient.values(), key=lambda item: item["created"], default=None)
            return self._stored_status(identifier, transient)

    def _expire(self):
        now = time.time()
        self.transient = {
            key: job for key, job in self.transient.items() if now - job["created"] < 1800
        }

    def _stored_status(self, identifier, transient):
        with closing(self.connection()) as db:
            row = db.execute(
                "SELECT * FROM public_discovery_jobs WHERE id=?"
                if identifier
                else "SELECT * FROM public_discovery_jobs ORDER BY created DESC LIMIT 1",
                (identifier,) if identifier else (),
            ).fetchone()
        if row is None:
            return copy.deepcopy(transient) if identifier is None else None
        if identifier is None and transient and transient["created"] > row["created"]:
            return copy.deepcopy(transient)
        results = json.loads(row["results"])
        for item in results:
            item["profile_details"] = hydrate_public_details(item)
        return {
            "id": row["id"],
            "campaign": row["campaign"],
            "name": row["name"],
            "target": row["target"],
            "status": row["status"],
            "created": row["created"],
            "api_calls": row["calls"],
            "results": results,
            "found": len(results),
            "message": row["message"],
            "persistent": True,
            "max_requests": row["max_requests"],
            "expires_at": None,
            "pipeline_synced": bool(row["pipeline_synced"]),
        }

    def update(self, identifier, rows, calls, status="running", message=""):
        with self.lock:
            if identifier in self.transient:
                delta = calls - self.transient[identifier]["api_calls"]
                with closing(self.connection()) as db:
                    self._record_attempts(db, delta)
                    db.commit()
                self.transient[identifier].update(
                    results=copy.deepcopy(rows),
                    found=len(rows),
                    api_calls=calls,
                    status=status,
                    message=message,
                )
                return
        with closing(self.connection()) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT calls FROM public_discovery_jobs WHERE id=?", (identifier,)
            ).fetchone()
            if previous:
                self._record_attempts(db, calls - previous[0])
            db.execute(
                "UPDATE public_discovery_jobs SET results=?,calls=?,status=?,message=?,updated=? "
                "WHERE id=?",
                (json.dumps(rows), calls, status, message, time.time(), identifier),
            )
            db.commit()

    def run(self, identifier, definition, target):
        try:
            current = self.status(identifier)
            if current is None:
                return
            budget = current["max_requests"]
            rows, calls = discover_and_check(
                definition,
                target,
                self.provider_factory(),
                lambda rows, calls: self.update(identifier, rows, calls),
                max_requests=budget,
            )
            self.update(
                identifier,
                rows,
                calls,
                "completed",
                "Public discovery completed. Strongest public evidence shown first. "
                "Automatic detail checks share the selected request cap. "
                "Prospects need review; customer need is unverified."
                if len(rows) >= target
                else "Discovery stopped below target: public results, evidence improvement "
                f"or the {budget}-request budget exhausted. "
                "No profiles invented; unresolved public fields need verification.",
            )
            if current["persistent"] and os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") == "1":
                try:
                    self.publish(identifier)
                except Exception:
                    job = self.status(identifier)
                    self.update(
                        identifier,
                        rows,
                        calls,
                        "completed",
                        job["message"]
                        + " Pipeline sync needs attention; use Sync to pipeline to retry. "
                        "Discovery results remain saved.",
                    )
        except Exception as error:
            current = self.status(identifier)
            if current is None:
                return
            self.update(
                identifier,
                current["results"],
                current["api_calls"],
                "failed",
                str(error)
                if isinstance(error, DiscoveryError)
                else "Public discovery stopped. Partial results retained; no automatic retry.",
            )

    def publish(self, identifier):
        from linkedin_automation.application.public_prospect_review import prepare_public_collection
        from linkedin_automation.domain.search_definition import SearchDefinition
        from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository

        if os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") != "1":
            raise ValueError("Storage rights are required for pipeline sync")
        with self.lock:
            job = self.status(identifier)
            if not job or not job["persistent"] or job["status"] != "completed":
                raise ValueError("Choose a completed storage-enabled discovery run")
            with closing(self.connection()) as db:
                snapshot = db.execute(
                    "SELECT definition FROM public_discovery_jobs WHERE id=?", (identifier,)
                ).fetchone()
            run, rows, observations = prepare_public_collection(
                job, SearchDefinition.model_validate_json(snapshot[0])
            )
            repository = DatabaseResearchRepository(self.root / "research.sqlite3")
            try:
                # Preserve earlier raw observations when parser improvements reveal
                # additional fields. Lead supplements are handled independently.
                existing_observations = {
                    item.id for item in repository.list_observations(run.id, limit=250)
                }
                observations_to_add = [
                    item for item in observations if item.id not in existing_observations
                ]
                inserted, skipped = repository.import_review_run(
                    run, rows, observations=observations_to_add
                )
                enriched = repository.enrich_public_review_missing(rows)
            finally:
                repository.close()
            with closing(self.connection()) as db:
                db.execute(
                    "UPDATE public_discovery_jobs SET pipeline_synced=1 WHERE id=?", (identifier,)
                )
                db.commit()
            return {
                "run_id": str(run.id),
                "collected": len(observations),
                "saved_leads": len(rows),
                "inserted": inserted,
                "skipped": skipped,
                "enriched": enriched,
                "outreach_queued": False,
            }


def csv_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def install_public_discovery(app, root, catalog):
    store = PublicDiscovery(root, catalog)
    app.state.public_discovery = store

    @app.get("/api/public-discovery/settings")
    def settings():
        return {**readiness(), "job": store.status(), "usage": store.usage()}

    @app.post("/api/public-discovery/jobs")
    def create(payload: DiscoveryRequest):
        if not readiness()["configured"]:
            raise HTTPException(503, readiness()["message"])
        try:
            identifier, definition = store.enqueue(payload)
        except (ValueError, KeyError):
            raise HTTPException(
                409, "Select a valid campaign and finish any current discovery first."
            ) from None
        Thread(target=store.run, args=(identifier, definition, payload.target), daemon=True).start()
        return store.status(identifier)

    @app.get("/api/public-discovery/jobs/{identifier}/export.csv")
    def export(identifier: UUID):
        job = store.status(str(identifier))
        if job is None:
            raise HTTPException(404, "Public discovery run not found in this workspace.")
        if not job["persistent"] or os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") != "1":
            raise HTTPException(409, "Preview only. Storage rights are required for CSV export.")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "Campaign",
                "Name from public index",
                "LinkedIn URL",
                "Public title",
                "Public snippet",
                "Evidence status",
                "Matched criteria",
                "Missing criteria",
                "Customer need",
                "Source",
                "Discovery query",
                "Discovered at (UTC epoch)",
                "Public location",
                "Public role",
                "Public company",
                "Public industry",
                "Public about",
                "Public experience",
                "Public education",
                "Public skills",
                "Public connections",
                "Public followers",
                "Public field evidence (unverified)",
            ]
        )
        for row in job["results"]:
            details = hydrate_public_details(row)
            writer.writerow(
                [
                    csv_cell(x)
                    for x in (
                        job["name"],
                        row["name"],
                        row["profile_url"],
                        row["public_title"],
                        row["snippet"],
                        row["review_status"],
                        "; ".join(row["matched"]),
                        "; ".join(row["missing"]),
                        row["customer_need"],
                        row["source"],
                        row["query"],
                        job["created"],
                        details["location"],
                        details["current_role"],
                        details["company"],
                        details["industry"],
                        details["about"],
                        " | ".join(details["experience"]),
                        " | ".join(details["education"]),
                        " | ".join(details["skills"]),
                        details["connections"],
                        details["followers"],
                        json.dumps(details["field_evidence"], ensure_ascii=False),
                    )
                ]
            )
        return Response(
            "\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="public-prospects-{identifier}.csv"'
            },
        )

    @app.post("/api/public-discovery/jobs/{identifier}/save-leads")
    def save_leads(identifier: UUID, payload: SaveProspectsRequest):
        if os.getenv("BRAVE_SEARCH_STORAGE_ALLOWED") != "1":
            raise HTTPException(409, "Storage rights are required to save public prospects.")
        job = store.status(str(identifier))
        if job is None:
            raise HTTPException(404, "Discovery run not found in this workspace.")
        if not job["persistent"] or job["status"] == "running":
            raise HTTPException(409, "Wait for a storage-enabled discovery run to finish.")
        from linkedin_automation.application.public_prospect_review import prepare_public_review
        from linkedin_automation.domain.search_definition import SearchDefinition
        from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository

        with closing(store.connection()) as db:
            snapshot = db.execute(
                "SELECT definition FROM public_discovery_jobs WHERE id=?", (str(identifier),)
            ).fetchone()
        if snapshot is None:
            raise HTTPException(404, "Discovery run not found in this workspace.")
        try:
            run, rows = prepare_public_review(
                job, SearchDefinition.model_validate_json(snapshot[0]), payload.profile_urls
            )
            repository = DatabaseResearchRepository(root / "research.sqlite3")
            try:
                inserted, skipped = repository.import_review_run(run, rows)
                selected_ids = {lead.id for lead, _ in rows}
                statuses = {
                    record.status.value
                    for record in repository.list_outreach(run.id)
                    if record.lead_id in selected_ids
                }
            finally:
                repository.close()
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {
            "run_id": str(run.id),
            "inserted": inserted,
            "skipped": skipped,
            "status": statuses.pop() if len(statuses) == 1 else "mixed",
            "outreach_queued": False,
            "message": "Saved for review. Open Outreach to confirm eligible drafts.",
        }

    @app.post("/api/public-discovery/jobs/{identifier}/sync-pipeline")
    def sync_pipeline(identifier: UUID):
        if store.status(str(identifier)) is None:
            raise HTTPException(404, "Discovery run not found in this workspace.")
        try:
            return store.publish(str(identifier))
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
