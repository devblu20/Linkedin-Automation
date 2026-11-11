"""Campaign-based public index discovery, without LinkedIn login or browser access."""

import json
import re
import time
from dataclasses import replace
from html import unescape
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.domain.services import (
    canonicalize_linkedin_profile_url,
    evaluate_candidate,
)
from linkedin_automation.infrastructure.public_profile_details import (
    add_public_evidence,
    public_profile_details,
    strip_profile_boilerplate,
)
from linkedin_automation.infrastructure.run_sync import _NoRedirect

_RATE_LOCK = Lock()
_LAST_REQUEST = 0.0


class DiscoveryError(ValueError):
    pass


def clean(value, maximum=3000):
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())[:maximum]


def public_queries(definition):
    criteria = definition.search
    subjects = list(
        dict.fromkeys([*criteria.keywords.include, *criteria.companies, *criteria.industries])
    )
    roles = criteria.titles.include

    def quote(value):
        return '"' + clean(value, 60).replace('"', "").rstrip(". ") + '"'

    location = " OR ".join(quote(x) for x in criteria.locations[:3])
    suffix = f" ({location})" if location else ""
    terms = subjects[:8] or roles[:8]
    queries = []
    # Precise role + business queries precede broad discovery. Interleave broad
    # subjects so an over-specific role cannot consume the entire request budget.
    for i, term in enumerate(terms):
        if roles and subjects:
            queries.append(
                f"site:linkedin.com/in/ {quote(roles[i % len(roles)])} {quote(term)}{suffix}"
            )
        queries.append(f"site:linkedin.com/in/ {quote(term)}{suffix}")
    if subjects:
        queries.extend(
            f"site:linkedin.com/in/ {quote(role)} {quote(subjects[i % len(subjects)])}{suffix}"
            for i, role in enumerate(roles[:8])
        )
    return list(dict.fromkeys(queries))[:12]


def parse_public_result(item, query, definition):
    """Use explicit public title/snippet evidence; missing facts remain unknown."""
    if not isinstance(item, dict):
        return None
    raw_url = item.get("url", "")
    if not isinstance(raw_url, str):
        return None
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").casefold()
    if host not in {"www.linkedin.com", "linkedin.com"} and not re.fullmatch(
        r"[a-z]{2,3}\.linkedin\.com", host
    ):
        return None
    try:
        url = canonicalize_linkedin_profile_url("https://www.linkedin.com" + parsed.path)
    except ValueError:
        return None
    title = clean(item.get("title"), 500)
    excerpts = [strip_profile_boilerplate(clean(item.get("description")))]
    extra = item.get("extra_snippets", [])
    if isinstance(extra, list):
        excerpts.extend(
            strip_profile_boilerplate(clean(value, 600))
            for value in extra[:5]
            if isinstance(value, str)
            and re.search(
                r"\b(?:location|experience|education|current (?:role|title)|company|skills|"
                r"industry|about|based in|connections|followers)\b",
                value,
                re.I,
            )
        )
    snippet = "\n".join(dict.fromkeys(x for x in excerpts if x))[:6000]
    title = re.sub(r"\s*[|\u2013-]\s*LinkedIn\s*$", "", title, flags=re.I)
    pieces = re.split(r"\s+[\u2013-]\s+|\s*\|\s*", title, maxsplit=1)
    name = pieces[0].strip()
    if len(name.split()) < 2 or name.casefold() in {"linkedin member", "linkedin user"}:
        return None
    headline = pieces[1] if len(pieces) > 1 else ""
    details = public_profile_details(title, snippet, name)
    evidence_text = headline + " " + snippet
    # The query itself is not evidence that the person lives in its target location.
    locations = [
        value
        for value in definition.search.locations
        if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", evidence_text, re.I)
    ]
    candidate = LeadCandidate(
        profile_url=url,
        # Names and query terms are not evidence of a person's business or role.
        full_name="",
        headline=headline + " " + snippet,
        current_title=details["current_role"],
        company=details["company"],
        industry=details["industry"],
        location=", ".join(locations),
        source_search="Brave public index: " + query,
    )
    match = evaluate_candidate(candidate, definition)
    # A snippet may mention clients, colleagues or previous jobs. Only the public
    # headline can supply role evidence; still expose it as unverified index data.
    role_match = evaluate_candidate(replace(candidate, headline=headline), definition)
    matched = [x for x in match.matched if not x.startswith("title:")]
    matched.extend(x for x in role_match.matched if x.startswith("title:"))
    excluded = [x for x in match.excluded if not x.startswith("title:") and x != "required:titles"]
    excluded.extend(
        x for x in role_match.excluded if x.startswith("title:") or x == "required:titles"
    )
    explicit_exclusions = [x for x in excluded if not x.startswith("required:")]
    if explicit_exclusions:
        return None
    # A location-only hit is not business relevance. Avoid filling targets with
    # unrelated people when the index ignores part of the query.
    business_criteria = any(
        (
            definition.search.titles.include,
            definition.search.keywords.include,
            definition.search.industries,
            definition.search.companies,
        )
    )
    if business_criteria and not any(not x.startswith("location:") for x in matched):
        return None
    evidence_status = "required_evidence_matched" if not excluded else "needs_verification"
    return {
        "name": name,
        "profile_url": url,
        "public_title": title,
        "snippet": snippet,
        "source": "Brave public web index",
        "query": query,
        "matched": matched,
        "missing": [x.removeprefix("required:") for x in excluded],
        "evidence_status": evidence_status,
        "review_status": "Required public evidence matches; current details unverified"
        if not excluded
        else "Needs verification: incomplete public evidence",
        "company": details["company"],
        "current_role": details["current_role"],
        "location": details["location"],
        "profile_details": details,
        "customer_need": "Not verified",
    }


