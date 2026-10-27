"""Editable search requirements compiled against a trusted qualification template."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from linkedin_automation.domain.search_definition import (
    IncludeExcludeCriteria,
    SearchCriteria,
    SearchDefinition,
)

Term = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Dimension = Literal["titles", "locations", "industries", "companies", "keywords"]


def definition_hash(definition: SearchDefinition) -> str:
    return hashlib.sha256(
        json.dumps(
            definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


class CampaignSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    template_id: str = Field(min_length=1, max_length=150)
    titles: list[Term] = Field(default_factory=list, max_length=30)
    excluded_titles: list[Term] = Field(default_factory=list, max_length=30)
    locations: list[Term] = Field(default_factory=list, max_length=20)
    industries: list[Term] = Field(default_factory=list, max_length=20)
    companies: list[Term] = Field(default_factory=list, max_length=30)
    keywords: list[Term] = Field(default_factory=list, max_length=40)
    excluded_keywords: list[Term] = Field(default_factory=list, max_length=30)
    required_dimensions: list[Dimension] = Field(default_factory=list, max_length=5)
    connection_degrees: list[Literal["1st", "2nd", "3rd"]] = Field(
        default_factory=list, max_length=3
    )
    profile_limit: int = Field(default=5, ge=1, le=25)
    page_limit: int = Field(default=3, ge=1, le=5)
    daily_limit: int = Field(default=25, ge=1, le=500)


class CampaignPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    spec: CampaignSpec
    template_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: int = Field(ge=1)


def spec_from_template(identifier: str, definition: SearchDefinition) -> CampaignSpec:
    search = definition.search
    return CampaignSpec(
        template_id=identifier,
        name=definition.name,
        titles=search.titles.include,
        excluded_titles=search.titles.exclude,
        locations=search.locations,
        industries=search.industries,
        companies=search.companies,
        keywords=search.keywords.include,
        excluded_keywords=search.keywords.exclude,
        required_dimensions=search.required_dimensions,
        connection_degrees=search.connection_degrees,
        profile_limit=min(5, definition.limits.max_results),
        page_limit=min(3, definition.limits.max_pages),
        daily_limit=min(50, definition.limits.max_daily_results),
    )


def compile_campaign(
    template: SearchDefinition, spec: CampaignSpec, identifier: str
) -> SearchDefinition:
    if identifier != spec.template_id:
        if not identifier.startswith("campaign-"):
            raise ValueError("Invalid saved campaign identifier")
        UUID(identifier.removeprefix("campaign-"))
    if (
        spec.profile_limit > template.limits.max_results
        or spec.page_limit > template.limits.max_pages
        or spec.daily_limit > template.limits.max_daily_results
    ):
        raise ValueError("Campaign limits cannot exceed the template's collection limits")
    values = template.model_dump()
    values["name"] = spec.name
    values["search"] = SearchCriteria(
        titles=IncludeExcludeCriteria(include=spec.titles, exclude=spec.excluded_titles),
        keywords=IncludeExcludeCriteria(include=spec.keywords, exclude=spec.excluded_keywords),
        locations=spec.locations,
        industries=spec.industries,
        companies=spec.companies,
        required_dimensions=spec.required_dimensions,
        connection_degrees=spec.connection_degrees,
    ).model_dump()
    values["limits"].update(
        max_results=spec.profile_limit,
        max_pages=spec.page_limit,
        max_daily_results=spec.daily_limit,
    )
    if identifier != spec.template_id:
        values["output"]["file_name"] = identifier + ".xlsx"
    try:
        return SearchDefinition.model_validate(values)
    except ValidationError as error:
        raise ValueError(
            "; ".join(item["msg"] for item in error.errors(include_input=False))
        ) from None
