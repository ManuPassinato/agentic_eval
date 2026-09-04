from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from agentic_eval.domain import (
    AttemptResult,
    HarnessCapabilities,
    QuestionCase,
    TraceEvent,
)

EventSink = Callable[[TraceEvent], Awaitable[None]]


class HarnessWorker(Protocol):
    worker_id: str

    async def start(self) -> None: ...

    async def health(self) -> dict[str, object]: ...

    async def execute(
        self,
        case: QuestionCase,
        prompt: str,
        attempt: int,
        timeout_seconds: float,
        event_sink: EventSink,
    ) -> AttemptResult: ...

    async def stop(self) -> None: ...


class HarnessAdapter(Protocol):
    name: str
    capabilities: HarnessCapabilities

    async def qualify(self, run_live_case: bool = True) -> dict[str, object]: ...

    async def create_worker(self, worker_index: int) -> HarnessWorker: ...

    async def close(self) -> None: ...
