"""Domain enumerations for research-run lifecycle management."""

from enum import StrEnum


class RunStatus(StrEnum):
    """Durable lifecycle states for a research run."""

    PENDING = "pending"
    COLLECTING = "collecting"
    PROCESSING = "processing"
    REPORTING = "reporting"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"


class OutreachStatus(StrEnum):
    """Human-controlled qualification and outreach lifecycle."""

    NEEDS_VERIFICATION = "needs_verification"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    CONNECTION_QUEUED = "connection_queued"
    CONNECTION_SENT = "connection_sent"
    CONNECTED = "connected"
    MESSAGE_READY = "message_ready"
    MESSAGE_SENT = "message_sent"
    REJECTED = "rejected"


ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.COLLECTING, RunStatus.FAILED}),
    RunStatus.COLLECTING: frozenset({RunStatus.PROCESSING, RunStatus.PAUSED, RunStatus.FAILED}),
    RunStatus.PAUSED: frozenset({RunStatus.COLLECTING, RunStatus.FAILED}),
    RunStatus.PROCESSING: frozenset({RunStatus.REPORTING, RunStatus.FAILED}),
    RunStatus.REPORTING: frozenset({RunStatus.UPLOADING, RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.UPLOADING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset({RunStatus.REPORTING, RunStatus.UPLOADING}),
    RunStatus.FAILED: frozenset({RunStatus.COLLECTING, RunStatus.REPORTING, RunStatus.UPLOADING}),
}


class RunStateError(ValueError):
    """A requested run status transition is not allowed."""


def ensure_transition(current: RunStatus, target: RunStatus) -> None:
    """Raise when a lifecycle transition is not explicitly allowed."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise RunStateError(f"cannot transition run from {current.value} to {target.value}")
