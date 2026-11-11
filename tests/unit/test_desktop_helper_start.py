"""Starting an installed helper must not initiate a new pairing."""

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.mark.parametrize("start_only", [True, False])
def test_duplicate_helper_start_respects_explicit_launch_mode(tmp_path, monkeypatch, start_only):
    spec = importlib.util.spec_from_file_location(
        "desktop_helper_test", Path("scripts/desktop-worker.py")
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, "ROOT", tmp_path)
    monkeypatch.setattr(
        helper.sys, "argv", ["helper", *(["--start-existing"] if start_only else [])]
    )

    @contextmanager
    def running(_):
        yield False

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.worker_supervisor.supervisor_lock", running
    )
    helper.main()
    assert (tmp_path / "pair-again").exists() is (not start_only)
