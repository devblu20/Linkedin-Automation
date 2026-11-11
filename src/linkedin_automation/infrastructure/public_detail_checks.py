"""Automatic exact-profile public checks, using only the discovery's unused budget."""

from linkedin_automation.infrastructure.public_discovery import (
    DiscoveryError,
    discover,
    parse_public_result,
)
from linkedin_automation.infrastructure.public_profile_details import add_public_evidence


def discover_and_check(definition, target, provider, progress, max_requests=25):
    discovery_cap = max_requests - 1 if max_requests >= 4 else max_requests
    rows, calls = discover(
        definition, target, provider, progress, max_requests=discovery_cap, stop_at_target=True
    )
    for row in rows:
        row["automatic_detail_check"] = "Public excerpts parsed; missing fields not inferred"
    progress(rows, calls)
    # Check qualification-critical gaps first. Accept evidence only from the same
    # canonical profile and name, regardless of what the search engine returns.
    priority = sorted(
        rows,
        key=lambda row: (
            -sum(
                not row["profile_details"].get(key)
                for key in ("current_role", "company", "location")
            ),
            -len(row["profile_details"]["missing_fields"]),
        ),
    )
    detail_definition = definition.model_copy(
        update={
            "search": definition.search.model_copy(
                update={
                    "titles": definition.search.titles.model_copy(
                        update={"include": [], "exclude": []}
                    ),
                    "keywords": definition.search.keywords.model_copy(
                        update={"include": [], "exclude": []}
                    ),
                    "locations": [],
                    "industries": [],
                    "companies": [],
                    "required_dimensions": [],
                }
            )
        }
    )
    for row in priority:
        if calls >= max_requests:
            break
        if not row["profile_details"]["missing_fields"]:
            continue
        name = row["name"].replace('"', "")
        query = f'site:linkedin.com "{name}"'
        if len(query) > 400:
            continue
        calls += 1
        progress(rows, calls)
        try:
            batch = provider.search(query, 0)
        except DiscoveryError:
            row["automatic_detail_check"] = (
                "Additional public check stopped: provider unavailable/quota. No retry."
            )
            progress(rows, calls)
            break
        before = len(row["profile_details"]["missing_fields"])
        for item in batch:
            candidate = parse_public_result(item, query, detail_definition)
            if candidate:
                add_public_evidence(row, candidate)
        after = len(row["profile_details"]["missing_fields"])
        row["automatic_detail_check"] = (
            "Additional public lookup completed; "
            + str(before - after)
            + " missing fields found. Remaining fields need verification."
        )
        progress(rows, calls)
    return rows, calls
