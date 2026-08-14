"""Port for remote artifact storage."""

from pathlib import Path
from typing import Protocol

from linkedin_automation.domain.models import StoredArtifact


class ArtifactStorage(Protocol):
    """Upload a local artifact with an idempotency key."""

    def upload(self, path: Path, *, folder_id: str, idempotency_key: str) -> StoredArtifact: ...
