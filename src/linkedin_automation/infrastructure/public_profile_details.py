"""Extract explicitly labeled public-index details; never infer missing profile facts."""

import re

_LABELS = (
    "location",
    "based in",
    "current role",
    "current title",
    "current company",
    "company",
    "industry",
    "about",
    "experience",
    "education",
    "skills",
    "connections",
    "followers",
)
_LABEL_PATTERN = "|".join(re.escape(value) for value in _LABELS)


def strip_profile_boilerplate(text):
    """Keep profile metadata before LinkedIn's generic footer/reposted feed text."""
    return re.split(
        r"\bView\s+.{0,180}?\bprofile\s+on\s+LinkedIn\b|"
        r"\ba professional community of\b|\bRead the full article\b",
        text,
        maxsplit=1,
        flags=re.I,
    )[0].strip()


def _excerpt_details(title, snippet, name):
    fields = {
        key: ""
        for key in (
            "location",
            "current_role",
            "company",
            "industry",
            "about",
            "connections",
            "followers",
        )
    }
    fields.update(experience=[], education=[], skills=[])
    evidence = {}
    text = strip_profile_boilerplate(snippet)

    def remember(key, value, source):
        if "..." in value or "…" in value:
            return
        value = value.strip(" \t\r\n.;|·")[:1000]
        if not value or "..." in value or "…" in value:
            return
        if key in {"skills", "education", "experience"}:
            values = [
                part.strip() for part in re.split(r"[;\n]" if key != "skills" else r"[,;]", value)
            ]
            fields[key] = list(dict.fromkeys(x for x in values if x))[:30]
        else:
            fields[key] = value
        evidence[key] = {"text": source[:1200], "status": "public_index_unverified"}

    # Labels must start a fragment or follow punctuation. A query or incidental
    # sentence such as 'clients in London' is never a person's location.
    pattern = re.compile(
        rf"(?:^|[\n|·;.!?]\s*)(?P<label>{_LABEL_PATTERN})\s*:\s*"
        rf"(?P<value>.*?)(?=$|[\n|·;]|\b(?:{_LABEL_PATTERN})\s*:)",
        re.I,
    )
    aliases = {
        "current title": "current_role",
        "current role": "current_role",
        "current company": "company",
        "based in": "location",
    }
    for match in pattern.finditer(text):
        key = aliases.get(match["label"].lower(), match["label"].lower())
        if not fields[key]:
            remember(key, match["value"], match.group(0))
    if not fields["location"]:
        match = re.search(r"(?:^|[.!?;|·]\s*)Based in\s+([^.!?;|·]+)", text, re.I)
        if match:
            remember("location", match[1], match[0])
    headline = str(title or "")
    if name and headline.startswith(name):
        headline = re.sub(r"^[\s|\u2013-]+", "", headline[len(name) :])
    # Only an explicit role-at-company headline supplies these fields. Experience
    # and education labels are never promoted into a current employer.
    match = re.fullmatch(r"([^|·]+?)\s+(?:at|@)\s+([^|·]+)", headline.strip(), re.I)
    if match and not re.search(r"\b(former|previous|past|ex-)\b", headline, re.I):
        if not fields["current_role"]:
            remember("current_role", match[1], headline)
        if not fields["company"]:
            remember("company", match[2], headline)
    for key in ("connections", "followers"):
        if not fields[key]:
            for fragment in re.split(r"[\n|·;]", text):
                fragment = fragment.strip(" .")
                fragment = re.sub(r"\s+on LinkedIn$", "", fragment, flags=re.I)
                number = r"[\d][\d,.]*(?:[kKmM])?\+?"
                if not re.fullmatch(
                    rf"{number}\s+(?:connections|followers)"
                    rf"(?:\s+(?:and\s+)?{number}\s+(?:connections|followers))?",
                    fragment,
                    re.I,
                ):
                    continue
                match = re.search(r"\b(" + number + r")\s+" + key + r"\b", fragment, re.I)
                if match:
                    remember(key, match[1], fragment)
                    break
    return {
        **fields,
        "field_evidence": evidence,
        "missing_fields": [key for key, value in fields.items() if not value],
    }


def _fill_missing(details, extra, query=None):
    # An excerpt describing a different job must not supply half of the current job.
    job_fields = ("current_role", "company")
    conflicting_job = any(
        details[key] and extra[key] and details[key].casefold() != extra[key].casefold()
        for key in job_fields
    )
    for key, evidence in extra["field_evidence"].items():
        if not details[key] and not (conflicting_job and key in job_fields):
            details[key] = extra[key]
            details["field_evidence"][key] = (
                evidence if query is None else {**evidence, "query": query}
            )
    details["missing_fields"] = [key for key in details["missing_fields"] if not details[key]]


def public_profile_details(title, snippet, name=""):
    fragments = str(snippet or "")[:6000].split("\n")
    details = _excerpt_details(title, fragments[0], name)
    for fragment in fragments[1:]:
        _fill_missing(details, _excerpt_details("", fragment, name))
    return details


def hydrate_public_details(item):
    """Rebuild details from retained excerpts; conflicting fields remain unchanged."""
    details = public_profile_details(item["public_title"], item["snippet"], item["name"])
    for excerpt in item.get("additional_evidence", [])[:5]:
        if excerpt.get("profile_url") != item["profile_url"]:
            continue
        extra = public_profile_details(
            excerpt.get("public_title", ""), excerpt.get("snippet", ""), item["name"]
        )
        _fill_missing(details, extra, excerpt.get("query", ""))
    return details


def add_public_evidence(row, candidate):
    """Keep a bounded excerpt only when it fills a gap for the same profile."""
    if (
        row["profile_url"] != candidate["profile_url"]
        or row["name"].casefold() != candidate["name"].casefold()
    ):
        return
    existing = row.get("additional_evidence", [])
    if len(existing) >= 5:
        return
    excerpt = {key: candidate[key] for key in ("profile_url", "public_title", "snippet", "query")}
    combined = [*existing, excerpt]
    details = hydrate_public_details({**row, "additional_evidence": combined})
    if len(details["missing_fields"]) >= len(row["profile_details"]["missing_fields"]):
        return
    row["additional_evidence"] = combined
    row["profile_details"] = details
    for key in ("company", "current_role", "location"):
        row[key] = details[key]
