"""Port for creating research-report artifacts."""

from pathlib import Path
from typing import Protocol

from linkedin_automation.domain.models import Lead, OutreachRecord, ResearchRun


class ReportWriter(Protocol):
    """Generate a verified local report for a run."""

    def write(
        self,
        run: ResearchRun,
        leads: list[Lead],
        outreach: list[OutreachRecord],
        file_name: str,
    ) -> Path: ...
