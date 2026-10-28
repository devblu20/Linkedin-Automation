"""A delivery handoff retries reporting, never the external action."""

from uuid import uuid4

import pytest

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
