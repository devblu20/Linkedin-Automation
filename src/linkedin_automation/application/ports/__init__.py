"""Ports implemented by external adapters."""

from linkedin_automation.application.ports.collector import LeadCollector
from linkedin_automation.application.ports.reporting import ReportWriter
from linkedin_automation.application.ports.repository import ResearchRepository
from linkedin_automation.application.ports.search_definition_loader import (
    SearchDefinitionLoader,
)
from linkedin_automation.application.ports.storage import ArtifactStorage

__all__ = [
    "ArtifactStorage",
    "LeadCollector",
    "ReportWriter",
    "ResearchRepository",
    "SearchDefinitionLoader",
]
