from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field
from typing import Any


def route_from_path(path: str) -> str:
    path = path or ""
    if "process_link" in path:
        return "process_link"
    if "process_photo" in path:
        return "process_photo"
    if "predict" in path:
        return "predict"
    if "dataset" in path:
        return "dataset"
    return path.strip("/") or "root"


@dataclass
class PerfCollector:
    route: str
    started_at: float = field(default_factory=time.perf_counter)
    spans: dict[str, float] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def span(self, name: str):
        collector = self

        class _Span:
            def __enter__(self) -> None:
                self._start = time.perf_counter()

            def __exit__(self, exc_type, exc, tb) -> None:
                collector.spans[name] = collector.spans.get(name, 0.0) + ((time.perf_counter() - self._start) * 1000)

        return _Span()

    def set(self, key: str, value: Any) -> None:
        self.extra[key] = value

    def finalize(self) -> dict[str, Any]:
        total_ms = (time.perf_counter() - self.started_at) * 1000
        return {"route": self.route, "total_ms": round(total_ms, 3), **self.extra, **{k: round(v, 3) for k, v in self.spans.items()}}


current_perf: contextvars.ContextVar[PerfCollector | None] = contextvars.ContextVar("current_perf", default=None)
