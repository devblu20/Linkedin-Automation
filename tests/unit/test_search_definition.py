"""Unit tests for strict search-definition domain validation."""

from typing import Any

import pytest
from pydantic import ValidationError

from linkedin_automation.domain.search_definition import SearchDefinition


def valid_definition() -> dict[str, Any]:
    return {
        "version": 1,
        "name": "  AI engineering leads  ",
        "search": {
            "titles": {
                "include": [" AI Engineer ", "ai engineer", "ML Engineer"],
                "exclude": [" Intern ", "intern"],
            },
            "locations": [" India ", "india"],
            "industries": [],
            "companies": [],
            "keywords": {"include": [], "exclude": []},
        },
        "limits": {"max_results": 100, "max_pages": 10},
        "output": {
            "file_name": "leads.xlsx",
            "drive_folder_id_env": "GOOGLE_DRIVE_FOLDER_ID",
        },
    }


def test_normalizes_whitespace_and_deduplicates_stably() -> None:
    definition = SearchDefinition.model_validate(valid_definition())
    assert definition.name == "AI engineering leads"
    assert definition.search.titles.include == ["AI Engineer", "ML Engineer"]
    assert definition.search.titles.exclude == ["Intern"]
    assert definition.search.locations == ["India"]


def test_rejects_unknown_fields() -> None:
    data = valid_definition()
    data["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SearchDefinition.model_validate(data)


@pytest.mark.parametrize("version", [0, 3, "1", True])
def test_rejects_unsupported_or_coerced_version(version: object) -> None:
    data = valid_definition()
    data["version"] = version
    with pytest.raises(ValidationError):
        SearchDefinition.model_validate(data)


def test_version_two_requires_qualification() -> None:
    data = valid_definition()
    data["version"] = 2
    with pytest.raises(ValidationError, match="requires qualification"):
        SearchDefinition.model_validate(data)


def test_requires_positive_search_criterion() -> None:
    data = valid_definition()
    data["search"] = {
        "titles": {"include": [], "exclude": ["Intern"]},
        "locations": [],
        "industries": [],
        "companies": [],
        "keywords": {"include": [], "exclude": ["Student"]},
    }
    with pytest.raises(ValidationError, match="at least one positive"):
        SearchDefinition.model_validate(data)


def test_rejects_whitespace_only_criterion() -> None:
    data = valid_definition()
    data["search"]["locations"] = ["  "]
    with pytest.raises(ValidationError):
        SearchDefinition.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_results", 0),
        ("max_results", 251),
        ("max_pages", 0),
        ("max_pages", 26),
        ("max_daily_results", 0),
        ("max_daily_results", 501),
    ],
)
def test_rejects_limits_outside_policy(field: str, value: int) -> None:
    data = valid_definition()
    data["limits"][field] = value
    with pytest.raises(ValidationError):
        SearchDefinition.model_validate(data)


@pytest.mark.parametrize(
    "file_name", ["leads.csv", "../leads.xlsx", "folder/leads.xlsx", "C:\\leads.xlsx"]
)
def test_rejects_unsafe_or_non_xlsx_file_names(file_name: str) -> None:
    data = valid_definition()
    data["output"]["file_name"] = file_name
    with pytest.raises(ValidationError):
        SearchDefinition.model_validate(data)


@pytest.mark.parametrize("environment_name", ["folder-id", "1_FOLDER", "DriveFolder", "A B"])
def test_rejects_invalid_drive_environment_variable_name(environment_name: str) -> None:
    data = valid_definition()
    data["output"]["drive_folder_id_env"] = environment_name
    with pytest.raises(ValidationError):
        SearchDefinition.model_validate(data)
