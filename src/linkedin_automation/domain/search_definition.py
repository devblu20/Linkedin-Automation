"""Typed domain contract for lead-search definitions."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CleanString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
_ENVIRONMENT_VARIABLE_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _normalize_unique_values(values: list[str]) -> list[str]:
    """Trim and stably deduplicate non-empty strings, ignoring case."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("criterion values must not be empty or whitespace-only")
        key = trimmed.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(trimmed)
    return normalized


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and implicit type coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


class IncludeExcludeCriteria(StrictModel):
    """Positive and negative values for one search dimension."""

    include: list[CleanString] = Field(default_factory=list)
    exclude: list[CleanString] = Field(default_factory=list)

    @field_validator("include", "exclude", mode="after")
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        return _normalize_unique_values(values)


class SearchCriteria(StrictModel):
    """All supported LinkedIn people-search dimensions."""

    titles: IncludeExcludeCriteria = Field(default_factory=IncludeExcludeCriteria)
    locations: list[CleanString] = Field(default_factory=list)
    industries: list[CleanString] = Field(default_factory=list)
    companies: list[CleanString] = Field(default_factory=list)
    keywords: IncludeExcludeCriteria = Field(default_factory=IncludeExcludeCriteria)
    required_dimensions: list[
        Literal["titles", "locations", "industries", "companies", "keywords"]
    ] = Field(default_factory=list)

    @field_validator("locations", "industries", "companies", mode="after")
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        return _normalize_unique_values(values)

    @field_validator("required_dimensions", mode="after")
    @classmethod
    def normalize_required_dimensions(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @property
    def positive_count(self) -> int:
        """Return the number of normalized positive criterion values."""
        return sum(
            (
                len(self.titles.include),
                len(self.locations),
                len(self.industries),
                len(self.companies),
                len(self.keywords.include),
            )
        )

    @property
    def excluded_count(self) -> int:
        """Return the number of normalized excluded criterion values."""
        return len(self.titles.exclude) + len(self.keywords.exclude)


class CollectionLimits(StrictModel):
    """Conservative limits enforced by future collection phases."""

    max_results: int = Field(default=100, ge=1, le=250)
    max_pages: int = Field(default=10, ge=1, le=25)
    max_daily_results: int = Field(default=250, ge=1, le=500)
    max_daily_connections: int = Field(default=20, ge=1, le=50)
    max_daily_messages: int = Field(default=20, ge=1, le=50)


class OutputConfiguration(StrictModel):
    """Safe report naming and indirect Google Drive configuration."""

    file_name: CleanString
    drive_folder_id_env: CleanString

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        windows_path = PureWindowsPath(value)
        posix_path = PurePosixPath(value)
        if (
            windows_path.is_absolute()
            or posix_path.is_absolute()
            or windows_path.name != value
            or posix_path.name != value
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
        ):
            raise ValueError("file_name must be a plain file name without a directory path")
        if not value.casefold().endswith(".xlsx"):
            raise ValueError("file_name must use the .xlsx extension")
        return value

    @field_validator("drive_folder_id_env")
    @classmethod
    def validate_drive_environment_name(cls, value: str) -> str:
        if _ENVIRONMENT_VARIABLE_PATTERN.fullmatch(value) is None:
            raise ValueError("drive_folder_id_env must be an uppercase environment-variable name")
        return value


class QualificationConfiguration(StrictModel):
    """Company-level evidence and executive-ranking policy."""

    firm_types: list[CleanString] = Field(default_factory=list)
    minimum_funds_gbp: int = Field(default=1_000_000, ge=1)
    require_fund_evidence: bool = True
    executive_title_priority: list[CleanString] = Field(default_factory=list)
    minimum_score: int = Field(default=70, ge=0, le=100)
    company_size_min: int = Field(default=1, ge=1, le=10_000)
    company_size_max: int = Field(default=100, ge=1, le=10_000)
    self_employment_keywords: list[CleanString] = Field(default_factory=list)
    prefer_self_employed: bool = False

    @field_validator(
        "firm_types", "executive_title_priority", "self_employment_keywords", mode="after"
    )
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        return _normalize_unique_values(values)

    @model_validator(mode="after")
    def validate_company_size(self) -> QualificationConfiguration:
        if self.company_size_min > self.company_size_max:
            raise ValueError("company_size_min must not exceed company_size_max")
        return self


class OutreachConfiguration(StrictModel):
    """Templates for drafts that always require human review."""

    enabled: bool = False
    connection_template: CleanString = (
        "Hi {first_name}, I came across your work at {company} and would value connecting."
    )
    message_template: CleanString = (
        "Hi {first_name}, thank you for connecting. I would welcome a brief conversation "
        "about {company}."
    )
    manual_send_only: Literal[True] = True


class SearchDefinition(StrictModel):
    """Versioned, validated definition of a lead-research search."""

    version: Literal[1, 2]
    name: CleanString
    search: SearchCriteria
    limits: CollectionLimits = Field(default_factory=CollectionLimits)
    qualification: QualificationConfiguration | None = None
    outreach: OutreachConfiguration = Field(default_factory=OutreachConfiguration)
    output: OutputConfiguration

    @model_validator(mode="after")
    def require_positive_criterion(self) -> SearchDefinition:
        if self.search.positive_count == 0:
            raise ValueError("at least one positive search criterion is required")
        dimensions = {
            "titles": self.search.titles.include,
            "locations": self.search.locations,
            "industries": self.search.industries,
            "companies": self.search.companies,
            "keywords": self.search.keywords.include,
        }
        empty_required = [name for name in self.search.required_dimensions if not dimensions[name]]
        if empty_required:
            raise ValueError(
                f"required dimensions have no positive values: {', '.join(empty_required)}"
            )
        if self.version == 2 and self.qualification is None:
            raise ValueError("version 2 requires qualification settings")
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_version(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("version"), bool):
            raise ValueError("version must be the integer 1")
        return data
