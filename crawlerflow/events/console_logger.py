"""Human-readable automatic step lifecycle logging."""

from __future__ import annotations

from collections.abc import Callable

from crawlerflow.events.bus import EventBus
from crawlerflow.events.models import EventName, WorkflowEvent


class ConsoleEventLogger:
    """Print step start, completion, failure, and duration automatically."""

    _event_names = (
        EventName.STEP_STARTED,
        EventName.STEP_FINISHED,
        EventName.STEP_FAILED,
    )

    def __init__(self, write: Callable[[str], None] = print) -> None:
        self._write = write

    def prepare(self) -> None:
        return None

    def attach(self, event_bus: EventBus) -> None:
        for event_name in self._event_names:
            event_bus.subscribe(event_name, self.handle)

    def detach(self, event_bus: EventBus) -> None:
        for event_name in self._event_names:
            event_bus.unsubscribe(event_name, self.handle)

    def handle(self, event: WorkflowEvent) -> None:
        path = ".".join(str(part) for part in event.payload.get("path", ()))
        location = f"step {path}" if path else "step"
        name = event.step_name or "unknown"
        if event.name is EventName.STEP_STARTED:
            self._write(f"[{location}] STARTED {name}")
            return

        duration = self._duration(event)
        if event.name is EventName.STEP_FAILED:
            error = event.payload.get("error", "Unknown error")
            self._write(f"[{location}] FAILED {name} ({duration}): {error}")
            return

        status = str(event.payload.get("status", "succeeded")).upper()
        attempts = int(event.payload.get("attempts", 1))
        attempt_text = f", {attempts} attempts" if attempts > 1 else ""
        self._write(f"[{location}] {status} {name} ({duration}{attempt_text})")

    @staticmethod
    def _duration(event: WorkflowEvent) -> str:
        duration_ms = float(event.payload.get("duration_ms", 0))
        return f"{duration_ms:.3f} ms"
