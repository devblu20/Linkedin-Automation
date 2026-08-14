"""Framework-independent domain models and policies."""

from linkedin_automation.domain.enums import RunStatus
from linkedin_automation.domain.models import Lead, LeadCandidate, ResearchRun
from linkedin_automation.domain.search_definition import SearchDefinition

__all__ = ["Lead", "LeadCandidate", "ResearchRun", "RunStatus", "SearchDefinition"]
