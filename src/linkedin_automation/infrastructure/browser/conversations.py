"""Read visible one-to-one conversations; never infer missing senders or timestamps."""

import hashlib
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlsplit

from playwright.sync_api import sync_playwright

from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.linkedin import (
    PlaywrightLinkedInCollector,
    read_page_snapshot,
)


class BrowserPreflightError(ValueError):
    """A check failed before any outbound browser action was attempted."""


def verify_sender(page, expected):
    expected = canonicalize_linkedin_profile_url(expected)
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))
    menu = page.get_by_role("button", name=re.compile(r"(?:^|\s)Me$", re.I)).first
    menu.click()
    own = page.get_by_role("link", name="View profile", exact=True)
    own.wait_for(state="visible")
    if (
        own.count() != 1
        or canonicalize_linkedin_profile_url(own.get_attribute("href") or "") != expected
    ):
        raise BrowserPreflightError("Signed-in LinkedIn account differs from the approved sender.")
    menu.click()
    return expected


def recipient_header(page, target, name):
    target = canonicalize_linkedin_profile_url(target)
    page.goto(target, wait_until="domcontentloaded")
    PlaywrightLinkedInCollector._raise_for_safety_state(*read_page_snapshot(page))
    if canonicalize_linkedin_profile_url(page.url) != target:
        raise BrowserPreflightError("Recipient redirected; no message attempted.")
    heading = page.get_by_role("heading", name=name, exact=True).first
    heading.wait_for(state="visible")
    header = heading.locator("xpath=ancestor::section[1]")
    if not header.count():
        raise BrowserPreflightError("Cannot verify recipient profile header.")
    connected = bool(
        re.search(r"(?mi)^\s*[·•]?\s*1st\s*$", "\n".join(header.inner_text().splitlines()[:10]))
    )
    return header, connected


def open_conversation(page, header, target, name):
    controls = header.get_by_role("button", name=re.compile(r"^Message$", re.I))
    if not controls.count():
        controls = header.get_by_role("link", name="Message", exact=True)
        control = next((x for x in controls.all() if x.is_visible()), None)
        if control is None:
            raise BrowserPreflightError("No visible recipient Message link.")
        destination = urljoin(page.url, control.get_attribute("href") or "")
        parsed = urlsplit(destination)
        params = parse_qs(parsed.query)
        identifier = params.get("recipient", [""])[0]
        if (
            parsed.hostname != "www.linkedin.com"
            or parsed.path != "/messaging/compose/"
            or not re.fullmatch(r"ACo[A-Za-z0-9_-]+", identifier)
            or params.get("profileUrn") != ["urn:li:fsd_profile:" + identifier]
        ):
            raise BrowserPreflightError("Cannot bind the Message link to the verified profile.")
        # Follow the actual profile's own link, avoiding a fragile SPA overlay bridge.
        page.goto(destination, wait_until="load")
        conversation = page.locator(".msg-convo-wrapper").first
        conversation.locator('[contenteditable="true"][role="textbox"]').wait_for(state="visible")
        pills = conversation.locator(".msg-connections-typeahead__added-recipients .artdeco-pill")
        if (
            parse_qs(urlsplit(page.url).query).get("recipient") != [identifier]
            or pills.count() != 1
            or pills.get_by_text(name, exact=True).count() != 1
            or pills.get_attribute("aria-label") != "Remove " + name
        ):
            raise BrowserPreflightError(
                "Selected conversation recipient differs from the reviewed profile."
            )
        return conversation, identifier
    controls.first.click()
    conversation = page.locator(".msg-overlay-conversation-bubble").filter(has_text=name).last
    if not conversation.count():
        conversation = page.get_by_role("dialog").filter(has_text=name).last
    conversation.wait_for(state="visible")
    verify_conversation_target(conversation, target)
    return conversation, None


def verify_conversation_target(conversation, target):
    links = conversation.locator(
        "header a[href*='/in/'], .msg-overlay-bubble-header a[href*='/in/']"
    )
    urls = {
        canonicalize_linkedin_profile_url(x)
        for x in links.evaluate_all("items => items.map(x => x.href)")
    }
    if urls != {canonicalize_linkedin_profile_url(target)}:
        raise BrowserPreflightError("Cannot verify the one-to-one conversation recipient.")