class BraveSearch:
    def __init__(self, key):
        self.key = key

    def search(self, query, offset):
        global _LAST_REQUEST
        if not self.key:
            raise DiscoveryError("App owner must configure BRAVE_SEARCH_API_KEY.")
        if not 0 <= offset <= 9:
            raise DiscoveryError("Public search pagination exceeds the provider limit.")
        with _RATE_LOCK:
            time.sleep(max(0, 1.1 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
            request = Request(
                "https://api.search.brave.com/res/v1/web/search?"
                + urlencode(
                    {
                        "q": query,
                        "count": 20,
                        "offset": offset,
                        "safesearch": "moderate",
                        "extra_snippets": "true",
                    }
                ),
                headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
            )
            try:
                with build_opener(_NoRedirect).open(request, timeout=25) as response:
                    raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise DiscoveryError("Public search response was too large.")
                result = json.loads(raw)
            except HTTPError as error:
                if error.code in {401, 403}:
                    raise DiscoveryError("Brave API key or subscription needs attention.") from None
                if error.code in {402, 429}:
                    raise DiscoveryError(
                        "Brave quota/rate limit reached. No automatic retry."
                    ) from None
                raise DiscoveryError(
                    "Public search provider unavailable. No automatic retry."
                ) from None
            except (URLError, TimeoutError, OSError, ValueError):
                raise DiscoveryError(
                    "Public search could not complete. No automatic retry."
                ) from None
        web = result.get("web", {}) if isinstance(result, dict) else {}
        rows = web.get("results", []) if isinstance(web, dict) else []
        if not isinstance(rows, list):
            raise DiscoveryError("Public search response had an unexpected format.")
        return rows[:20]


def discover(definition, target, provider, progress, max_requests=25, stop_at_target=False):
    if not 1 <= max_requests <= 25:
        raise ValueError("Choose a search request budget from 1 to 25.")
    rows, best, calls, stale = [], {}, 0, 0
    queries = public_queries(definition)

    def rank(row):
        return (-len(row["missing"]), len({x.split(":", 1)[0] for x in row["matched"]}))

    # Keep the strongest independently observed evidence for each canonical URL.
    # Never combine different snippets to manufacture a fully matched profile.
    exhausted = set()
    for offset in range(3):
        for query in queries:
            if query in exhausted:
                continue
            if calls >= max_requests or (
                len(rows) >= target and (stop_at_target or not any(x["missing"] for x in rows))
            ):
                return rows, calls
            calls += 1
            progress(rows, calls)
            batch = provider.search(query, offset)
            if not batch:
                exhausted.add(query)
            before = [(x["profile_url"], rank(x)) for x in rows]
            for item in batch:
                row = parse_public_result(item, query, definition)
                if row is None:
                    continue
                previous = best.get(row["profile_url"])
                if previous is None:
                    best[row["profile_url"]] = row
                elif rank(row) > rank(previous):
                    add_public_evidence(row, previous)
                    for excerpt in previous.get("additional_evidence", []):
                        add_public_evidence(row, {**excerpt, "name": previous["name"]})
                    best[row["profile_url"]] = row
                else:
                    add_public_evidence(previous, row)
            rows = sorted(best.values(), key=rank, reverse=True)[:target]
            after = [(x["profile_url"], rank(x)) for x in rows]
            stale = stale + 1 if after == before else 0
            progress(rows, calls)
            # Stop repetitive queries early instead of spending the whole cap.
            if stale >= 4:
                return rows, calls
    return rows, calls
