"""A delivery handoff retries reporting, never the external action."""

from pathlib import Path
from uuid import uuid4

import pytest

from linkedin_automation.application.exceptions import AuthenticationRequiredError, RateLimitedError
from linkedin_automation.infrastructure.browser import delivery
from linkedin_automation.infrastructure.delivery_worker import run_authorized_delivery


@pytest.mark.parametrize("crash", [False, True])
def test_delivery_recovery_never_repeats_external_send(tmp_path, monkeypatch, crash):
    monkeypatch.setenv("LINKEDIN_AUTOMATION_ARTIFACT_DIR", str(tmp_path))
    calls = []
    job = {
        "id": str(uuid4()),
        "token": "a" * 48,
        "action": "connection",
        "name": "Example Recipient",
        "profile_url": "https://www.linkedin.com/in/example/",
        "draft": "Approved example",
    }

    class Client:
        url = "http://127.0.0.1"
        username = "reviewer"
        password = "private-dashboard-password"

        def __init__(self):
            self.claimed = False
            self.reports = []

        def request(self, path, payload=None):
            if path.endswith("/claim"):
                if self.claimed:
                    return {"job": None}
                self.claimed = True
                return {"job": job}
            self.reports.append(payload)
            if not crash and len(self.reports) == 1:
                raise ValueError("Network response lost")
            return {"ok": True}

    def execute(self, actual):
        calls.append(actual["id"])
        if crash:
            raise KeyboardInterrupt()
        return {"outcome": "pending", "proof": "Local fixture confirmation only"}

    monkeypatch.setattr(
        "linkedin_automation.infrastructure.delivery_worker.VerifiedLinkedInDelivery.execute",
        execute,
    )
    client = Client()
    with pytest.raises(KeyboardInterrupt if crash else ValueError):
        run_authorized_delivery(client)
    saved = list((tmp_path / "worker-delivery").glob("*.json"))
    assert len(saved) == 1 and client.password not in saved[0].read_text()
    run_authorized_delivery(client)
    assert calls == [job["id"]]
    assert client.reports[-1]["outcome"] == ("uncertain" if crash else "pending")
    assert not list((tmp_path / "worker-delivery").glob("*.json"))


def test_transient_windows_receipt_lock_does_not_repeat_send(tmp_path, monkeypatch):
    original = Path.replace
    locked = []

    def replace(path, target):
        if path.suffix == ".tmp" and Path(target).exists() and not locked:
            locked.append(True)
            raise PermissionError("Temporary test file lock")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    test_delivery_recovery_never_repeats_external_send(tmp_path, monkeypatch, False)
    assert locked == [True]


@pytest.mark.parametrize("sign_in_succeeded", [True, False])
def test_delivery_waits_for_login_then_rechecks_approved_sender(
    tmp_path, monkeypatch, sign_in_succeeded
):
    sender = "https://www.linkedin.com/in/sender/"
    recipient = "https://www.linkedin.com/in/recipient/"
    checked = []
    opened = []

    def verify_sender(page, expected):
        checked.append(expected)
        if len(checked) == 1:
            raise AuthenticationRequiredError("sign in")

    class Page:
        def goto(self, url, **kwargs):
            opened.append(url)

    monkeypatch.setattr(delivery, "verify_sender", verify_sender)
    monkeypatch.setattr(
        delivery, "wait_for_authenticated_page", lambda *args, **kwargs: sign_in_succeeded
    )
    monkeypatch.setattr(delivery, "read_page_snapshot", lambda page: (recipient, "", ""))
    adapter = delivery.VerifiedLinkedInDelivery(tmp_path, expected_sender=sender)
    if sign_in_succeeded:
        adapter._open_recipient(Page(), recipient, allow_sign_in=True)
        assert checked == [sender, sender]
        assert opened == [recipient]
    else:
        with pytest.raises(AuthenticationRequiredError):
            adapter._open_recipient(Page(), recipient, allow_sign_in=True)
        assert checked == [sender]
        assert opened == []


def test_rate_limit_does_not_open_sign_in_recovery(tmp_path, monkeypatch):
    def rate_limited(*args):
        raise RateLimitedError("wait")

    monkeypatch.setattr(delivery, "verify_sender", rate_limited)
    monkeypatch.setattr(
        delivery, "wait_for_authenticated_page", lambda *args, **kwargs: pytest.fail("opened login")
    )
    adapter = delivery.VerifiedLinkedInDelivery(tmp_path, expected_sender="sender")
    with pytest.raises(delivery.BrowserPreflightError) as error:
        adapter._open_recipient(None, "recipient", allow_sign_in=True)
    assert isinstance(error.value.__cause__, RateLimitedError)
