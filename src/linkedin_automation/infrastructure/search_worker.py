"""Local browser execution of bounded hosted search requests; no outreach."""

import base64
import json
import os
import time
from contextlib import closing, suppress
from hashlib import sha256
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
    BrowserClosedError,
    LayoutChangedError,
    LimitedVisibilityError,
    RateLimitedError,
    SearchQuotaExceededError,
    SecurityChallengeError,
)
from linkedin_automation.application.research_transfer import export_research_transfer
from linkedin_automation.application.run_transfer import RunTransfer
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.domain.search_definition import SearchDefinition
from linkedin_automation.infrastructure.browser import PlaywrightLinkedInCollector
from linkedin_automation.infrastructure.browser_hold import BrowserHold
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.run_sync import _NoRedirect, sync_transfer
from linkedin_automation.infrastructure.runtime import RuntimeSettings
from linkedin_automation.interfaces.search_jobs import configured_campaigns, definition_hash


class DashboardRejectedError(ValueError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Dashboard worker request rejected (HTTP {status})")


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
        if not path.startswith(("/api/search/", "/api/outreach/")):
            raise ValueError("Invalid worker endpoint")
        if path in {"/api/search/claim", "/api/search/heartbeat"}:
            payload = {
                "managed": os.getenv("LINKEDIN_AUTOMATION_WORKER_MANAGED") == "1",
                "autostart": os.getenv("LINKEDIN_AUTOMATION_WORKER_AUTOSTART") == "1",
                **(payload or {}),
            }
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
            raise DashboardRejectedError(error.code) from None
        except (URLError, TimeoutError, OSError):
            raise ValueError("Cannot reach dashboard") from None


class PendingCompletion:
    """Local durable handoff, isolated by destination and reviewer account."""

    def __init__(self, client: DashboardClient):
        scope = sha256(f"{client.url}\n{client.username}".encode()).hexdigest()
        self.path = (
            RuntimeSettings.from_environment().artifact_dir / "worker-pending" / f"{scope}.json"
        )

    def save(self, job: dict, result: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"id": str(UUID(job["id"])), "token": job["token"], "result": result}),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def deliver(self, client: DashboardClient) -> None:
        if not self.path.exists():
            return
        pending = json.loads(self.path.read_text(encoding="utf-8"))
        result = dict(pending["result"])
        transfer = result.pop("_transfer", None)
        if transfer:
            sync_transfer(
                RunTransfer.model_validate(transfer),
                client.url,
                client.username,
                client.password,
                include_observations=True,
            )
        try:
            client.request(
                f"/api/search/jobs/{UUID(pending['id'])}/complete",
                {"token": pending["token"], **result},
            )
        except DashboardRejectedError as error:
            if error.status != 409:
                raise
            # Expired claims cannot be revived. Research is already synced by run ID,
            # and the local repository retains the original observations.
            print("Pending result synced; search claim expired. No search repeated.", flush=True)
        self.path.unlink()


def validate_job(
    job: dict, config_dir: Path, *, trusted_template: SearchDefinition | None = None
) -> SearchDefinition:
    UUID(job["id"])
    policy = CampaignPolicy.model_validate(job["policy"]) if job.get("policy") else None
    identifier = policy.spec.template_id if policy else job["campaign"]
    original = configured_campaigns(config_dir).get(identifier)
    if original is None and identifier.startswith("custom-template-"):
        UUID(identifier.removeprefix("custom-template-").removesuffix(".yaml"))
        original = trusted_template
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
        1 <= limits.max_results <= min(100, original.limits.max_results)
        and 1 <= limits.max_pages <= min(5, original.limits.max_pages)
    ):
        raise ValueError("Search exceeds worker limits")
    allowed_limits = original.limits.model_copy(
        update={"max_results": limits.max_results, "max_pages": limits.max_pages}
    )
    if limits != allowed_limits:
        raise ValueError("Daily and outreach limits must remain unchanged")
    return requested


def checkpoint_path(client: DashboardClient, job: dict) -> Path:
    scope = sha256(f"{client.url}\n{client.username}".encode()).hexdigest()
    if client.username.startswith("worker-"):
        # Private workspaces already have a full UUID in their artifact root.
        # Keep filenames short enough for Windows installations under Downloads;
        # retain a 128-bit destination namespace plus the complete job UUID.
        return (
            RuntimeSettings.from_environment().artifact_dir
            / "cp"
            / scope[:32]
            / (UUID(job.get("checkpoint_id") or job["id"]).hex + ".json")
        )
    return (
        RuntimeSettings.from_environment().artifact_dir
        / "browser-checkpoints"
        / scope
        / f"{UUID(job.get('checkpoint_id') or job['id'])}.json"
    )


