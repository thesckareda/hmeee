"""Phase 0 foundations — slot values, IQ events, name capture, registry, Wave 6 perf."""
from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from call_planner import CallPlanner, DiscoveryState, SlotValue, TurnStage
from iq_assembler import assemble_wave6
from iq_registry import all_primitives, clear_registry_cache
from name_capture import extract_caller_name, extract_relations


# ── 0.1 value-bearing slots ──────────────────────────────────────────────────

def test_slot_captures_value():
    state = DiscoveryState()
    events = state.fill_from_text("Na budget 1.2 Cr varaku undi andi", turn=2)
    assert state.budget.filled
    assert state.budget.value.lower() == "1.2 cr"
    assert [e["event"] for e in events] == ["slot_filled"]


def test_slot_boolean_match_without_value_still_fills():
    state = DiscoveryState()
    state.fill_from_text("emi gurinchi kuda cheppandi", turn=1)
    assert state.budget.filled
    assert state.budget.value == ""


def test_slot_delta_emitted_on_contradiction():
    state = DiscoveryState()
    state.fill_from_text("budget 1 crore", turn=1)
    events = state.fill_from_text("actually budget 1.5 crore max", turn=3)
    deltas = [e for e in events if e["event"] == "slot_delta"]
    assert len(deltas) == 1
    assert deltas[0]["old"].lower() == "1 crore"
    assert deltas[0]["new"].lower() == "1.5 crore"
    assert state.budget.history == [("1 crore", 1)]


def test_filled_missing_signatures_backward_compat():
    state = DiscoveryState()
    state.fill_from_text("3bhk kokapet lo kavali")
    assert "bhk" in state.filled()
    assert "location" in state.filled()
    assert "budget" in state.missing()


def test_legacy_bool_construction_coerced():
    state = DiscoveryState(budget=True, location=False)
    assert isinstance(state.budget, SlotValue)
    assert state.budget.filled
    assert not state.location.filled


# ── 0.2 IQ event queue ───────────────────────────────────────────────────────

def test_stage_change_event_emitted():
    planner = CallPlanner()
    planner.process_utterance("hello, looking for a flat")
    events = planner.pop_iq_events()
    assert any(e["event"] == "stage_change" for e in events)
    assert planner.pop_iq_events() == []  # drained


def test_objection_and_close_events():
    planner = CallPlanner()
    planner.process_utterance("hi")
    planner.pop_iq_events()
    planner.process_utterance("idi too expensive andi")
    assert any(e["event"] == "objection" for e in planner.pop_iq_events())
    planner.process_utterance("sare, site visit book cheddam")
    assert any(e["event"] == "close_intent" for e in planner.pop_iq_events())


def test_repeat_complaint_event():
    planner = CallPlanner()
    planner.process_utterance("nenu already told you my budget!")
    assert any(e["event"] == "repeat_complaint" for e in planner.pop_iq_events())


# ── 0.3 name capture ─────────────────────────────────────────────────────────

def test_name_capture_explicit_intro():
    assert extract_caller_name("na peru Ravi andi") == "Ravi"
    assert extract_caller_name("mera naam suresh hai") == "Suresh"
    assert extract_caller_name("myself Priya") == "Priya"


def test_name_capture_never_guesses():
    assert extract_caller_name("I'm looking for a 3BHK") is None
    assert extract_caller_name("nenu ready andi") is None
    assert extract_caller_name("what is the price?") is None


def test_relation_capture():
    rels = extract_relations("na wife Priya schools gurinchi adigindi")
    assert rels == [{"name": "Priya", "relation": "wife"}]


# ── 0.4 registry integrity ───────────────────────────────────────────────────

def test_registry_all_18_present_and_covered():
    clear_registry_cache()
    prims = all_primitives()
    assert len(prims) == 18
    for pid, prim in prims.items():
        for pack in ("tinglish", "hte", "english"):
            assert prim.rules.get(pack), f"{pid} missing {pack} rule"


# ── 0.5 Wave 6 ≤5ms with 10 queued events ────────────────────────────────────

def test_wave6_latency_budget():
    planner = CallPlanner()
    live = {"conversational_iq_v2_enabled": True, "_caller_name": "Ravi"}

    # Warm-up: registry + module imports load at worker prewarm in production
    # (worker_prewarm.py), so the budget applies to steady-state turns.
    planner._iq_events.append({"event": "stage_change", "from": "open", "to": "discover"})
    assemble_wave6(live_config=dict(live), planner=planner, transcript="warmup", emotion=None)

    for _ in range(10):
        planner._iq_events.append({"event": "stage_change", "from": "open", "to": "discover"})

    t0 = time.perf_counter()
    msg = assemble_wave6(
        live_config=live, planner=planner, transcript="3bhk kavali", emotion=None
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert msg is not None
    assert elapsed_ms < 5.0, f"Wave 6 took {elapsed_ms:.2f}ms (budget 5ms)"


def test_wave6_dark_when_flag_off():
    planner = CallPlanner()
    planner._iq_events.append({"event": "stage_change", "from": "open", "to": "discover"})
    msg = assemble_wave6(
        live_config={"_caller_name": "Ravi"}, planner=planner, transcript="hi", emotion=None
    )
    assert msg is None
    assert planner._iq_events == []  # queue drained even when dark
