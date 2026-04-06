"""
Observability event bus — lightweight structured event emission.

The engine calls obs_emit() for every pipeline step. The chat route
subscribes via ObsSubscription to forward events as SSE JSON chunks.

Design: a simple per-request list. No background threads, no global state.
The Engine receives an optional ObsSubscription at process() call time.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ObsEvent:
    type: str
    data: dict

    def to_sse_dict(self) -> dict:
        return {"obs_event": {"type": self.type, "data": self.data}}


class ObsCollector:
    """
    Collects observability events during a single request.
    The engine holds a reference and emits into it.
    The chat route drains it between SSE chunks.
    """
    def __init__(self):
        self._events: list[ObsEvent] = []

    def emit(self, event_type: str, **data):
        self._events.append(ObsEvent(type=event_type, data={k: str(v) for k, v in data.items()}))

    def drain(self) -> list[ObsEvent]:
        events, self._events = self._events, []
        return events

    def all(self) -> list[ObsEvent]:
        return list(self._events)
