from pathlib import Path
from uuid import uuid4

import pytest

from linkedin_automation.application.exceptions import (
    AuthenticationRequiredError,
    RateLimitedError,
    SecurityChallengeError,
)
from linkedin_automation.infrastructure import browser_reply_worker, delivery_worker, search_worker
from linkedin_automation.infrastructure.browser import detect_safety_state
from linkedin_automation.infrastructure.browser_hold import (
    BrowserHold,
    requires_manual_intervention,
)


@pytest.mark.parametrize(
    "error_type", [AuthenticationRequiredError, SecurityChallengeError, RateLimitedError]
)
def test_search_pause_survives_restart_without_claiming_or_opening_browser(
    tmp_path, monkeypatch, error_type
):
    monkeypatch.setenv("LINKEDIN_AUTOMATION_BROWSER_DATA", str(tmp_path / "profile"))
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    client = search_worker.DashboardClient("http://127.0.0.1", "reviewer", "private-password")
    calls = []

    def request(path, payload=None):
        calls.append(path)
        if path == "/api/search/heartbeat":
            assert payload == {"linkedin_paused": True}
        if path == "/api/search/claim":
            return {"job": {"id": str(uuid4()), "token": "a" * 48}}
        return {"ok": True}

    def blocked(*args):
        raise error_type("Human action required")

    client.request = request
    monkeypatch.setattr(search_worker, "execute_job", blocked)
    search_worker.run_worker(client, Path("config"), once=True)
    hold = BrowserHold(tmp_path / "profile")
    assert hold.active
    assert "private-password" not in hold.path.read_text()
    calls.clear()
    search_worker.run_worker(client, Path("config"), once=True)
    delivery_worker.run_authorized_delivery(client)
    browser_reply_worker.run_browser_reply_sync(client)
    assert calls == ["/api/search/heartbeat"]
    # Another browser profile is unaffected; a supervisor restart cannot clear this one.
    assert not BrowserHold(tmp_path / "other-profile").active
    hold.clear_after_manual_login()
    assert not BrowserHold(tmp_path / "profile").active


@pytest.mark.parametrize(
    "url,title,html",
    [
        ("https://www.linkedin.com/login/login-restriction/", "LinkedIn", ""),
        ("https://www.linkedin.com/feed/", "Restriction | LinkedIn", ""),
        (
            "https://www.linkedin.com/login",
            "LinkedIn",
            "<main>Access to your account has been temporarily restricted</main>",
        ),
    ],
)
def test_restriction_is_not_reported_as_normal_login(url, title, html):
    assert detect_safety_state(url=url, title=title, html=html) == "security_challenge"


def test_wrapped_delivery_security_error_retains_safety_signal():
    try:
        try:
            raise SecurityChallengeError("Verify identity")
        except SecurityChallengeError as error:
            raise ValueError("Preflight failed") from error
    except ValueError as error:
        assert requires_manual_intervention(error)
    assert not requires_manual_intervention(ValueError("Recipient mismatch"))