def resolve_job_definition(
    client: DashboardClient, job: dict, config_dir: Path
) -> SearchDefinition:
    identifier = (
        job.get("policy", {}).get("spec", {}).get("template_id")
        if job.get("policy")
        else job["campaign"]
    )
    trusted_template = None
    if identifier and identifier.startswith("custom-template-"):
        # Retrieve separately through the existing authenticated HTTPS worker client.
        # A job's own definition never becomes its trust anchor.
        response = client.request("/api/search/template-definition", {"id": identifier})
        if response.get("id") != identifier:
            raise ValueError("The dashboard returned a different template")
        trusted_template = SearchDefinition.model_validate(response["definition"])
    return validate_job(job, config_dir, trusted_template=trusted_template)


def execute_job(client: DashboardClient, job: dict, config_dir: Path) -> dict:
    definition = resolve_job_definition(client, job, config_dir)
    settings = RuntimeSettings.from_environment()
    checkpoint = checkpoint_path(client, job)
    if job.get("checkpoint_id", job["id"]) != job["id"] and not checkpoint.exists():
        raise ValueError("Resume requires the original worker computer and its checkpoint")
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
                checkpoint_file=checkpoint,
            ),
            reporter=ExcelReportWriter(settings.artifact_dir),
        )
        result = workflow.start(definition_file, dry_run=False, upload=False)
        observations = repository.list_observations(result.run.id)
        return {
            "_transfer": export_research_transfer(result.run, observations).model_dump(mode="json")
            if observations
            else None,
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
            else "No read profiles met this campaign's criteria."
            if result.run.counters.observed
            else "No readable profiles were collected within the remaining visit budget.",
        }


def run_worker(client: DashboardClient, config_dir: Path, *, once: bool = False) -> None:
    print("Browser worker ready. Searches and explicitly authorized delivery only.", flush=True)
    pending = PendingCompletion(client)
    hold = BrowserHold(RuntimeSettings.from_environment().browser_data_dir)
    while True:
        try:
            # Never launch another browser search while an earlier handoff is pending.
            pending.deliver(client)
            if hold.active:
                client.request("/api/search/heartbeat", {"linkedin_paused": True})
                print(
                    "LinkedIn tasks paused. Complete manual login/verification before resuming.",
                    flush=True,
                )
                if once:
                    return
                time.sleep(30)
                continue
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
                    if isinstance(error, SearchQuotaExceededError):
                        message = str(error)
                    elif isinstance(error, LimitedVisibilityError):
                        message = (
                            "LinkedIn hides these profiles outside your network. "
                            "Choose an accessible campaign or review account access."
                        )
                    elif isinstance(error, (AuthenticationRequiredError, SecurityChallengeError)):
                        hold.pause()
                        message = (
                            "LinkedIn tasks paused: sign-in or security verification is required. "
                            "Complete manual login on the worker computer before resuming. "
                            "Restarting the worker does not clear this pause."
                        )
                    elif isinstance(error, BrowserClosedError):
                        message = (
                            "The LinkedIn browser was closed. Start a new search and leave "
                            "its browser open until collection finishes."
                        )
                    elif isinstance(error, RateLimitedError):
                        hold.pause()
                        message = (
                            "LinkedIn requested a pause. Stop searching and review the "
                            "account notice before starting another search."
                        )
                    elif isinstance(error, LayoutChangedError):
                        message = (
                            "LinkedIn did not load a readable page. Check internet access "
                            "and the browser page before starting a new search."
                        )
                    elif isinstance(error, ValueError) and str(error).startswith("Resume requires"):
                        message = (
                            "Resume requires the original worker computer and its saved checkpoint."
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
                        "resume_available": isinstance(
                            error,
                            (
                                BrowserClosedError,
                                AuthenticationRequiredError,
                                SecurityChallengeError,
                                LayoutChangedError,
                            ),
                        )
                        and checkpoint_path(client, job).exists(),
                    }
                finally:
                    stop.set()
                    thread.join(timeout=35)
                pending.save(job, result)
                pending.deliver(client)
                print(f"Search job {job['id']}: {result['status']}", flush=True)
            elif os.getenv("LINKEDIN_AUTOMATION_ENABLE_OUTREACH_WORKER") == "1":
                from linkedin_automation.infrastructure.delivery_worker import (
                    run_authorized_delivery,
                )

                run_authorized_delivery(client)
                if os.getenv("PERSONAL_BROWSER_MODE") == "1" and not hold.active:
                    from linkedin_automation.infrastructure.browser_reply_worker import (
                        run_browser_reply_sync,
                    )

                    run_browser_reply_sync(client)
            if once:
                return
        except (ValueError, KeyError, TimeoutError, OSError):
            print(
                "Worker handoff unavailable. Pending results are kept locally; "
                "retrying without repeating collection or exposing credentials.",
                flush=True,
            )
            if once:
                raise
        time.sleep(10)
