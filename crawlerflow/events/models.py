from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventName(StrEnum):
    WORKFLOW_STARTED = "workflow.started"
    WORKFLOW_FINISHED = "workflow.finished"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    STEP_FAILED = "step.failed"
    RETRY_STARTED = "retry.started"
    RETRY_FINISHED = "retry.finished"
    REQUEST_STARTED = "request.started"
    REQUEST_FINISHED = "request.finished"


@dataclass(slots=True, frozen=True)
class WorkflowEvent:
    """Immutable event emitted during workflow execution."""

    name: EventName
    workflow_name: str
    step_name: str | None = None
    step_index: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

