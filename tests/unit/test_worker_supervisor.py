"""Supervision must restart crashes without duplicating an existing worker."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from linkedin_automation.infrastructure.worker_supervisor import WorkerSupervisor, supervisor_lock


def test_additional_pairings_are_local_and_do_not_recurse(tmp_path, monkeypatch):
    from linkedin_automation.infrastructure.worker_supervisor import additional_pairings

    monkeypatch.delenv("LINKEDIN_AUTOMATION_DASHBOARD_CREDENTIALS_FILE", raising=False)
    paired = tmp_path / ".env.worker.private.devblu.local"
    paired.touch()
    (tmp_path / ".env.worker.private.local").touch()
    (tmp_path / ".env.railway.local").touch()
    assert additional_pairings(tmp_path) == [paired]
    monkeypatch.setenv("LINKEDIN_AUTOMATION_DASHBOARD_CREDENTIALS_FILE", str(paired))
    assert additional_pairings(tmp_path) == []


def configured_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LINKEDIN_AUTOMATION_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("LINKEDIN_AUTOMATION_DASHBOARD_CREDENTIALS_FILE", raising=False)
    (tmp_path / ".env.railway.local").write_text(
        "DASHBOARD_USERNAME=reviewer\nDASHBOARD_PASSWORD=private-example-password\n"
    )
    return tmp_path


def test_existing_worker_is_not_interrupted_or_duplicated(tmp_path, monkeypatch):
    root = configured_root(tmp_path, monkeypatch)
    delays = []

    def duplicate(*args, **kwargs):
        pytest.fail("A running worker must not be duplicated or stopped")

    supervisor = WorkerSupervisor(
        root, finder=lambda origin: [1234], factory=duplicate, pause=delays.append
    )
    supervisor.cycle()
    assert delays == [15]


def test_worker_exits_restart_with_same_scope_and_safe_flags(tmp_path, monkeypatch):
    root = configured_root(tmp_path, monkeypatch)
    shortcut = root / "startup.lnk"
    shortcut.touch()
    monkeypatch.setattr(
        "linkedin_automation.infrastructure.worker_supervisor.startup_shortcut", lambda: shortcut
    )
    calls, delays = [], []

    def process(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=1234, wait=lambda: 1)

    supervisor = WorkerSupervisor(
        root, finder=lambda origin: [], factory=process, pause=delays.append
    )
    supervisor.cycle()
    supervisor.cycle()
    assert len(calls) == 2 and delays == [15, 15]
    for command, options in calls:
        assert "private-example-password" not in str(command)
        assert command[1:4] == ["-m", "linkedin_automation", "worker"]
        assert options["cwd"] == root
        assert options["env"]["LINKEDIN_AUTOMATION_WORKER_MANAGED"] == "1"
        assert options["env"]["LINKEDIN_AUTOMATION_WORKER_AUTOSTART"] == "1"
        assert options["env"]["LINKEDIN_AUTOMATION_BROWSER_DATA"] == str(
            root / "browser-data-chrome"
        )


def test_failed_process_inventory_never_starts_a_duplicate(tmp_path, monkeypatch):
    root = configured_root(tmp_path, monkeypatch)

    def unavailable(origin):
        raise OSError("Process inventory unavailable")

    def duplicate(*args, **kwargs):
        pytest.fail("Unknown process state must not launch another worker")

    supervisor = WorkerSupervisor(root, finder=unavailable, factory=duplicate)
    with pytest.raises(OSError):
        supervisor.cycle()


def test_supervisor_lock_prevents_second_helper_and_releases(tmp_path: Path):
    lock = tmp_path / "worker.lock"
    with supervisor_lock(lock) as first:
        assert first
        with supervisor_lock(lock) as second:
            assert not second
    with supervisor_lock(lock) as recovered:
        assert recovered


def test_paired_worker_has_private_paths_and_no_implicit_sending(tmp_path, monkeypatch):
    root = configured_root(tmp_path, monkeypatch)
    account = "12345678123456781234567812345678"
    credentials = root / ".env.worker.private.local"
    credentials.write_text(
        "DASHBOARD_USERNAME=worker-" + account + "\nDASHBOARD_PASSWORD=" + "a" * 64 + "\n"
    )
    calls = []

    def process(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=1234, wait=lambda: 0)

    supervisor = WorkerSupervisor(
        root, finder=lambda origin: [], factory=process, pause=lambda _: None
    )
    supervisor.cycle()
    command, options = calls[0]
    assert command[-1] == str(credentials)
    assert "a" * 64 not in str(command)
    environment = options["env"]
    scope = root / "data/workers" / account
    assert environment["LINKEDIN_AUTOMATION_ARTIFACT_DIR"] == str(scope / "artifacts")
    assert environment["LINKEDIN_AUTOMATION_BROWSER_DATA"] == str(
        (scope / "browser-data").resolve()
    )
    assert environment["LINKEDIN_AUTOMATION_ENABLE_OUTREACH_WORKER"] == "0"
    assert "local-research.sqlite3" not in environment["LINKEDIN_AUTOMATION_DATABASE_URL"]


def test_private_worker_profile_must_stay_in_this_installation(tmp_path, monkeypatch):
    root = configured_root(tmp_path, monkeypatch)
    (root / ".env.worker.private.local").write_text(
        "DASHBOARD_USERNAME=worker-12345678123456781234567812345678\n"
        "DASHBOARD_PASSWORD=" + "a" * 64 + "\nWORKER_BROWSER_DATA=" + str(root.parent) + "\n"
    )
    with pytest.raises(ValueError, match="inside this installation"):
        WorkerSupervisor(root)
