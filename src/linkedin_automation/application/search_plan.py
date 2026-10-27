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
    return [
        (terms[index % len(terms)], (index // len(terms)) + 1)
        for index in range(definition.limits.max_pages)
    ]
