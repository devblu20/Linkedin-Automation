"""Read-only reply work in the same sequential owner worker as search and delivery."""

from contextlib import suppress
from threading import Event, Thread

from linkedin_automation.infrastructure.browser.conversations import BrowserConversationReader
from linkedin_automation.infrastructure.browser_hold import (
    BrowserHold,
    requires_manual_intervention,
)
from linkedin_automation.infrastructure.runtime import RuntimeSettings


def run_browser_reply_sync(client):
    hold = BrowserHold(RuntimeSettings.from_environment().browser_data_dir)
    if hold.active:
        return
    job = client.request("/api/outreach/browser/sync/claim")["job"]
    if not job:
        return
    result = {
        "id": job["id"],
        "token": job["token"],
        "sender": job["sender"],
        "conversations": [],
        "error": "",
    }
    stop = Event()

    def heartbeat():
        while not stop.wait(10):
            with suppress(ValueError):
                client.request("/api/search/heartbeat")

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        result["conversations"] = BrowserConversationReader(
            RuntimeSettings.from_environment().browser_data_dir, job["sender"]
        ).read(job["targets"])
    except Exception as error:
        if requires_manual_intervention(error):
            hold.pause()
        result["error"] = "Browser reply check stopped. Inspect LinkedIn before retrying."
    finally:
        stop.set()
        thread.join(timeout=35)
    client.request("/api/outreach/browser/sync/complete", result)
