"""Local browser execution of bounded hosted search requests; no outreach."""

import base64
import json
import os
import time
from contextlib import closing, suppress
from pathlib import Path
from threading import Event, Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from uuid import UUID

import yaml

from linkedin_automation.application.campaigns import CampaignPolicy, compile_campaign
from linkedin_automation.application.exceptions import (
    AuthenticationRequiredError,
    LimitedVisibilityError,
    SecurityChallengeError,
)
from linkedin_automation.application.research_transfer import export_research_transfer
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInCollector
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.run_sync import _NoRedirect, sync_transfer
from linkedin_automation.infrastructure.runtime import RuntimeSettings
from linkedin_automation.interfaces.search_jobs import configured_campaigns, definition_hash


class DashboardClient:
    def __init__(self, url: str, username: str, password: str):
        parsed = urlsplit(url)
        if (
            (
                parsed.scheme != "https"
                and not (parsed.hostname in {"localhost", "127.0.0.1"} and parsed.scheme == "http")
            )
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use the dashboard HTTPS origin without a path or credentials")
        if not username or not password:
            raise ValueError("Dashboard credentials are missing")
        self.url, self.username, self.password = url.rstrip("/"), username, password

    def request(self, path: str, payload: dict | None = None) -> dict:
        if not path.startswith("/api/search/"):
            raise ValueError("Invalid worker endpoint")
        authorization = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        request = Request(
            self.url + path,
            data=json.dumps(payload or {}).encode(),
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with build_opener(_NoRedirect).open(request, timeout=30) as response:
                return json.loads(response.read(1_000_000))
        except HTTPError as error:
            raise ValueError(f"Dashboard worker request rejected (HTTP {error.code})") from None
        except URLError:
            raise ValueError("Cannot reach dashboard") from None


def validate_job(job: dict, config_dir: Path) -> SearchDefinition:
    UUID(job["id"])
    policy = CampaignPolicy.model_validate(job["policy"]) if job.get("policy") else None
    identifier = policy.spec.template_id if policy else job["campaign"]
    original = configured_campaigns(config_dir).get(identifier)
    if original is None or definition_hash(original) != job["base_hash"]:
        raise ValueError("Worker campaign configuration differs from the dashboard")
    if policy:
        if definition_hash(original) != policy.template_hash:
            raise ValueError("Campaign template does not match the local worker")
        original = compile_campaign(original, policy.spec, job["campaign"])
    requested = SearchDefinition.model_validate(job["definition"])
    if requested.model_copy(update={"limits": original.limits}) != original:
        raise ValueError("Worker only accepts changes to search limits")
    limits = requested.limits
    if not (
        1 <= limits.max_results <= min(25, original.limits.max_results)
        and 1 <= limits.max_pages <= min(5, original.limits.max_pages)
    ):
        raise ValueError("Search exceeds worker limits")
    allowed_limits = original.limits.model_copy(
        update={"max_results": limits.max_results, "max_pages": limits.max_pages}
    )
    if limits != allowed_limits:
        raise ValueError("Daily and outreach limits must remain unchanged")
    return requested


def execute_job(client: DashboardClient, job: dict, config_dir: Path) -> dict:
    definition = validate_job(job, config_dir)
    settings = RuntimeSettings.from_environment()
    requests_dir = settings.artifact_dir / "search-requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    definition_file = requests_dir / f"{UUID(job['id'])}.yaml"
    definition_file.write_text(yaml.safe_dump(definition.model_dump(mode="json")), encoding="utf-8")
    with closing(DatabaseResearchRepository(settings.database_url)) as repository:
        workflow = ResearchWorkflow(
            loader=YamlSearchDefinitionLoader(),
            repository=repository,
            collector=PlaywrightLinkedInCollector(
                browser_data_dir=settings.browser_data_dir,
                screenshot_dir=settings.screenshot_dir,
                browser_channel=os.getenv("LINKEDIN_AUTOMATION_BROWSER_CHANNEL") or "chrome",
            ),
            reporter=ExcelReportWriter(settings.artifact_dir),
        )
        result = workflow.start(definition_file, dry_run=False, upload=False)
        observations = repository.list_observations(result.run.id)
        if observations:
            transfer = export_research_transfer(result.run, observations)
            sync_transfer(
                transfer, client.url, client.username, client.password, include_observations=True
            )
        return {
            "status": "completed",
            "run_id": str(result.run.id),
            "observed": result.run.counters.observed,
            "accepted": result.lead_count,
            "rejected": result.run.counters.rejected,
            "pages_visited": result.pages_visited,
            "hidden_result_cards": result.hidden_result_cards,
            "rejection_counts": result.rejection_counts,
            "message": "Search completed. Matching profiles are saved for review."
            if result.lead_count
            else "No read profiles met this campaign's criteria.",
        }


def run_worker(client: DashboardClient, config_dir: Path, *, once: bool = False) -> None:
    print("Browser worker ready. Only search and review sync are enabled.", flush=True)
    while True:
        try:
            job = client.request("/api/search/claim")["job"]
            if job:
                print(f"Running search job {job['id']}", flush=True)
                stop = Event()

                def heartbeat(stop_event=stop):
                    while not stop_event.wait(10):
                        with suppress(ValueError):
                            client.request("/api/search/heartbeat")

                thread = Thread(target=heartbeat, daemon=True)
                thread.start()
                try:
                    result = execute_job(client, job, config_dir)
                except Exception as error:
                    if isinstance(error, LimitedVisibilityError):
                        message = (
                            "LinkedIn hides these profiles outside your network. "
                            "Choose an accessible campaign or review account access."
                        )
                    elif isinstance(error, (AuthenticationRequiredError, SecurityChallengeError)):
                        message = (
                            "LinkedIn sign-in or security verification is required "
                            "on the worker computer."
                        )
                    else:
                        message = (
                            "Search could not finish or sync. Check the local worker "
                            "and campaign configuration before retrying."
                        )
                    print(f"Search stopped: {type(error).__name__}", flush=True)
                    result = {
                        "status": "failed",
                        "message": message,
                        "pages_visited": getattr(error, "pages_visited", 0),
                        "hidden_result_cards": getattr(error, "hidden_result_cards", 0),
                    }
                finally:
                    stop.set()
                    thread.join(timeout=35)
                client.request(
                    f"/api/search/jobs/{job['id']}/complete", {"token": job["token"], **result}
                )
                print(f"Search job {job['id']}: {result['status']}", flush=True)
            if once:
                return
        except (ValueError, KeyError):
            print(
                "Worker connection unavailable; retrying without exposing credentials.", flush=True
            )
            if once:
                raise
        time.sleep(10)
