"""Minimal, test-friendly application logging configuration."""

import logging
import os

_LOG_LEVEL_ENVIRONMENT_VARIABLE = "LINKEDIN_AUTOMATION_LOG_LEVEL"
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str | None = None) -> None:
    """Configure stable event-oriented logging without sensitive payloads."""
    raw_level = (
        level if level is not None else os.getenv(_LOG_LEVEL_ENVIRONMENT_VARIABLE, "WARNING")
    )
    configured_level = raw_level.upper()
    if configured_level not in _VALID_LOG_LEVELS:
        configured_level = "WARNING"
    logging.basicConfig(
        level=configured_level,
        format="%(asctime)s %(levelname)s %(name)s event=%(message)s",
        force=True,
    )
