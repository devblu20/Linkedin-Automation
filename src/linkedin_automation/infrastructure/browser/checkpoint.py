"""Private per-search checkpoints. Never accept checkpoints for changed search rules."""

from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, Field

from linkedin_automation.domain.models import LeadCandidate
from linkedin_automation.domain.search_definition import SearchDefinition


class BrowserProgress(BaseModel):
    definition_hash: str
    candidates: list[LeadCandidate] = Field(default_factory=list)
    seen_urls: set[str] = Field(default_factory=set)
    visited_steps: set[int] = Field(default_factory=set)
    last_page: int = 0
    profile_attempts: int = 0
    hidden_result_cards: int = 0
    restricted_search: bool = False


class BrowserCheckpoint:
    def __init__(self, path: Path | None, definition: SearchDefinition):
        self.path = path
        self.fingerprint = sha256(definition.model_dump_json().encode()).hexdigest()

    def load(self) -> BrowserProgress:
        if self.path is None or not self.path.exists():
            return BrowserProgress(definition_hash=self.fingerprint)
        state = BrowserProgress.model_validate_json(self.path.read_text(encoding="utf-8"))
        if state.definition_hash != self.fingerprint:
            raise ValueError("Saved search checkpoint belongs to different campaign rules")
        return state

    def save(self, state: BrowserProgress) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)