def displayed_event_time(event, now):
    times = event.locator("time[datetime]")
    if times.count() == 1:
        date = datetime.fromisoformat(times.get_attribute("datetime").replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("Missing message timezone")
        return date
    clock = event.locator("time.msg-s-message-group__timestamp")
    if clock.count() != 1:
        raise ValueError("No readable message time")
    # Interpret displayed relative dates in the worker PC's verified local timezone.
    # Minutes remain minute-precision; missing dates/times are never replaced with 'now'.
    heading = event.evaluate("""e => {let n=e.closest('li');while(n){
      const time=n.querySelector('time.msg-s-message-list__time-heading');
      if(time)return time.innerText;n=n.previousElementSibling;}return null;}""")
    label = (heading or "").strip().casefold()
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    if label in {"today", "yesterday"}:
        day = now.date() - timedelta(days=1 if label == "yesterday" else 0)
    elif label in weekdays:
        # LinkedIn uses weekday headings for recent messages. Only resolve a
        # strictly preceding day within this week; same-day labels are ambiguous.
        offset = (now.weekday() - weekdays.index(label)) % 7
        if not offset:
            raise ValueError("Ambiguous same-day weekday heading")
        day = now.date() - timedelta(days=offset)
    else:
        day = None
        for pattern in ("%B %d, %Y", "%b %d, %Y", "%A, %B %d, %Y", "%Y-%m-%d"):
            try:
                day = datetime.strptime(label, pattern).date()
                break
            except ValueError:
                continue
        if day is None or day > now.date():
            raise ValueError("No supported displayed conversation date")
    hour = datetime.strptime(clock.inner_text().strip(), "%I:%M %p").time()
    return datetime.combine(day, hour, tzinfo=now.tzinfo)


def conversation_snapshot(
    conversation,
    target,
    sender,
    *,
    recipient_identifier=None,
    recipient_name=None,
    sender_name=None,
    now=None,
):
    target, sender = (canonicalize_linkedin_profile_url(x) for x in (target, sender))
    result = {"profile_url": target, "messages": [], "complete": True, "incoming_detected": False}
    events = conversation.locator(".msg-s-event-listitem")
    if not events.count():
        # An absent/unrecognized message list must not be treated as an empty conversation.
        empty = conversation.get_by_text(
            re.compile(r"^(No messages yet|Start a conversation)$", re.I)
        )
        result["complete"] = bool(empty.count())
        return result
    if events.count() > 100:
        result["complete"] = False
        return result
    for event in events.all():
        group = event.locator(
            "xpath=ancestor-or-self::*[contains(concat(' ',normalize-space(@class),' '),"
            "' msg-s-message-group ')][1]"
        )
        actor_links = event.locator(
            "a.msg-s-message-group__profile-link, a.msg-s-event-listitem__link"
        )
        if not actor_links.count() and group.count():
            actor_links = group.locator("a.msg-s-message-group__profile-link")
        actors = {
            canonicalize_linkedin_profile_url(x)
            for x in actor_links.evaluate_all("items => items.map(x => x.href)")
        }
        if recipient_identifier:
            recipient_alias = canonicalize_linkedin_profile_url(
                "https://www.linkedin.com/in/" + recipient_identifier
            )
            if actors == {recipient_alias}:
                actors = {target}
            elif (
                actors not in ({sender}, {target})
                and len(actors) == 1
                and sender_name
                and recipient_name
                and sender_name.casefold() != recipient_name.casefold()
            ):
                labels = {
                    " ".join(x.split()).casefold()
                    for x in event.locator(
                        ".msg-s-message-group__meta a, .msg-s-message-group__name"
                    ).all_inner_texts()
                }
                # Only the platform's author metadata, never message-body text, binds an alias.
                if sender_name.casefold() in labels and recipient_name.casefold() not in labels:
                    actors = {sender}
        if actors not in ({sender}, {target}):
            result["complete"] = False
            continue
        outgoing = actors == {sender}
        result["incoming_detected"] |= not outgoing
        body = event.locator(".msg-s-event-listitem__body")
        try:
            date = displayed_event_time(event, now or datetime.now().astimezone())
            text = "\n".join(body.all_inner_texts()) if body.count() else ""
            at = date.astimezone(UTC).isoformat()
            identifier = (
                event.get_attribute("data-event-urn")
                or hashlib.sha256(
                    (target + "\n" + at + "\n" + str(outgoing) + "\n" + text).encode()
                ).hexdigest()
            )
            result["messages"].append(
                {
                    "id": identifier,
                    "chat": target,
                    "sent_at": at,
                    "outgoing": outgoing,
                    "content": text,
                }
            )
        except (ValueError, TypeError, AttributeError):
            result["complete"] = False
    return result


class BrowserConversationReader:
    def __init__(self, profile_dir, sender, channel="chrome"):
        self.profile_dir, self.sender, self.channel = profile_dir, sender, channel

    def read(self, targets):
        results = []
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(self.profile_dir), channel=self.channel, headless=False
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                verify_sender(page, self.sender)
                for target in targets[:5]:
                    header, connected = recipient_header(
                        page, target["profile_url"], target["name"]
                    )
                    if not connected:
                        results.append(
                            {
                                "profile_url": target["profile_url"],
                                "messages": [],
                                "complete": True,
                                "incoming_detected": False,
                            }
                        )
                        continue
                    chat, identifier = open_conversation(
                        page, header, target["profile_url"], target["name"]
                    )
                    results.append(
                        conversation_snapshot(
                            chat,
                            target["profile_url"],
                            self.sender,
                            recipient_identifier=identifier,
                            recipient_name=target["name"],
                            sender_name=os.getenv("LINKEDIN_AUTOMATION_SENDER_NAME"),
                        )
                    )
                    close = chat.get_by_role("button", name=re.compile(r"^Close", re.I))
                    if close.count():
                        close.last.click()
                return results
            finally:
                context.close()
