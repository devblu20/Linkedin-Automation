"""SQLite and PostgreSQL persistence adapters."""

from linkedin_automation.infrastructure.persistence.repository import (
    DatabaseResearchRepository,
    SqliteResearchRepository,
)

__all__ = ["DatabaseResearchRepository", "SqliteResearchRepository"]
