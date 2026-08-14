"""Use case for validating and summarizing a search-definition file."""

from dataclasses import dataclass
from pathlib import Path

from linkedin_automation.application.ports import SearchDefinitionLoader


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    """Framework-independent summary of a normalized search definition."""

    name: str
    version: int
    positive_criteria: int
    excluded_criteria: int
    max_results: int
    max_pages: int
    max_daily_results: int


class ValidateSearchDefinition:
    """Validate a search file through an injected loader."""

    def __init__(self, loader: SearchDefinitionLoader) -> None:
        self._loader = loader

    def execute(self, path: Path) -> ValidationSummary:
        """Load a definition and return its normalized summary."""
        definition = self._loader.load(path)
        return ValidationSummary(
            name=definition.name,
            version=definition.version,
            positive_criteria=definition.search.positive_count,
            excluded_criteria=definition.search.excluded_count,
            max_results=definition.limits.max_results,
            max_pages=definition.limits.max_pages,
            max_daily_results=definition.limits.max_daily_results,
        )
