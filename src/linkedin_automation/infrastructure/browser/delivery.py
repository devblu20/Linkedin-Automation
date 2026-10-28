"""Verified browser delivery for explicitly authorized, named outreach jobs."""

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.linkedin import (
    PlaywrightLinkedInCollector,
    read_page_snapshot,
)


class VerifiedLinkedInDelivery:
    def __init__(self, profile_dir: Path, channel="chrome"):
        self.profile_dir, self.channel = profile_dir, channel

    def execute(self, job):
        target = canonicalize_linkedin_profile_url(job["profile_url"])
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(self.profile_dir), channel=self.channel, headless=False
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                page.goto(target, wait_until="domcontentloaded")
                PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))
                if canonicalize_linkedin_profile_url(page.url) != target:
                    raise ValueError("Profile redirected; no delivery attempted")
                heading = page.get_by_role("heading", name=job["name"], exact=True).first
                heading.wait_for(state="visible")
                header = heading.locator("xpath=ancestor::section[1]")
                if not header.count():
                    raise ValueError("Cannot verify recipient profile header")
                text = header.inner_text()
                connected = bool(
                    re.search(r"(?mi)^\s*[·•]?\s*1st\s*$", "\n".join(text.splitlines()[:10]))
                )
                if job["action"] == "check":
                    return {
                        "outcome": "connected" if connected else "not_connected",
                        "proof": "Recipient's own profile header shows first-degree connection"
                        if connected
                        else "First-degree connection is not shown in recipient header",
                    }
                if job["action"] == "connection":
                    if connected:
                        return {
                            "outcome": "connected",
                            "proof": "Recipient is already connected; no invitation sent",
                        }
                    pending = header.get_by_role("button", name=re.compile(r"^Pending$", re.I))
                    if pending.count() and pending.first.is_visible():
                        return {
                            "outcome": "pending",
                            "proof": "Existing Pending invitation confirmed on recipient profile",
                        }
                    connect = header.get_by_role("button", name=re.compile(r"^Connect$", re.I))
                    if not connect.count():
                        header.get_by_role("button", name=re.compile(r"^More", re.I)).first.click()
                        connect = page.get_by_role("menuitem", name=re.compile(r"^Connect", re.I))
                    connect.first.click()
                    dialog = page.get_by_role("dialog").last
                    dialog.wait_for(state="visible")
                    note = dialog.get_by_role("button", name=re.compile(r"^Add a note$", re.I))
                    if not note.count():
                        raise ValueError(
                            "LinkedIn did not offer the approved invitation-note flow; nothing sent"
                        )
                    note.click()
                    editor = dialog.locator("textarea").first
                    editor.wait_for(state="visible")
                    maximum = editor.get_attribute("maxlength")
                    if maximum and len(job["draft"]) > int(maximum):
                        raise ValueError(
                            "Approved note exceeds the displayed character limit; nothing sent"
                        )
                    editor.fill(job["draft"])
                    dialog.get_by_role(
                        "button", name=re.compile(r"^Send(?: invitation)?$", re.I)
                    ).click()
                    pending.wait_for(state="visible", timeout=12000)
                    page.wait_for_timeout(1500)
                    PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))
                    if not pending.is_visible():
                        raise ValueError("Invitation confirmation disappeared; inspect LinkedIn")
                    return {
                        "outcome": "pending",
                        "proof": "Pending invitation confirmed after Send on recipient profile",
                    }
                if not connected:
                    raise ValueError("Connection acceptance is not confirmed; message not sent")
                header.get_by_role("button", name=re.compile(r"^Message$", re.I)).first.click()
                conversation = (
                    page.locator(".msg-overlay-conversation-bubble")
                    .filter(has_text=job["name"])
                    .last
                )
                if not conversation.count():
                    conversation = page.get_by_role("dialog").filter(has_text=job["name"]).last
                conversation.wait_for(state="visible")
                recipient = conversation.locator(
                    "header a[href*='/in/'], .msg-overlay-bubble-header a[href*='/in/']"
                ).first
                if (
                    not recipient.count()
                    or canonicalize_linkedin_profile_url(recipient.get_attribute("href") or "")
                    != target
                ):
                    raise ValueError(
                        "Cannot verify the conversation recipient's profile link; nothing sent"
                    )
                textbox = conversation.locator('[contenteditable="true"][role="textbox"]').last
                textbox.wait_for(state="visible")
                sent = conversation.locator(".msg-s-event-listitem__body").filter(
                    has_text=job["draft"]
                )
                if any(
                    " ".join(value.split()) == " ".join(job["draft"].split())
                    for value in sent.all_inner_texts()
                ):
                    raise ValueError(
                        "Matching message text already exists; verify manually before any resend"
                    )
                textbox.fill(job["draft"])
                conversation.get_by_role("button", name=re.compile(r"^Send$", re.I)).last.click()
                for _ in range(20):
                    page.wait_for_timeout(500)
                    exact = any(
                        " ".join(value.split()) == " ".join(job["draft"].split())
                        for value in sent.all_inner_texts()
                    )
                    if exact and not textbox.inner_text().strip():
                        return {
                            "outcome": "message_sent",
                            "proof": "Approved message confirmed in recipient conversation",
                        }
                raise ValueError("Message delivery could not be confirmed")
            finally:
                context.close()
