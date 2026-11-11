"""Offline Chrome coverage of profile-bound compose recipients and visible local times."""

# ruff: noqa: E501 - rendered fixture HTML.
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from linkedin_automation.infrastructure.browser.conversations import (
    BrowserPreflightError,
    conversation_snapshot,
)
from linkedin_automation.infrastructure.browser.delivery import VerifiedLinkedInDelivery

SENDER = "https://www.linkedin.com/in/owner-example/"
TARGET = "https://www.linkedin.com/in/example-person/"
PROFILE = f"""<meta charset="utf-8"><nav><button onclick="document.querySelector('#me').hidden=!document.querySelector('#me').hidden">Me</button><a id="me" hidden href="{SENDER}">View profile</a></nav><main><section><h1>Example Person</h1><p>· 1st</p><a href="/messaging/compose/?recipient=ACoRecipient&amp;profileUrn=urn%3Ali%3Afsd_profile%3AACoRecipient">Message</a></section></main>"""
EVENT = """<li><div class="msg-s-event-listitem" data-event-urn="IDENTIFIER"><a class="msg-s-event-listitem__link" href="https://www.linkedin.com/in/ACTOR">View profile</a><div class="msg-s-message-group__meta"><a href="https://www.linkedin.com/in/ACTOR">NAME</a><time class="msg-s-message-group__timestamp">10:51 PM</time></div><p class="msg-s-event-listitem__body">CONTENT</p></div></li>"""
COMPOSE = """<meta charset="utf-8"><main><div class="msg-convo-wrapper"><div class="msg-connections-typeahead__added-recipients"><button class="artdeco-pill" aria-label="Remove PERSON"><span>PERSON</span></button>EXTRA</div><ul><li><time class="msg-s-message-list__time-heading">Today</time></li>EVENTS</ul><div role="textbox" contenteditable="true" id="editor"></div><button onclick="const p=document.createElement('p');p.className='msg-s-event-listitem__body';p.textContent=document.querySelector('#editor').innerText;this.parentElement.append(p);document.querySelector('#editor').innerText=''">Send</button></div></main>"""

os.environ["LINKEDIN_AUTOMATION_SENDER_NAME"] = "Owner Example"
with TemporaryDirectory() as directory, sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    state = {"incoming": False, "name": "Example Person", "group": False}

    def context(*args, **kwargs):
        ctx = browser.new_context()
        events = (
            EVENT.replace("IDENTIFIER", "outgoing-1")
            .replace("ACTOR", "ACoOwner")
            .replace("NAME", "Owner Example")
            .replace("CONTENT", "Existing invitation note")
        )
        if state["incoming"]:
            events += (
                EVENT.replace("IDENTIFIER", "incoming-1")
                .replace("ACTOR", "ACoRecipient")
                .replace("NAME", "Example Person")
                .replace("CONTENT", "TEST RECEIVED")
            )
        html = (
            COMPOSE.replace("PERSON", state["name"])
            .replace(
                "EXTRA",
                '<button class="artdeco-pill">Other person</button>' if state["group"] else "",
            )
            .replace("EVENTS", events)
        )
        ctx.route(
            "**/*",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=html if "/messaging/" in route.request.url else PROFILE,
            ),
        )
        return ctx

    @contextmanager
    def manager():
        yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=context))

    captured = []

    def observe(value):
        captured.append(value)
        return {"complete": value["complete"], "blocked": value["incoming_detected"]}

    job = {
        "action": "message",
        "name": "Example Person",
        "profile_url": TARGET,
        "draft": "Approved fixture test message",
    }
    with patch("linkedin_automation.infrastructure.browser.delivery.sync_playwright", manager):
        adapter = VerifiedLinkedInDelivery(Path(directory), expected_sender=SENDER, observe=observe)
        assert adapter.execute(job)["outcome"] == "message_sent"
        assert captured[-1]["complete"] and captured[-1]["messages"][0]["outgoing"]
        state["incoming"] = True
        try:
            adapter.execute(job)
            raise AssertionError("A received reply must pause the queued follow-up")
        except BrowserPreflightError:
            assert captured[-1]["incoming_detected"] and captured[-1]["complete"]

        def reviewed_observation(value):
            return {
                "complete": value["complete"],
                "blocked": True,
                "reply_states": {TARGET: {"hold": "Recipient replied", "signature": "reviewed-1"}},
            }

        reviewed = VerifiedLinkedInDelivery(
            Path(directory), expected_sender=SENDER, observe=reviewed_observation
        )
        assert (
            reviewed.execute({**job, "reviewed_reply_signature": "reviewed-1"})["outcome"]
            == "message_sent"
        )
        try:
            reviewed.execute({**job, "reviewed_reply_signature": "older-review"})
            raise AssertionError("A changed reply must invalidate a reviewed response")
        except BrowserPreflightError:
            pass
        state["group"] = True
        try:
            adapter.execute(job)
            raise AssertionError("A group recipient must not be used for one-to-one delivery")
        except BrowserPreflightError:
            pass
    state["group"] = False
    ctx = context()
    page = ctx.new_page()
    page.goto("https://www.linkedin.com/messaging/compose/")
    snapshot = conversation_snapshot(
        page.locator(".msg-convo-wrapper"),
        TARGET,
        SENDER,
        recipient_identifier="ACoRecipient",
        recipient_name="Example Person",
        sender_name="Owner Example",
        now=datetime(2026, 10, 30, 23, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    )
    assert snapshot["messages"][-1]["sent_at"] == "2026-10-30T17:21:00+00:00"
    assert not snapshot["messages"][-1]["outgoing"]
    ctx.close()
    browser.close()
print(
    "PASS: exact compose recipient, full-page send, alias author direction, displayed minute time and reply/group blocks"
)
