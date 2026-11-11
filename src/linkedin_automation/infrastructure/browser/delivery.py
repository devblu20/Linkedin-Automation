"""Verified browser delivery for explicitly authorized, named outreach jobs."""

import os
import re
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from linkedin_automation.application.exceptions import (
    AuthenticationRequiredError,
    SecurityChallengeError,
)
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.conversations import (
    BrowserPreflightError,
    conversation_snapshot,
    open_conversation,
    verify_sender,
)
from linkedin_automation.infrastructure.browser.linkedin import (
    PlaywrightLinkedInCollector,
    read_page_snapshot,
)
from linkedin_automation.infrastructure.browser.manual_login import wait_for_authenticated_page


def invitation_dialog(page):
    """LinkedIn can host its invitation modal in the preloaded first-party frame."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for frame in page.frames:
            if frame != page.main_frame and urlsplit(frame.url).hostname != "www.linkedin.com":
                continue
            try:
                for dialog in frame.get_by_role("dialog").all():
                    if (
                        dialog.is_visible()
                        and dialog.get_by_role(
                            "button", name=re.compile(r"^Add a note$", re.I)
                        ).count()
                    ):
                        return dialog
            except PlaywrightError:
                continue  # A preloaded frame may be replaced while the modal opens.
        page.wait_for_timeout(250)
    raise BrowserPreflightError("LinkedIn did not load the invitation-note form; nothing sent.")


class VerifiedLinkedInDelivery:
    def __init__(self, profile_dir: Path, channel="chrome", *, expected_sender=None, observe=None):
        self.profile_dir, self.channel = profile_dir, channel
        self.expected_sender, self.observe = expected_sender, observe

    def _open_recipient(self, page, target, *, allow_sign_in):
        def open_verified():
            if self.expected_sender:
                try:
                    verify_sender(page, self.expected_sender)
                except (AuthenticationRequiredError, SecurityChallengeError):
                    raise
                except Exception as error:
                    raise BrowserPreflightError(
                        "Cannot verify the approved LinkedIn sender; nothing sent."
                    ) from error
            page.goto(target, wait_until="domcontentloaded")
            PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))

        try:
            open_verified()
        except (AuthenticationRequiredError, SecurityChallengeError):
            if not allow_sign_in or not wait_for_authenticated_page(page, timeout_seconds=300):
                raise
            open_verified()

    def execute(self, job):
        target = canonicalize_linkedin_profile_url(job["profile_url"])
        attempted = False
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(self.profile_dir), channel=self.channel, headless=False
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                self._open_recipient(
                    page,
                    target,
                    allow_sign_in=bool(self.expected_sender)
                    and job["action"] in {"connection", "message"},
                )
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
                    pending = header.locator("button,a").filter(
                        has_text=re.compile(r"^\s*Pending\s*$", re.I)
                    )
                    if pending.count() and pending.first.is_visible():
                        return {
                            "outcome": "pending",
                            "proof": "Existing Pending invitation confirmed on recipient profile",
                        }
                    connect = header.get_by_role("button", name=re.compile(r"^Connect$", re.I))
                    if not connect.count():
                        connect = header.get_by_role(
                            "link", name=f"Invite {job['name']} to connect", exact=True
                        )
                        if connect.count():
                            vanity = parse_qs(
                                urlsplit(connect.first.get_attribute("href") or "").query
                            ).get("vanityName", [""])[0]
                            if (
                                canonicalize_linkedin_profile_url(
                                    "https://www.linkedin.com/in/" + vanity
                                )
                                != target
                            ):
                                raise BrowserPreflightError(
                                    "Invitation link does not match the approved recipient."
                                )
                        else:
                            header.get_by_role(
                                "button", name=re.compile(r"^More", re.I)
                            ).first.click()
                            connect = page.get_by_role(
                                "menuitem", name=re.compile(r"^Connect", re.I)
                            )
                    visible = [control for control in connect.all() if control.is_visible()]
                    if len(visible) != 1:
                        raise BrowserPreflightError(
                            "Cannot identify one visible recipient Connect control."
                        )
                    visible[0].click()
                    dialog = invitation_dialog(page)
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
                    attempted = True
                    # A successful submit may detach the invitation frame. Do not click
                    # again; Pending on this exact recipient is the only success proof.
                    with suppress(PlaywrightError):
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
                conversation, identifier = open_conversation(page, header, target, job["name"])
                textbox = conversation.locator('[contenteditable="true"][role="textbox"]').last
                textbox.wait_for(state="visible")
                if self.expected_sender:
                    snapshot = conversation_snapshot(
                        conversation,
                        target,
                        self.expected_sender,
                        recipient_identifier=identifier,
                        recipient_name=job["name"],
                        sender_name=os.getenv("LINKEDIN_AUTOMATION_SENDER_NAME"),
                    )
                    if not self.observe:
                        raise BrowserPreflightError(
                            "Reply verification is unavailable; no message sent."
                        )
                    try:
                        observed = self.observe(snapshot)
                    except Exception as error:
                        raise BrowserPreflightError(
                            "Cannot save the reply check; no message sent."
                        ) from error
                    reply_state = observed.get("reply_states", {}).get(target, {})
                    reviewed = bool(job.get("reviewed_reply_signature")) and (
                        reply_state.get("hold") == "Recipient replied"
                        and reply_state.get("signature") == job["reviewed_reply_signature"]
                    )
                    if (
                        not snapshot["complete"]
                        or not observed.get("complete")
                        or (
                            (snapshot["incoming_detected"] or observed.get("blocked"))
                            and not reviewed
                        )
                    ):
                        raise BrowserPreflightError(
                            "Recipient replied or reply check is incomplete; follow-up paused."
                        )
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
                attempted = True
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
            except Exception as error:
                if not attempted:
                    raise BrowserPreflightError(str(error)) from error
                raise
            finally:
                context.close()
