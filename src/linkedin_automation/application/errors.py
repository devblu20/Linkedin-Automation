"""Application-level exceptions safe to present at interfaces."""


class ConfigurationError(Exception):
    """A search-definition file could not be loaded or validated."""
