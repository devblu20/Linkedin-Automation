"""Bounded cloud collection using only the caller's server-verified LinkedIn account."""

from dataclasses import replace
from urllib.parse import urlsplit

from linkedin_automation.application.exceptions import CollectionError, LimitedVisibilityError
from linkedin_automation.application.search_plan import build_people_search_steps
from linkedin_automation.domain.models import CollectionBatch, LeadCandidate
from linkedin_automation.domain.services import canonicalize_linkedin_profile_url
from linkedin_automation.infrastructure.browser.linkedin import build_people_search_url
from linkedin_automation.infrastructure.unipile_client import UnipileError


def text(value, maximum=10000):
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or ""
    return value.strip()[:maximum] if isinstance(value, str) else ""


def profile_candidate(item, source):
    if not isinstance(item, dict):
        return None
    name = text(
        item.get("display_name")
        or item.get("name")
        or " ".join(filter(None, [text(item.get("first_name")), text(item.get("last_name"))]))
    )
    if not name or name.casefold() == "linkedin member":
        return None
    url = text(item.get("public_profile_url") or item.get("profile_url"))
    if not url and item.get("public_identifier"):
        url = "https://www.linkedin.com/in/" + text(item["public_identifier"], 255)
    try:
        url = canonicalize_linkedin_profile_url(url)
    except ValueError:
        return None
    specifics = item.get("specifics")
    specifics = specifics if isinstance(specifics, dict) else {}
    specific = specifics.get("linkedin")
    specific = specific if isinstance(specific, dict) else {}
    experience = (
        specific.get("experience")
        or item.get("work_experience")
        or item.get("current_positions")
        or []
    )
    experience = experience if isinstance(experience, list) else []
    current = next(
        (
            x
            for x in experience
            if isinstance(x, dict) and not x.get("ended_on") and not x.get("end")
        ),
        {},
    )
    summaries = tuple(
        " | ".join(
            filter(
                None,
                [
                    text(x.get("title") or x.get("position") or x.get("role")),
                    text(x.get("company")),
                    text(x.get("description")),
                ],
            )
        )
        for x in experience[:30]
        if isinstance(x, dict)
    )
    education = specific.get("education") or item.get("education") or []
    skills = specific.get("skills") or item.get("skills") or []
    education = education if isinstance(education, list) else []
    skills = skills if isinstance(skills, list) else []
    return LeadCandidate(
        profile_url=url,
        full_name=name[:255],
        headline=text(item.get("description") or item.get("headline")),
        current_title=text(
            current.get("title") or current.get("position") or current.get("role"), 255
        ),
        company=text(current.get("company"), 255),
        location=text(item.get("location"), 255),
        industry=text(item.get("industry"), 255),
        about=text(item.get("bio") or item.get("summary")),
        experience=summaries,
        education=tuple(
            text(x.get("school") or x.get("degree")) for x in education[:20] if isinstance(x, dict)
        ),
        skills=tuple(
            text(x.get("name") or x.get("text")) if isinstance(x, dict) else text(x)
            for x in skills[:50]
        ),
        connections=str(item.get("relations_count") or item.get("connections_count") or ""),
        followers=str(item.get("followers_count") or item.get("follower_count") or ""),
        source_search=source,
    )


class UnipileCollector:
    def __init__(self, client, account_id, allowed=lambda: True):
        self.client, self.account_id, self.allowed = client, account_id, allowed

    def check(self):
        if not self.allowed():
            raise CollectionError(
                "Workspace access or LinkedIn connection changed. Collection stopped."
            )

    def collect(self, definition, *, start_page=1, dry_run=False):
        if dry_run:
            return CollectionBatch((), 0, True)
        candidates, seen, pages, hidden, visits = [], set(), 0, 0, 0
        cursors = {}
        exhausted = set()
        try:
            for term, number in build_people_search_steps(definition):
                self.check()
                if term in exhausted:
                    continue
                if number > 1 and not cursors.get(term):
                    continue
                url = build_people_search_url(definition, 1, search_term=term)
                response = self.client.search(self.account_id, url, cursor=cursors.get(term))
                pages += 1
                entries = response.get("items", response.get("data", []))
                if not isinstance(entries, list):
                    raise CollectionError(
                        "Unipile returned an unsupported search response. No profiles guessed."
                    )
                cursor = response.get("next_cursor") or response.get("cursor")
                cursors[term] = cursor
                if not cursor:
                    exhausted.add(term)
                for item in entries:
                    self.check()
                    if not isinstance(item, dict):
                        continue
                    candidate = profile_candidate(item, url)
                    if candidate is None:
                        hidden += 1
                        continue
                    if candidate.profile_url in seen:
                        continue
                    seen.add(candidate.profile_url)
                    if visits >= definition.limits.max_results:
                        break
                    visits += 1
                    identifier = (
                        item.get("id")
                        or item.get("provider_id")
                        or urlsplit(candidate.profile_url).path.strip("/").split("/")[-1]
                    )
                    try:
                        profile = self.client.profile(self.account_id, identifier)
                    except UnipileError as error:
                        if error.status not in {404, 422}:
                            raise
                        # A stale/inaccessible detail page must not discard other results.
                        # Keep only the named fields actually returned by this search card.
                        profile = {}
                    profile = profile.get("data", profile) if isinstance(profile, dict) else {}
                    fresh = profile_candidate(profile, url)
                    if fresh and fresh.profile_url == candidate.profile_url:
                        # Detail responses can omit fields already visible in the search card.
                        # Merge only observations of this exact canonical profile, never guesses.
                        candidate = replace(
                            fresh,
                            **{
                                name: getattr(fresh, name) or getattr(candidate, name)
                                for name in (
                                    "headline",
                                    "current_title",
                                    "company",
                                    "location",
                                    "industry",
                                    "about",
                                    "experience",
                                    "education",
                                    "skills",
                                    "connections",
                                    "followers",
                                )
                            },
                        )
                    candidates.append(candidate)
                if visits >= definition.limits.max_results:
                    break
        except UnipileError as error:
            raise CollectionError(
                str(error), pages_visited=pages, hidden_result_cards=hidden
            ) from None
        if not candidates and hidden:
            raise LimitedVisibilityError(
                "LinkedIn returned only hidden or unreadable search results for this account. "
                "No profiles were saved. Compare this search in LinkedIn with the same account; "
                "Monthly limits are not confirmed by this result. "
                "Hidden identities are not unlocked.",
                pages_visited=pages,
                hidden_result_cards=hidden,
            )
        return CollectionBatch(tuple(candidates), pages, True, pages, hidden)


class KnownTestProfileCollector(UnipileCollector):
    """Fetch one explicitly supplied test profile; campaign matching still applies."""

    def __init__(self, client, account_id, profile_url, allowed=lambda: True):
        super().__init__(client, account_id, allowed)
        self.profile_url = canonicalize_linkedin_profile_url(profile_url)

    def collect(self, definition, *, start_page=1, dry_run=False):
        if dry_run:
            return CollectionBatch((), 0, True)
        self.check()
        identifier = urlsplit(self.profile_url).path.strip("/").split("/")[-1]
        try:
            data = self.client.profile(self.account_id, identifier)
        except UnipileError as error:
            raise CollectionError(str(error)) from None
        data = data.get("data", data) if isinstance(data, dict) else None
        candidate = profile_candidate(data, self.profile_url)
        if not candidate or candidate.profile_url != self.profile_url:
            raise CollectionError("Provider did not return the exact approved test profile.")
        self.check()
        return CollectionBatch((candidate,), 0, True)
