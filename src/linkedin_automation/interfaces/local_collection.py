"""Explicit local-only profile collection from the dashboard."""

# ruff: noqa: E501 - static dashboard markup follows the review site's embedded assets.

import os
from contextlib import closing
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.application.exceptions import WorkflowError
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.direct_profile import DirectProfileCollector
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.runtime import RuntimeSettings


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


COLLECTION_PANEL = """<section class="panel" id="profileCollection">
<form id="collectForm" class="toolbar">
<div class="search"><label for="collectUrl">Collect one profile</label><input id="collectUrl" type="url" required placeholder="https://www.linkedin.com/in/…"></div>
<select id="collectCampaign" aria-label="Collection campaign" required><option value="">Choose campaign</option></select>
<button class="btn primary" id="collectButton" type="submit">Collect profile</button>
</form><p id="collectResult" role="status" style="padding:0 19px 14px;margin:0">Opens your local browser. Campaign matching applies. No outreach is sent.</p></section>"""

COLLECTION_SCRIPT = """<script>
const collectForm=document.getElementById('collectForm'),collectButton=document.getElementById('collectButton'),collectResult=document.getElementById('collectResult'),collectCampaign=document.getElementById('collectCampaign');
fetch('/api/collection/campaigns').then(async r=>{if(!r.ok)throw Error('Cannot load campaigns');return r.json()}).then(items=>{for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;collectCampaign.appendChild(option)}if(!items.length){collectButton.disabled=true;collectResult.textContent='Add a version 2 campaign in config to collect profiles.'}}).catch(e=>{collectResult.textContent=e.message;collectButton.disabled=true});
collectForm.onsubmit=async e=>{e.preventDefault();collectButton.disabled=true;collectResult.textContent='Reading profile in your local browser. Please leave it open…';try{const r=await fetch('/api/collection/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_url:document.getElementById('collectUrl').value,campaign:collectCampaign.value})});const body=await r.json();if(!r.ok)throw Error(typeof body.detail==='string'?body.detail:'Check the profile URL and campaign');collectResult.textContent=`Completed: ${body.observed} profile read, ${body.accepted} accepted, ${body.rejected} rejected by campaign criteria. Report: ${body.report||'none'}. Run: ${body.run_id}`;await Promise.all([load(),loadSummary()])}catch(error){collectResult.textContent=error.message}finally{collectButton.disabled=false}};
</script>"""
