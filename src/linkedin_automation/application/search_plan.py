"""Bounded search planning shared by the dashboard and browser worker."""

from linkedin_automation.domain.search_definition import SearchDefinition


def build_people_search_steps(definition: SearchDefinition) -> list[tuple[str, int]]:
    """Try specific role phrases already covered by keywords, then cycle subject terms."""
    search = definition.search
    focused_roles = [
        title
        for title in search.titles.include
        if len(title.split()) >= 2
        and any(keyword.casefold() in title.casefold() for keyword in search.keywords.include)
    ]
    source = [*focused_roles, *search.keywords.include, *search.companies]
    if not source:
        source = list(search.industries) or list(search.titles.include)
    terms, seen = [], set()
    for term in source:
        if term.casefold() not in seen:
            terms.append(term)
            seen.add(term.casefold())
    if (
        "titles" in search.required_dimensions
        and search.titles.include
        and not focused_roles
        and (search.keywords.include or search.companies or search.industries)
    ):
        # Required roles should guide discovery as well as the final profile filter.
        # One role/subject pair per query avoids a long list being treated as one phrase.
        steps, counts = [], {}
        for index in range(definition.limits.max_pages):
            role = search.titles.include[index % len(search.titles.include)].replace('"', "")
            subject = terms[index % len(terms)].replace('"', "")
            query = f'"{role}" AND "{subject}"'
            counts[query] = counts.get(query, 0) + 1
            steps.append((query, counts[query]))
        return steps
    return [
        (terms[index % len(terms)], (index // len(terms)) + 1)
        for index in range(definition.limits.max_pages)
    ]
