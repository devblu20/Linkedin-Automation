"""Port for loading search definitions without coupling use cases to YAML."""

from pathlib import Path
from typing import Protocol

from linkedin_automation.domain.search_definition import SearchDefinition


class SearchDefinitionLoader(Protocol):
    """Load and validate a search definition from a filesystem path."""

    def load(self, path: Path) -> SearchDefinition:
        """Return the validated definition or raise ConfigurationError."""
        ...
