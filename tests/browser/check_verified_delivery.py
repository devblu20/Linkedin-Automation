"""Offline Chrome checks of recipient verification and actual confirmation selectors."""
# ruff: noqa: E501 - embedded local-only LinkedIn-shaped fixtures.

import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright as real_playwright

from linkedin_automation.infrastructure.browser.delivery import VerifiedLinkedInDelivery

DRAFT="Hello Example, may we connect?"
FIXTURE="""<meta charset="utf-8"><title>Profile test</title><main><section><h2>Example Person</h2><p>· DEGREE</p><p>Independent Trader</p><p>London</p>
<button onclick="document.getElementById('invite').style.display='block'">Connect</button>
<button onclick="document.getElementById('chat').style.display='block'">Message</button></section>
<aside><h2>Other Person</h2><p>· 1st</p></aside>
<div role="dialog" id="invite" style="display:none"><button onclick="document.getElementById('note').style.display='block';document.getElementById('sendInvite').style.display='block'">Add a note</button>
<textarea id="note" maxlength="MAXIMUM" style="display:none"></textarea>
<button id="sendInvite" style="display:none" onclick="this.closest('[role=dialog]').style.display='none';const b=document.createElement('button');b.textContent='Pending';document.querySelector('section').append(b)">Send</button></div>
<div role="dialog" id="chat" class="msg-overlay-conversation-bubble" style="display:none"><header><a href="CHATPROFILE">Example Person</a></header>
<div role="textbox" contenteditable="true" id="message"></div>
<button onclick="const p=document.createElement('p');p.className='msg-s-event-listitem__body';p.textContent=document.getElementById('message').innerText;this.parentElement.append(p);document.getElementById('message').innerText=''">Send</button></div></main>"""

with tempfile.TemporaryDirectory() as directory, real_playwright() as p:
    browser=p.chromium.launch(channel="chrome",headless=True)
    state={"degree":"2nd","maximum":"200","recipient":"https://www.linkedin.com/in/example/"}
    def context(*args,**kwargs):
        value=browser.new_context()
        html=FIXTURE.replace("DEGREE",state["degree"]).replace("MAXIMUM",state["maximum"]).replace("CHATPROFILE",state["recipient"])
        value.route("**/*",lambda route:route.fulfill(status=200,content_type="text/html",body=html))
        return value
    @contextmanager
    def manager():
        yield SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=context))
    job={"profile_url":"https://www.linkedin.com/in/example/","name":"Example Person","draft":DRAFT,"action":"connection"}
    with patch("linkedin_automation.infrastructure.browser.delivery.sync_playwright",manager):
        adapter=VerifiedLinkedInDelivery(Path(directory))
        assert adapter.execute(job)["outcome"]=="pending"
        assert adapter.execute({**job,"action":"check"})["outcome"]=="not_connected"
        try:
            adapter.execute({**job,"action":"message"})
            raise AssertionError("Non-connected recipient must not receive messages")
        except ValueError as error:
            assert "acceptance" in str(error)
        state["degree"]="1st"
        assert adapter.execute({**job,"action":"message"})["outcome"]=="message_sent"
        state["recipient"]="https://www.linkedin.com/in/wrong-person/"
        try:
            adapter.execute({**job,"action":"message"})
            raise AssertionError("Conversation must match the approved recipient")
        except ValueError as error:
            assert "recipient" in str(error)
        state.update(degree="2nd",maximum="5")
        try:
            adapter.execute(job)
            raise AssertionError("An approved draft must not be truncated or silently changed")
        except ValueError as error:
            assert "character limit" in str(error)
    browser.close()
    print("PASS: invitation pending proof, first-degree acceptance, exact message proof, recipient match and note limits")

