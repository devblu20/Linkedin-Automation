import subprocess
from types import SimpleNamespace

import pytest

from linkedin_automation.infrastructure.personal_worker import PersonalBrowserSupervisor


def test_personal_helper_keeps_existing_worker_and_does_not_duplicate(tmp_path):
    def fail(*args, **kwargs):
        pytest.fail("An existing worker must not be duplicated")

    waits = []
    helper = PersonalBrowserSupervisor(
        tmp_path, finder=lambda: [123], factory=fail, pause=waits.append
    )
    helper.cycle()
    assert waits == [15]


def test_personal_worker_restart_keeps_exact_owner_profile_and_transport(tmp_path, monkeypatch):
    shortcut = tmp_path / "startup.lnk"
    shortcut.touch()
    monkeypatch.setattr(
        "linkedin_automation.infrastructure.personal_worker.startup_shortcut", lambda: shortcut
    )
    calls, waits = [], []

    def spawn(command, **options):
        calls.append((command, options))
        return SimpleNamespace(wait=lambda: 1)

    helper = PersonalBrowserSupervisor(
        tmp_path, finder=lambda: [], factory=spawn, pause=waits.append
    )
    helper.cycle()
    helper.cycle()
    assert len(calls) == 2 and waits == [15, 15]
    for command, options in calls:
        assert command[-1] == str(tmp_path / ".env.railway.local")
        assert options["cwd"] == tmp_path
        assert options["env"]["LINKEDIN_AUTOMATION_BROWSER_DATA"] == str(
            tmp_path / "browser-data-chrome"
        )
        assert options["env"]["PERSONAL_BROWSER_MODE"] == "1"
        assert options["env"]["LINKEDIN_AUTOMATION_WORKER_AUTOSTART"] == "1"
        assert options["env"]["LINKEDIN_AUTOMATION_SENDER_NAME"] == "silky work"


@pytest.mark.parametrize(
    "failure",
    [
        OSError("sensitive diagnostic"),
        subprocess.TimeoutExpired("private-command", 30),
        ValueError("invalid inventory"),
    ],
)
def test_transient_inventory_failure_keeps_supervisor_alive_without_starting(
    tmp_path, caplog, failure
):
    calls = []

    def inventory():
        calls.append(1)
        if len(calls) == 1:
            raise failure
        raise KeyboardInterrupt

    def forbidden(*args, **kwargs):
        pytest.fail("Unknown inventory must not start another worker")

    waits = []
    helper = PersonalBrowserSupervisor(
        tmp_path, finder=inventory, factory=forbidden, pause=waits.append
    )
    with pytest.raises(KeyboardInterrupt):
        helper.run()
    assert calls == [1, 1] and waits == [15]
    assert "sensitive diagnostic" not in caplog.text
    assert "private-command" not in caplog.text


def test_temporary_launch_failure_is_retried_without_losing_supervision(tmp_path):
    attempts, waits = [], []

    def spawn(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("Chrome/Python launch temporarily unavailable")
        raise KeyboardInterrupt

    helper = PersonalBrowserSupervisor(
        tmp_path, finder=lambda: [], factory=spawn, pause=waits.append
    )
    with pytest.raises(KeyboardInterrupt):
        helper.run()
    assert len(attempts) == 2 and waits == [15]
