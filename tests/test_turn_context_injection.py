"""Equivalence tests: sequential vs parallel turn context injection."""
from __future__ import annotations

import os
import random
import sys
from types import SimpleNamespace

import pytest

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


def _make_agent_tools(phone: str = "+919999999999"):
    return SimpleNamespace(
        _re_lead_phone=phone,
        buffer_calls=[],
        buffer_objection_handled=lambda *args, **kwargs: None,
    )


def _base_live_config() -> dict:
    return {
        "lang_preset": "hme",
        "parallel_turn_injectors_enabled": False,
        "_topic_memory": TopicMemory(),
        "_call_planner": CallPlanner(),
        "_deal_tracker": DealStageTracker(),
    }


def _result_key(result) -> tuple:
    side = None
    if result.objection_side_effect:
        se = result.objection_side_effect
        side = (se.log_type, se.transcript_snippet, se.script_used, se.outcome)
    return (
        tuple(result.messages),
        result.emotion,
        result.record_objection_handled,
        side,
    )


def _state_snapshot(live_config: dict) -> dict:
    tm = live_config.get("_topic_memory")
    cp = live_config.get("_call_planner")
    dt = live_config.get("_deal_tracker")
    return {
        "topics": frozenset(tm._discussed_topics) if tm else frozenset(),
        "planner_phase": cp.phase.value if cp else None,
        "deal_stage": dt.stage if dt else None,
    }


@pytest.fixture(scope="module", autouse=True)
def _preload_knowledge():
    preload_knowledge_packs()


@pytest.fixture(autouse=True)
def _deterministic_random(monkeypatch):
    """Seq vs par each call random.choice once; pin to first option for comparison."""

    def _first_choice(seq):
        if not seq:
            raise IndexError("empty sequence")
        return seq[0]

    monkeypatch.setattr(random, "choice", _first_choice)
    monkeypatch.setattr(random, "random", lambda: 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript", TRANSCRIPTS)
async def test_sequential_parallel_equivalence(transcript: str):
    base = _base_live_config()
    tools = _make_agent_tools()

    seq_cfg = clone_turn_state(base)
    par_cfg = clone_turn_state(base)

    seq = await compute_turn_injections_sequential(
        transcript=transcript,
        tc=3,
        max_t=40,
        live_config=seq_cfg,
        agent_tools=tools,
    )
    par = await compute_turn_injections_parallel(
        transcript=transcript,
        tc=3,
        max_t=40,
        live_config=par_cfg,
        agent_tools=tools,
    )

    assert _result_key(seq) == _result_key(par)
    assert _state_snapshot(seq_cfg) == _state_snapshot(par_cfg)


@pytest.mark.asyncio
async def test_final_turn_message_equivalence():
    base = _base_live_config()
    tools = _make_agent_tools()
    seq_cfg = clone_turn_state(base)
    par_cfg = clone_turn_state(base)

    seq = await compute_turn_injections_sequential(
        transcript="bye",
        tc=40,
        max_t=40,
        live_config=seq_cfg,
        agent_tools=tools,
    )
    par = await compute_turn_injections_parallel(
        transcript="bye",
        tc=40,
        max_t=40,
        live_config=par_cfg,
        agent_tools=tools,
    )
    assert seq.messages[-1] == par.messages[-1]
    assert "final turn" in seq.messages[-1].lower()


@pytest.mark.asyncio
async def test_multi_turn_state_equivalence():
    """Replay a short conversation; mutable state must match after each turn."""
    base = _base_live_config()
    tools = _make_agent_tools()
    conversation = TRANSCRIPTS[:4]

    seq_cfg = clone_turn_state(base)
    par_cfg = clone_turn_state(base)

    for turn_idx, transcript in enumerate(conversation, start=1):
        seq = await compute_turn_injections_sequential(
            transcript=transcript,
            tc=turn_idx,
            max_t=40,
            live_config=seq_cfg,
            agent_tools=tools,
        )
        par = await compute_turn_injections_parallel(
            transcript=transcript,
            tc=turn_idx,
            max_t=40,
            live_config=par_cfg,
            agent_tools=tools,
        )
        assert _result_key(seq) == _result_key(par), f"turn {turn_idx}"
        assert _state_snapshot(seq_cfg) == _state_snapshot(par_cfg), f"state turn {turn_idx}"


def test_latency_stats_recording():
    record_turn_inject_latency(1.5, parallel=False)
    record_turn_inject_latency(0.8, parallel=True)
    stats = get_turn_inject_latency_stats()
    assert stats["all"]["count"] >= 2
    assert stats["sequential"]["count"] >= 1
    assert stats["parallel"]["count"] >= 1