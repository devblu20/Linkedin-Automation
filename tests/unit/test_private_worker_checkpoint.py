"""Private workspace paths must not exceed classic Windows filename limits."""

from pathlib import Path
from uuid import uuid4

from linkedin_automation.infrastructure.search_worker import DashboardClient, checkpoint_path


def test_private_checkpoint_is_short_and_scoped(monkeypatch):
    artifact = Path.cwd() / "data/workers/123d1cc7fd7b411c86294e371f7fafca/artifacts"
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(artifact))
    identifier = str(uuid4())
    job = {"id": identifier}
    one = DashboardClient("https://example.com", "worker-" + "1" * 32, "a" * 64)
    two = DashboardClient("https://example.com", "worker-" + "2" * 32, "b" * 64)
    path = checkpoint_path(one, job)
    relative = path.relative_to(artifact)
    assert relative.parts[0] == "cp"
    assert len(relative.parts[1]) == 32
    assert relative.name == identifier.replace("-", "") + ".json"
    assert len(str(relative)) < 80
    assert path != checkpoint_path(two, job)
    assert path == checkpoint_path(one, {"id": str(uuid4()), "checkpoint_id": identifier})


def test_legacy_checkpoints_remain_compatible(monkeypatch, tmp_path):
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path))
    identifier = str(uuid4())
    client = DashboardClient("https://example.com", "owner", "a" * 64)
    path = checkpoint_path(client, {"id": identifier})
    assert path.parts[-3] == "browser-checkpoints"
    assert len(path.parts[-2]) == 64
    assert path.name == identifier + ".json"
