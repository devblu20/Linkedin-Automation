"""Unit tests for safe YAML loading and validation errors."""

from pathlib import Path

import pytest

from linkedin_automation.application.errors import ConfigurationError
from linkedin_automation.infrastructure.config import YamlSearchDefinitionLoader


def test_loads_valid_example_configuration() -> None:
    definition = YamlSearchDefinitionLoader().load(Path("config/search.example.yaml"))
    assert definition.version == 1
    assert definition.name == "india-ai-engineering-leads"
    assert definition.search.positive_count > 0


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="not found"):
        YamlSearchDefinitionLoader().load(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("content", "message"),
    [("", "empty"), ("name: [broken", "malformed YAML"), ("- one\n- two\n", "mapping")],
)
def test_rejects_invalid_yaml_documents(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "search.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=message):
        YamlSearchDefinitionLoader().load(path)


def test_validation_error_includes_field_location(tmp_path: Path) -> None:
    path = tmp_path / "search.yaml"
    path.write_text("version: 3\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="version"):
        YamlSearchDefinitionLoader().load(path)
