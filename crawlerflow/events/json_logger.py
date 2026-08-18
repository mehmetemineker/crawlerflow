"""JSON Lines sink for workflow lifecycle events."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from crawlerflow.events.bus import EventBus
from crawlerflow.events.models import WorkflowEvent


class JsonEventLogger:
    """Append structured workflow events to a JSON Lines file."""

    def __init__(
        self,
        path: Path,
        *,
        mode: str = "append",
        include_payload: bool = True,
    ) -> None:
        self.path = path
        self.mode = mode
        self.include_payload = include_payload
        self._lock = Lock()

    def prepare(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "overwrite":
            self.path.write_text("", encoding="utf-8")

    def attach(self, event_bus: EventBus) -> None:
        event_bus.subscribe_all(self.handle)

    def detach(self, event_bus: EventBus) -> None:
        event_bus.unsubscribe_all(self.handle)

    def handle(self, event: WorkflowEvent) -> None:
        record: dict[str, Any] = {
            "timestamp": event.occurred_at.isoformat(),
            "event": event.name.value,
            "workflow": event.workflow_name,
            "step": event.step_name,
            "step_index": event.step_index,
        }
        if self.include_payload:
            record["payload"] = event.payload
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{line}\n")

