"""Real Chrome, isolated contexts, intercepted fixture pages only; no LinkedIn traffic."""
# ruff: noqa: E501 - embedded HTML fixtures.

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from linkedin_automation.infrastructure.browser.conversations import (
    BrowserPreflightError,
    conversation_snapshot,
    verify_sender,
)
from linkedin_automation.infrastructure.browser.delivery import VerifiedLinkedInDelivery

SENDER = "https://www.linkedin.com/in/owner-example/"
TARGET = "https://www.linkedin.com/in/example-person/"
HTML = """<meta charset="utf-8"><title>Example Person | LinkedIn</title><nav>
<button onclick="document.querySelector('#me').hidden=!document.querySelector('#me').hidden">Me</button>
<a id="me" href="SENDER" hidden>View profile</a></nav><main><section>
<h1>Example Person</h1><p>· 1st</p><button onclick="document.querySelector('#chat').style.display='block'">Message</button>
</section><div role="dialog" id="chat" class="msg-overlay-conversation-bubble" style="display:none">
<header><a href="TARGET">Example Person</a></header><div class="msg-s-message-list">EVENT</div>
<div role="textbox" contenteditable="true" id="editor"></div>
<button onclick="const p=document.createElement('p');p.className='msg-s-event-listitem__body';p.textContent=document.querySelector('#editor').innerText;this.parentElement.append(p);document.querySelector('#editor').innerText=''">Send</button>
</div></main>"""
EVENT = """<div class="msg-s-event-listitem" data-event-urn="observed-message">
<a class="msg-s-message-group__profile-link" href="ACTOR">Sender</a>
<time datetime="2026-10-30T12:00:00+00:00">12:00</time>
<p class="msg-s-event-listitem__body">TEXT</p></div>"""


with TemporaryDirectory() as folder, sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    state = {"sender": SENDER, "actor": SENDER, "text": "An earlier outgoing message"}

    def context(*args, **kwargs):
        ctx = browser.new_context()
        event = EVENT.replace("ACTOR", state["actor"]).replace("TEXT", state["text"])
        html = HTML.replace("SENDER", state["sender"]).replace("TARGET", TARGET).replace("EVENT", event)
        ctx.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        return ctx

    @contextmanager
    def manager():
        yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=context))

    with patch("linkedin_automation.infrastructure.browser.delivery.sync_playwright", manager):
        observed = []

        def observe(value):
            observed.append(value)
            return {"complete": value["complete"], "blocked": value["incoming_detected"]}

        adapter = VerifiedLinkedInDelivery(Path(folder), expected_sender=SENDER, observe=observe)
        job = {"action": "message", "profile_url": TARGET, "name": "Example Person",
               "draft": "A separately approved fixture follow-up"}
        assert adapter.execute(job)["outcome"] == "message_sent"
        assert observed[-1]["messages"][0]["outgoing"] is True
        state.update(actor=TARGET, text="Please stop messaging me")
        try:
            adapter.execute(job)
            raise AssertionError("Incoming opt-out must stop the follow-up")
        except BrowserPreflightError:
            assert observed[-1]["incoming_detected"]
            assert observed[-1]["messages"][0]["outgoing"] is False
        state["sender"] = "https://www.linkedin.com/in/wrong-owner/"
        try:
            adapter.execute(job)
            raise AssertionError("Different sender must never deliver the authorized draft")
        except BrowserPreflightError:
            pass

    state["sender"] = SENDER
    ctx = context()
    page = ctx.new_page()
    verify_sender(page, SENDER)
    chat = page.locator("#chat")
    page.locator("time").evaluate("element => element.removeAttribute('datetime')")
    snapshot = conversation_snapshot(chat, TARGET, SENDER)
    assert snapshot["incoming_detected"] and not snapshot["complete"]
    assert snapshot["messages"] == []  # No invented timestamp or message evidence.
    ctx.close()
    browser.close()
print("PASS: sender pin, verified reply direction/time, opt-out preflight, message proof and incomplete-history block")
