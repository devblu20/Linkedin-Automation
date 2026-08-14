"""Unit tests for the framework-independent validation use case."""

from pathlib import Path

from linkedin_automation.application.validate_search import ValidateSearchDefinition
from linkedin_automation.domain.search_definition import SearchDefinition


class StubLoader:
    def __init__(self, definition: SearchDefinition) -> None:
        self.definition = definition
        self.received_path: Path | None = None

    def load(self, path: Path) -> SearchDefinition:
        self.received_path = path
        return self.definition


def test_returns_normalized_validation_summary() -> None:
    definition = SearchDefinition.model_validate(
        {
            "version": 1,
            "name": " Test leads ",
            "search": {
                "titles": {"include": ["Engineer"], "exclude": ["Intern"]},
                "locations": ["India"],
            },
            "output": {
                "file_name": "test.xlsx",
                "drive_folder_id_env": "GOOGLE_DRIVE_FOLDER_ID",
            },
        }
    )
    loader = StubLoader(definition)
    search_path = Path("search.yaml")

    summary = ValidateSearchDefinition(loader).execute(search_path)

    assert loader.received_path == search_path
    assert summary.name == "Test leads"
    assert summary.version == 1
    assert summary.positive_criteria == 2
    assert summary.excluded_criteria == 1
    assert summary.max_results == 100
    assert summary.max_pages == 10
    assert summary.max_daily_results == 250
