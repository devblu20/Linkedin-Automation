"""Explicit local-only profile collection from the dashboard."""

import os
import tempfile
from contextlib import closing
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.application.exceptions import WorkflowError
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInCollector
from linkedin_automation.infrastructure.browser.direct_profile import DirectProfileCollector
from linkedin_automation.infrastructure.campaign_catalog import CampaignCatalog
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.runtime import RuntimeSettings
from linkedin_automation.interfaces.search_jobs import (
    SearchRequest,
    configured_campaigns,
)


class ProfileCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_url: str = Field(max_length=2048)
    campaign: str = Field(min_length=1, max_length=150)

    @field_validator("profile_url")
    @classmethod
    def linkedin_url(cls, value: str) -> str:
        return canonicalize_linkedin_profile_url(value)


def install_local_collection(app: FastAPI, database_url: str | Path, config_dir: Path) -> None:
    root = config_dir.resolve()
    lock = Lock()
    loader = YamlSearchDefinitionLoader()
    search_history = []
    settings = RuntimeSettings.from_environment()
    catalog = CampaignCatalog(
        settings.artifact_dir / "campaigns.sqlite3", configured_campaigns(root)
    )

    def campaigns() -> dict[str, Path]:
        found = {}
        for candidate in sorted(root.glob("*.yaml")):
            if candidate.resolve().parent != root:
                continue
            try:
                definition = loader.load(candidate)
                if definition.version == 2:
                    found[candidate.name] = candidate
            except (ConfigurationError, ValueError, OSError):
                continue
        return found

    def local_request(request: Request, *, write: bool = False) -> None:
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "Collection is available only on this computer")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Use the local dashboard")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Invalid request origin")
        if write and not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(403, "JSON is required")

    @app.get("/api/search/settings")
    def search_settings(request: Request):
        local_request(request)
        return {
            "mode": "local",
            "worker_online": True,
            "jobs": search_history[-10:][::-1],
            "campaigns": catalog.options(),
        }

    @app.post("/api/search/jobs")
    def automatic_search(request: Request, payload: SearchRequest):
        local_request(request, write=True)
        try:
            definition, _ = catalog.resolve(payload.campaign)
        except (KeyError, ValueError) as error:
            raise HTTPException(422, "Select a valid saved campaign") from error
        if (
            definition is None
            or payload.max_results > definition.limits.max_results
            or payload.max_pages > definition.limits.max_pages
        ):
            raise HTTPException(422, "Choose a configured campaign and respect its search limits")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "A search or profile collection is already running")
        job = {
            "id": str(uuid4()),
            "status": "running",
            "campaign": definition.name,
            "result": None,
            "requested": {"profiles": payload.max_results, "pages": payload.max_pages},
        }
        search_history.append(job)
        del search_history[:-10]
        try:
            settings = RuntimeSettings.from_environment()
            definition = definition.model_copy(
                update={
                    "limits": definition.limits.model_copy(
                        update={
                            "max_results": payload.max_results,
                            "max_pages": payload.max_pages,
                        }
                    )
                }
            )
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "search.yaml"
                path.write_text(
                    yaml.safe_dump(definition.model_dump(mode="json")), encoding="utf-8"
                )
                with closing(
                    ResearchWorkflow(
                        loader=loader,
                        repository=DatabaseResearchRepository(database_url),
                        collector=PlaywrightLinkedInCollector(
                            browser_data_dir=settings.browser_data_dir,
                            screenshot_dir=settings.screenshot_dir,
                            browser_channel=os.getenv("LINKEDIN_AUTOMATION_BROWSER_CHANNEL")
                            or "chrome",
                        ),
                        reporter=ExcelReportWriter(settings.artifact_dir),
                    )
                ) as workflow:
                    result = workflow.start(path, dry_run=False, upload=False)
            job.update(
                status="completed",
                result={
                    "run_id": str(result.run.id),
                    "observed": result.run.counters.observed,
                    "accepted": result.lead_count,
                    "rejected": result.run.counters.rejected,
                    "pages_visited": result.pages_visited,
                    "hidden_result_cards": result.hidden_result_cards,
                    "rejection_counts": result.rejection_counts,
                },
            )
            return job
        except (ConfigurationError, WorkflowError, ValueError, OSError) as error:
            job.update(status="failed", result={"message": str(error)})
            raise HTTPException(422, str(error)) from error
        finally:
            lock.release()

    from linkedin_automation.interfaces.campaign_editor import install_campaign_routes

    install_campaign_routes(app, catalog, request_guard=local_request)

    @app.get("/api/collection/campaigns")
    def list_campaigns(request: Request):
        local_request(request)
        return [{"id": key, "name": loader.load(path).name} for key, path in campaigns().items()]

    @app.post("/api/collection/profile")
    def collect_profile(request: Request, payload: ProfileCollection):
        local_request(request, write=True)
        path = campaigns().get(payload.campaign)
        if path is None:
            raise HTTPException(422, "Select a configured version 2 campaign")
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "A profile is already being collected; wait for it to finish")
        try:
            settings = RuntimeSettings.from_environment()
            with closing(
                ResearchWorkflow(
                    loader=loader,
                    repository=DatabaseResearchRepository(database_url),
                    collector=DirectProfileCollector(
                        payload.profile_url,
                        settings.browser_data_dir,
                        os.getenv("LINKEDIN_AUTOMATION_BROWSER_CHANNEL") or "chrome",
                    ),
                    reporter=ExcelReportWriter(settings.artifact_dir),
                )
            ) as workflow:
                result = workflow.start(path, dry_run=False, upload=False)
            return {
                "run_id": str(result.run.id),
                "status": result.run.status.value,
                "observed": result.run.counters.observed,
                "accepted": result.lead_count,
                "rejected": result.run.counters.rejected,
                "report": Path(result.run.report_path).name if result.run.report_path else None,
            }
        except (ConfigurationError, WorkflowError, ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error
        finally:
            lock.release()
