"""Safe YAML adapter for the search-definition loading port."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.domain.search_definition import SearchDefinition


def _format_validation_error(error: ValidationError) -> str:
    details: list[str] = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"]) or "document"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


class YamlSearchDefinitionLoader:
    """Load UTF-8 YAML and validate it as a search definition."""

    def load(self, path: Path) -> SearchDefinition:
        """Return a typed definition with user-facing configuration errors."""
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise ConfigurationError(f"search file not found: {path}") from error
        except (OSError, UnicodeError) as error:
            raise ConfigurationError(f"search file could not be read: {path}") from error

        if not content.strip():
            raise ConfigurationError(f"search file is empty: {path}")

        try:
            document: Any = yaml.safe_load(content)
        except yaml.YAMLError as error:
            problem = getattr(error, "problem", None) or "invalid YAML syntax"
            raise ConfigurationError(f"malformed YAML in {path}: {problem}") from error

        if not isinstance(document, dict):
            raise ConfigurationError(f"search file must contain a YAML mapping: {path}")

        try:
            return SearchDefinition.model_validate(document)
        except ValidationError as error:
            details = _format_validation_error(error)
            raise ConfigurationError(f"invalid search definition in {path}: {details}") from error
