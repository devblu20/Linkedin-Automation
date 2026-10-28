"""Verify real queue recovery after missing requests and lost responses."""

from pathlib import Path
from uuid import UUID

import pytest

from linkedin_automation.infrastructure import search_worker
from linkedin_automation.interfaces.search_jobs import (
    SearchCompletion,
    SearchJobs,
    SearchRequest,
    configured_campaigns,
)


@pytest.mark.parametrize("lost_response", [False, True])
def test_worker_restart_delivers_pending_without_repeating_browser(
    tmp_path, monkeypatch, lost_response
):
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "reports"))
    jobs = SearchJobs(tmp_path / "jobs.sqlite3")
    jobs.enqueue(
        SearchRequest(campaign="bluqq-london-prop-family-offices.yaml", max_results=1, max_pages=1),
        configured_campaigns(Path("config")),
    )
    client = search_worker.DashboardClient("http://127.0.0.1", "reviewer", "private-password")
    executions, completions = [], []

    def execute(*args):
        executions.append(True)
        return {"status": "completed", "message": "Finished"}

    def request(path, payload=None):
        if path.endswith("/claim"):
            return {"job": jobs.claim()}
        completions.append(True)
        if len(completions) == 1:
            if lost_response:
                jobs.complete(UUID(path.split("/")[-2]), SearchCompletion(**payload))
            raise ValueError("Connection lost")
        jobs.complete(UUID(path.split("/")[-2]), SearchCompletion(**payload))
        return {"ok": True}

    monkeypatch.setattr(search_worker, "execute_job", execute)
    client.request = request
    with pytest.raises(ValueError):
        search_worker.run_worker(client, Path("config"), once=True)
    pending = search_worker.PendingCompletion(client)
    assert pending.path.exists()
    assert "private-password" not in pending.path.read_text()
    search_worker.run_worker(client, Path("config"), once=True)
    assert jobs.status()["jobs"][0]["status"] == "completed"
    assert len(executions) == 1
    assert len(completions) == 2
    assert not pending.path.exists()


def test_pending_transfer_failure_blocks_next_claim_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "reports"))
    client = search_worker.DashboardClient("http://127.0.0.1", "reviewer", "password")
    pending = search_worker.PendingCompletion(client)
    # A malformed local transfer must never be skipped silently or trigger collection.
    pending.save(
        {"id": "00000000-0000-0000-0000-000000000001", "token": "a" * 48},
        {"status": "completed", "_transfer": {"invalid": True}},
    )

    def unexpected(*args, **kwargs):
        pytest.fail("No claim or browser execution is allowed before pending handoff")

    client.request = unexpected
    monkeypatch.setattr(search_worker, "execute_job", unexpected)
    with pytest.raises(ValueError):
        search_worker.run_worker(client, Path("config"), once=True)
    assert pending.path.exists()
    other = search_worker.PendingCompletion(
        search_worker.DashboardClient("http://127.0.0.1", "another-reviewer", "password")
    )
    assert not other.path.exists()
