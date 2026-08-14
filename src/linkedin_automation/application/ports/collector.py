"""Port for supervised external lead collection."""

from typing import Protocol

from linkedin_automation.domain.models import CollectionBatch
from linkedin_automation.domain.search_definition import SearchDefinition


class LeadCollector(Protocol):
    """Collect a bounded page range from an authenticated source."""

    def collect(
        self, definition: SearchDefinition, *, start_page: int = 1, dry_run: bool = False
    ) -> CollectionBatch: ...
