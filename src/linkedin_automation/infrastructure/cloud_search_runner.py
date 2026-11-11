"""Server-side research runner. It never performs connections or sends messages."""

import os
from contextlib import closing, suppress
from pathlib import Path
from threading import Event, Lock, Thread
from uuid import UUID

from linkedin_automation.application.research_transfer import (
    export_research_transfer,
    prepare_research_transfer,
)
from linkedin_automation.application.workflow import ResearchWorkflow
from linkedin_automation.infrastructure.cloud_delivery import CloudDeliveryRunner
from linkedin_automation.infrastructure.persistence import DatabaseResearchRepository
from linkedin_automation.infrastructure.reporting import ExcelReportWriter
from linkedin_automation.infrastructure.unipile_collection import (
    KnownTestProfileCollector,
    UnipileCollector,
)
from linkedin_automation.interfaces.search_jobs import SearchCompletion, WorkerHeartbeat


class CloudSearchRunner:
    def __init__(self, client, account, allowed, app, root, *, scope=None):
        self.client, self.account, self.allowed, self.app, self.root = (
            client,
            account,
            allowed,
            app,
            Path(root),
        )
        self.stop = Event()
        self.lock = Lock()
        self.thread = None
        self.delivery = CloudDeliveryRunner(
            client, account, allowed, app.state.delivery_queue, root, scope=scope
        )
        from linkedin_automation.infrastructure.message_ai import MessageAI
        from linkedin_automation.infrastructure.reply_sync import ReplySync

        queue = app.state.delivery_queue
        self.replies = ReplySync(
            client, account, allowed, queue.repository, queue.engagement, MessageAI()
        )
        queue.reply_sync = self.replies
        app.state.reply_sync = self.replies

    def ensure(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = Thread(target=self.run, daemon=True)
                self.thread.start()

    def cycle(self):
        if not self.allowed():
            return
        account = self.account()
        if not account or account["status"] != "connected":
            return
        store = self.app.state.search_store
        job = store.claim(WorkerHeartbeat(managed=True), device="cloud")
        if not job:
            return
        heartbeat_stop = Event()

        def heartbeat():
            while not heartbeat_stop.wait(10):
                with suppress(Exception), store.connection() as db:
                    store.record_heartbeat(db, WorkerHeartbeat(managed=True), device="cloud")

        Thread(target=heartbeat, daemon=True).start()
        result = {
            "status": "failed",
            "message": "Cloud collection could not complete. No request was automatically retried.",
        }
        try:
            from linkedin_automation.domain.search_definition import SearchDefinition

            definition = SearchDefinition.model_validate(job["definition"])

            class Loader:
                def load(self, _):
                    return definition

            self.root.mkdir(parents=True, exist_ok=True)
            with closing(
                DatabaseResearchRepository(
                    "sqlite:///" + (self.root / "cloud-research.sqlite3").as_posix()
                )
            ) as repository:
                collector = (
                    KnownTestProfileCollector(
                        self.client, account["account_id"], job["source_profile_url"], self.allowed
                    )
                    if job.get("source_profile_url")
                    else UnipileCollector(self.client, account["account_id"], self.allowed)
                )
                workflow = ResearchWorkflow(
                    loader=Loader(),
                    repository=repository,
                    collector=collector,
                    reporter=ExcelReportWriter(self.root / "artifacts"),
                )
                run = workflow.start(self.root / "cloud-search.yaml", dry_run=False, upload=False)
                observations = repository.list_observations(run.run.id)
                if observations:
                    transfer = export_research_transfer(run.run, observations)
                    research, rows, observations = prepare_research_transfer(
                        transfer, automatic=os.getenv("AUTOMATIC_QUALIFICATION") == "1"
                    )
                else:
                    # Empty provider searches are completed runs, not invalid transfers.
                    # Keep the external transfer contract's non-empty requirement intact.
                    research, rows = run.run, []
                with closing(
                    DatabaseResearchRepository(
                        "sqlite:///" + (self.root / "research.sqlite3").as_posix()
                    )
                ) as target:
                    if not self.allowed():
                        raise ValueError("Workspace access changed during collection")
                    target.initialize()
                    target.import_review_run(research, rows, observations=observations)
                    if os.getenv("AUTOMATIC_QUALIFICATION") == "1":
                        target.enrich_unreviewed_profiles(rows)
                if os.getenv("AUTOMATIC_QUEUE") == "1":
                    self.app.state.delivery_queue.prepare()
                result = {
                    "status": "completed",
                    "run_id": str(run.run.id),
                    "observed": run.run.counters.observed,
                    "accepted": run.lead_count,
                    "rejected": run.run.counters.rejected,
                    "pages_visited": run.pages_visited,
                    "hidden_result_cards": run.hidden_result_cards,
                    "rejection_counts": run.rejection_counts,
                    "message": "Cloud search completed. Matching profiles are saved for review."
                    if run.lead_count
                    else "Cloud search completed; LinkedIn returned no profiles for these filters."
                    if not run.run.counters.observed
                    else "Cloud search completed; no collected profiles met the campaign criteria.",
                }
        except Exception as error:
            from linkedin_automation.application.exceptions import WorkflowError
            from linkedin_automation.infrastructure.unipile_client import UnipileError

            if isinstance(error, (WorkflowError, UnipileError)):
                result["message"] = str(error).split(" (run ID:")[0][:500]
                result["pages_visited"] = getattr(error, "pages_visited", 0)
                result["hidden_result_cards"] = getattr(error, "hidden_result_cards", 0)
            else:
                result["message"] = (
                    "Cloud collection stopped (" + type(error).__name__ + "). "
                    "No request was automatically retried."
                )
        finally:
            heartbeat_stop.set()
        store.complete(UUID(job["id"]), SearchCompletion(token=job["token"], **result))

    def run(self):
        while not self.stop.is_set():
            # Never print provider bodies, keys or user profile data.
            with suppress(Exception):
                self.cycle()
            with suppress(Exception):
                self.replies.cycle()
            with suppress(Exception):
                self.delivery.cycle()
            self.stop.wait(5)
