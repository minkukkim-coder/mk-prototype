"""Response-latency measurement and stats.

`measure_seconds()` is a context manager that times whatever runs inside it.
`summarize()` aggregates a list of recorded latencies into a small dict that
the sidebar renders. Per-model breakdowns are also supported.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class TimedResult:
    seconds: float = 0.0


@contextmanager
def measure_seconds() -> Iterator[TimedResult]:
    result = TimedResult()
    start = time.perf_counter()
    try:
        yield result
    finally:
        result.seconds = time.perf_counter() - start


def summarize(latencies: list[float]) -> dict:
    if not latencies:
        return {"count": 0}
    return {
        "count": len(latencies),
        "avg": sum(latencies) / len(latencies),
        "min": min(latencies),
        "max": max(latencies),
        "last": latencies[-1],
    }


def per_model_summary(messages: list[dict]) -> dict[str, dict]:
    """Group assistant messages by model_label, return {label: summary_dict}."""
    by_model: dict[str, list[float]] = {}
    for m in messages:
        if m.get("role") != "assistant" or "latency_s" not in m:
            continue
        by_model.setdefault(m.get("model_label", "?"), []).append(m["latency_s"])
    return {label: summarize(lats) for label, lats in by_model.items()}
