"""One delivery at a time, with durable result handoff and no blind send retries."""

import json
import os
import time
from contextlib import suppress
from hashlib import sha256
from threading import Event, Thread
from uuid import UUID

from linkedin_automation.infrastructure.browser.conversations import BrowserPreflightError
from linkedin_automation.infrastructure.browser.delivery import VerifiedLinkedInDelivery
from linkedin_automation.infrastructure.browser_hold import (
    BrowserHold,
    requires_manual_intervention,
)
from linkedin_automation.infrastructure.runtime import RuntimeSettings


def run_authorized_delivery(client):
    settings = RuntimeSettings.from_environment()
    scope = sha256((client.url + "\n" + client.username).encode()).hexdigest()
    path = settings.artifact_dir / "worker-delivery" / (scope + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)

    def save(value):
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            output.write(json.dumps(value))
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        for attempt in range(5):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                # Windows indexing/scanning can briefly hold our own receipt open.
                # Retry this atomic disk operation only, never the LinkedIn action.
                if attempt == 4:
                    raise
                time.sleep(0.05 * (2**attempt))

    def acknowledge(pending, result):
        try:
            client.request(
                "/api/outreach/jobs/" + str(UUID(pending["job"]["id"])) + "/complete",
                {"token": pending["job"]["token"], **result},
            )
        except ValueError as error:
            if getattr(error, "status", None) != 409:
                raise
            # Preserve proof when the reviewer resolved/revoked the claim. A
            # rejected old claim must never cause another send attempt.
            archive = path.with_name(
                scope + "-" + str(UUID(pending["job"]["id"])) + "-resolved.json"
            )
            path.replace(archive)
            return
        path.unlink()

    if path.exists():
        pending = json.loads(path.read_text(encoding="utf-8"))
        result = pending.get("result") or {
            "outcome": "uncertain",
            "proof": "Worker interrupted. Verify LinkedIn manually; automatic resend blocked.",
        }
        acknowledge(pending, result)
        return
    hold = BrowserHold(settings.browser_data_dir)
    if hold.active:
        return
    job = client.request("/api/outreach/claim")["job"]
    if not job:
        return
    save({"job": job})
    stop = Event()

    def heartbeat():
        while not stop.wait(10):
            with suppress(ValueError):
                client.request("/api/search/heartbeat")

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        sender = job.get("browser_sender_profile")

        def observe(conversation):
            return client.request(
                "/api/outreach/browser/observations",
                {
                    "sender": sender,
                    "conversations": [conversation],
                },
            )

        result = VerifiedLinkedInDelivery(
            settings.browser_data_dir, expected_sender=sender, observe=observe if sender else None
        ).execute(job)
    except BrowserPreflightError as error:
        if requires_manual_intervention(error):
            hold.pause()
        result = {
            "outcome": "not_sent",
            "proof": "No Send attempted: " + str(error).splitlines()[0][:350],
        }
    except Exception as error:
        if requires_manual_intervention(error):
            hold.pause()
        # Never retry a send after an unknown result; even a timeout may follow
        # a successful server-side action. Keep the original claim and proof.
        result = {
            "outcome": "uncertain",
            "proof": "Delivery could not be confirmed ("
            + type(error).__name__
            + "). Inspect LinkedIn and record the actual activity before retrying.",
        }
    finally:
        stop.set()
        thread.join(timeout=35)
    save({"job": job, "result": result})
    acknowledge({"job": job}, result)
