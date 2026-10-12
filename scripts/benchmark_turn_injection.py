#!/usr/bin/env python3
"""Benchmark sequential vs parallel turn context injection latency."""
from __future__ import annotations

import asyncio
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from call_planner import CallPlanner
from conversational_iq import TopicMemory
from knowledge_pack import preload_knowledge_packs
from sales_playbook import DealStageTracker
from turn_context_injection import (
    clone_turn_state,
    compute_turn_injections_parallel,
    compute_turn_injections_sequential,
    get_turn_inject_latency_stats,
    record_turn_inject_latency,
)

TRANSCRIPTS = [
    "Namaste, mujhe Mumbai mein 3BHK chahiye budget 2 crore.",
    "Godrej project ke baare mein details batao, site visit karna hai Saturday.",
    "Bahut mehnga hai, doosre options bhi dekh rahe hain.",
    "Kya EMI banegi agar 1.5 crore ka loan lete hain?",
    "Tell me about parking and amenities in the project.",
    "Book kar do site visit kal 11 baje.",
]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[idx]


async def _bench_path(
    *,
    parallel: bool,
    iterations: int,
) -> list[float]:
    from types import SimpleNamespace

    tools = SimpleNamespace(
        _re_lead_phone="+919999999999",
        buffer_calls=[],
        buffer_objection_handled=lambda *args, **kwargs: None,
    )
    base = {
        "lang_preset": "hme",
        "_topic_memory": TopicMemory(),
        "_call_planner": CallPlanner(),
        "_deal_tracker": DealStageTracker(),
    }
    fn = compute_turn_injections_parallel if parallel else compute_turn_injections_sequential
    timings: list[float] = []

    for i in range(iterations):
        cfg = clone_turn_state(base)
        transcript = TRANSCRIPTS[i % len(TRANSCRIPTS)]
        tc = (i % len(TRANSCRIPTS)) + 1
        start = time.perf_counter()
        await fn(
            transcript=transcript,
            tc=tc,
            max_t=40,
            live_config=cfg,
            agent_tools=tools,
        )
        timings.append((time.perf_counter() - start) * 1000)

    return timings


async def main() -> None:
    preload_knowledge_packs()
    iterations = int(os.getenv("BENCH_ITERATIONS", "50"))

    print(f"Benchmarking turn injection ({iterations} iterations per path)...")
    seq_ms = await _bench_path(parallel=False, iterations=iterations)
    par_ms = await _bench_path(parallel=True, iterations=iterations)

    def _report(label: str, vals: list[float]) -> None:
        print(
            f"  {label}: p50={_percentile(vals, 50):.2f}ms "
            f"p95={_percentile(vals, 95):.2f}ms "
            f"p99={_percentile(vals, 99):.2f}ms "
            f"mean={sum(vals) / len(vals):.2f}ms"
        )

    print("\nResults:")
    _report("sequential", seq_ms)
    _report("parallel  ", par_ms)
    speedup = sum(seq_ms) / sum(par_ms) if sum(par_ms) else 0
    print(f"\nWall-clock speedup (total): {speedup:.2f}x")

    # Also populate module-level stats recorder
    for ms in seq_ms:
        record_turn_inject_latency(ms, parallel=False)
    for ms in par_ms:
        record_turn_inject_latency(ms, parallel=True)
    print(f"\nModule stats: {get_turn_inject_latency_stats()}")


if __name__ == "__main__":
    asyncio.run(main())