"""Phase 1–4 primitives — fire/no-fire, cooldowns, conflicts, rule cap, prosody, scarcity."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from call_planner import CallPlanner
from iq_assembler import assemble_wave6


def _live(**extra) -> dict:
    cfg = {"conversational_iq_v2_enabled": True, "lang_preset": "tinglish"}
    cfg.update(extra)
    return cfg


def _planner_with(events: list[dict], turn: int = 5) -> CallPlanner:
    planner = CallPlanner()
    planner._turn_count = turn
    planner._iq_events = list(events)
    return planner


# ── #1 name-anchored transitions ─────────────────────────────────────────────

def test_iq01_fires_on_stage_change_with_name():
    planner = _planner_with([{"event": "stage_change", "from": "open", "to": "discover"}])
    msg = assemble_wave6(live_config=_live(_caller_name="Ravi"), planner=planner,
                         transcript="3bhk kavali", emotion=None)
    assert msg and "Ravi" in msg and "open" in msg and "discover" in msg


def test_iq01_no_fire_without_name():
    planner = _planner_with([{"event": "stage_change", "from": "open", "to": "discover"}])
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="3bhk kavali", emotion=None)
    assert msg is None or "phase shift" not in msg


# ── #2 name + micro-commitment ───────────────────────────────────────────────

def test_iq02_fires_on_slot_filled_in_discover():
    planner = _planner_with(
        [{"event": "slot_filled", "slot": "budget", "value": "1.2 Cr", "stage": "discover"}]
    )
    msg = assemble_wave6(live_config=_live(_caller_name="Ravi"), planner=planner,
                         transcript="budget 1.2 cr", emotion=None)
    assert msg and "1.2 Cr" in msg and "Ravi" in msg


# ── #7 same-boat (emotion + stress keywords) ─────────────────────────────────

def test_iq07_fires_on_stressed_emotion_with_keywords():
    planner = _planner_with([])
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="rent and EMI both are killing me", emotion="frustrated")
    assert msg and "empathy" in msg.lower()


def test_iq07_no_fire_without_stress_keywords():
    planner = _planner_with([])
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="the weather is bad", emotion="frustrated")
    assert msg is None or "empathy" not in (msg or "").lower()


# ── #9 de-escalation preempts everything ─────────────────────────────────────

def test_iq09_preempts_all_other_rules():
    planner = _planner_with([
        {"event": "repeat_complaint"},
        {"event": "stage_change", "from": "open", "to": "discover"},
        {"event": "slot_filled", "slot": "budget", "value": "1 Cr", "stage": "discover"},
    ])
    msg = assemble_wave6(live_config=_live(_caller_name="Ravi"), planner=planner,
                         transcript="already told you!", emotion="frustrated")
    assert msg is not None
    assert "DROP the agenda" in msg
    assert msg.count("\n") <= 1  # only the de-escalation rule fired


# ── #15 breadcrumbs need two VALUES ──────────────────────────────────────────

def test_iq15_fires_with_two_slot_values():
    planner = CallPlanner()
    planner._turn_count = 4
    planner.slots.fill_from_text("budget 1.2 cr", turn=2)
    planner.slots.fill_from_text("kokapet area", turn=3)
    planner._iq_events = [{"event": "stage_change", "from": "discover", "to": "match"}]
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="sare", emotion=None)
    assert msg and "1.2 cr" in msg.lower() and "kokapet" in msg.lower()


def test_iq15_no_fire_with_valueless_slots():
    planner = CallPlanner()
    planner._turn_count = 4
    planner.slots.fill_from_text("area gurinchi budget gurinchi discuss cheddam", turn=2)
    planner._iq_events = [{"event": "stage_change", "from": "discover", "to": "match"}]
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="sare", emotion=None)
    assert msg is None or "clear ayindi" not in msg


# ── #16 self-correction on slot delta ────────────────────────────────────────

def test_iq16_fires_on_slot_delta_with_dependent():
    planner = _planner_with(
        [{"event": "slot_delta", "slot": "bhk", "old": "2 BHK", "new": "3 BHK", "stage": "discover"}]
    )
    msg = assemble_wave6(live_config=_live(), planner=planner,
                         transcript="actually 3bhk", emotion=None)
    assert msg and "2 BHK" in msg and "3 BHK" in msg and "budget" in msg


# ── #22 preempts #21; fires max once per call ────────────────────────────────

def test_iq22_fires_once_and_preempts_story():
    live = _live()
    planner = _planner_with([{"event": "objection", "type": "expensive"}])
    msg1 = assemble_wave6(live_config=live, planner=planner,
                          transcript="too expensive andi", emotion=None)
    assert msg1 and "ONE question" in msg1
    assert "story" not in msg1.lower()

    planner2 = _planner_with([{"event": "objection", "type": "expensive"}], turn=9)
    live2 = dict(live)
    msg2 = assemble_wave6(live_config=live2, planner=planner2,
                          transcript="still too expensive", emotion=None)
    # second objection: #22 exhausted (max 1/call) → #21 story may fire instead
    assert msg2 is None or "ONE question about" not in msg2


# ── #21 story is grounded from the knowledge pack ────────────────────────────

def test_iq21_story_fires_after_bridge_exhausted():
    live = _live()
    live["_iq_state"] = {"iq_22_before_answer_bridge": {"fires": 1, "last_turn": 2}}
    planner = _planner_with([{"event": "objection", "type": "expensive"}], turn=8)
    msg = assemble_wave6(live_config=live, planner=planner,
                         transcript="idi chala expensive andi", emotion=None)
    assert msg and "genuine concern" in msg
    assert "58.5%" in msg  # real pack fact, not an invented number


# ── ≤2 rules per turn + cooldowns ────────────────────────────────────────────

def test_rule_cap_two_per_turn():
    planner = _planner_with([
        {"event": "stage_change", "from": "open", "to": "discover"},
        {"event": "slot_filled", "slot": "budget", "value": "1 Cr", "stage": "discover"},
        {"event": "objection", "type": "expensive"},
        {"event": "close_intent"},
    ])
    msg = assemble_wave6(live_config=_live(_caller_name="Ravi"), planner=planner,
                         transcript="visit fix cheddam kani expensive", emotion=None)
    assert msg is not None
    rules = [l for l in msg.split("\n") if l and not l.startswith("[")]
    assert len(rules) <= 2


def test_cooldown_respected():
    live = _live(_caller_name="Ravi")
    planner = _planner_with([{"event": "stage_change", "from": "open", "to": "discover"}], turn=3)
    msg1 = assemble_wave6(live_config=live, planner=planner, transcript="hi", emotion=None)
    assert msg1 and "Ravi" in msg1

    planner2 = _planner_with([{"event": "stage_change", "from": "discover", "to": "match"}], turn=4)
    live["_iq_state"]["iq_15_topic_breadcrumb_trail"] = {"fires": 1, "last_turn": 4}
    msg2 = assemble_wave6(live_config=live, planner=planner2, transcript="ok", emotion=None)
    # iq_01 fired turn 3 with cooldown 2 → blocked at turn 4
    assert msg2 is None or "phase shift" not in msg2


def test_kill_switch_per_primitive():
    planner = _planner_with([{"event": "stage_change", "from": "open", "to": "discover"}])
    msg = assemble_wave6(
        live_config=_live(_caller_name="Ravi",
                          iq_disabled_primitives={"iq_01_name_anchored_transition"}),
        planner=planner, transcript="hi", emotion=None,
    )
    assert msg is None or "phase shift" not in msg


# ── #24 grounded scarcity ────────────────────────────────────────────────────

def test_urgency_silent_without_fact():
    from sales_playbook import get_urgency_phrase
    assert get_urgency_phrase() == ""
    assert get_urgency_phrase({"available_units": "3"}) == ""


def test_urgency_speaks_real_fact_only():
    from sales_playbook import get_urgency_phrase
    phrase = get_urgency_phrase(scarcity_fact={"name": "Krishe", "units_left": 4})
    assert "4" in phrase and "actual availability" in phrase


# ── #26 prosody transform ────────────────────────────────────────────────────

def test_confirmation_tag_flattened():
    from tts_text_transform import flatten_confirmation_tag
    assert flatten_confirmation_tag("Idhi clear kada?") == "Idhi clear, kada."
    assert flatten_confirmation_tag("Site visit fix, sare na?") == "Site visit fix, sare na."


def test_genuine_questions_keep_rise():
    from tts_text_transform import flatten_confirmation_tag
    assert flatten_confirmation_tag("Budget entha andi?") == "Budget entha andi?"
    assert flatten_confirmation_tag("Saturday na, Sunday na?") == "Saturday na, Sunday na?"


# ── #5 name-based silence rescue ─────────────────────────────────────────────

def test_silence_bridge_uses_name_when_known():
    from silence_guard import SilenceGuard
    guard = SilenceGuard(live_config={"_caller_name": "Ravi", "lang_preset": "tinglish"})
    assert "Ravi" in guard._bridge_phrase()


def test_silence_bridge_generic_without_name():
    from silence_guard import SilenceGuard
    guard = SilenceGuard(live_config={})
    assert "{name}" not in guard._bridge_phrase()


# ── #17 temporal grounding ───────────────────────────────────────────────────

def test_history_renders_human_delta():
    from datetime import datetime, timedelta, timezone
    from re_voice_memory.caller_history import CallerHistoryEntry, format_history_for_injection

    two_days = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    entry = CallerHistoryEntry(
        caller_name="Ravi", phone="+919000000001",
        summary="Discussed 3BHK in Kokapet, budget 1.2 Cr", call_at=two_days,
    )
    text = format_history_for_injection("+919000000001", "Ravi", [entry])
    assert "2 days back" in text
    assert "anchor it in TIME" in text
