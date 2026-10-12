"""
Wave 6 — Conversational IQ assembler (IQ plan 0.5).

Drains planner IQ events + synthesizes emotion/turn events, matches them
against the frozen registry, and emits at most TWO priority-ordered rules
as a single system message. Pure dict lookups + str.format_map — budget ≤5ms.

Conflict policy (IQ plan Phase 1): #9 de-escalation preempts everything;
#22 preempts #21; stage-change primitives can't collide with discovery rules.

Master flag: live_config["conversational_iq_v2_enabled"].
Per-primitive kill switch: live_config["iq_disabled_primitives"] (set/list of ids).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

RULE_CAP_PER_TURN = 2

# IQ #7 trigger: stressed emotions + money/market pressure keywords
_STRESS_EMOTIONS = frozenset({"frustrated", "hesitant", "skeptical"})
_STRESS_KEYWORDS = re.compile(r"\b(rent|emi|loan|market|interest|salary|savings|afford)\b", re.I)

# IQ #16 dependency map — a changed slot implies its dependent may shift
_SLOT_DEPENDENTS = {
    "bhk": "budget",
    "location": "budget",
    "timeline": "purpose",
    "budget": "location",
    "purpose": "timeline",
}

_WAVE6_MS: list[float] = []


def record_wave6_latency(elapsed_ms: float) -> None:
    _WAVE6_MS.append(elapsed_ms)
    if len(_WAVE6_MS) > 5000:
        del _WAVE6_MS[:2500]


def get_wave6_latency_stats() -> dict[str, float | int]:
    if not _WAVE6_MS:
        return {"count": 0}
    ordered = sorted(_WAVE6_MS)

    def _pct(p: float) -> float:
        idx = int(round((p / 100.0) * (len(ordered) - 1)))
        return round(ordered[idx], 3)

    return {"count": len(ordered), "p50": _pct(50), "p95": _pct(95), "p99": _pct(99)}


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def _resolve_pack(live_config: dict) -> str:
    try:
        from knowledge_pack import resolve_preset_pack
        from language_router import resolve_lang_preset

        return resolve_preset_pack(resolve_lang_preset(live_config.get("lang_preset"), live_config))
    except Exception:
        return "tinglish"


def _collect_events(planner: Any, transcript: str, emotion: str | None) -> list[dict]:
    events: list[dict] = []
    if planner is not None:
        try:
            events.extend(planner.pop_iq_events())
        except Exception:
            pass

    # Synthetic: IQ #7 same-boat trigger (Wave 5 emotion + stress keywords)
    if emotion in _STRESS_EMOTIONS and _STRESS_KEYWORDS.search(transcript or ""):
        events.append({"event": "emotion_stress", "emotion": emotion})

    # Synthetic: per-turn event (IQ #26 prosody notation during DISCOVER/CLOSE)
    stage = ""
    try:
        stage = planner.phase.value if planner is not None else ""
    except Exception:
        stage = ""
    events.append({"event": "turn", "stage": stage})
    return events


def _base_context(live_config: dict, planner: Any) -> dict:
    ctx: dict = {
        "name": str(live_config.get("_caller_name") or ""),
        "relations_line": str(live_config.get("_relations_line") or ""),
        "next_missing_slot": "",
        "stage": "",
    }
    if planner is not None:
        try:
            ctx["next_missing_slot"] = planner.next_missing_slot()
            ctx["stage"] = planner.phase.value
        except Exception:
            pass
    relations = live_config.get("_relations") or []
    if relations:
        ctx["relation_name"] = str(relations[-1].get("name", ""))
        ctx["relation"] = str(relations[-1].get("relation", ""))
    return ctx


def _event_context(event: dict, base: dict, planner: Any, live_config: dict, transcript: str) -> dict:
    ctx = dict(base)
    ctx.update({k: str(v) for k, v in event.items() if k != "event"})

    if event["event"] == "slot_delta":
        ctx["dependent"] = _SLOT_DEPENDENTS.get(str(event.get("slot", "")), "timeline")

    if event["event"] == "repeat_complaint" and not ctx.get("next_missing_slot"):
        ctx["next_missing_slot"] = "the caller's actual question"

    if event["event"] == "stage_change" and planner is not None:
        try:
            pairs = planner.slots.filled_with_values()
            if len(pairs) >= 2:
                (ctx["slot1"], ctx["value1"]) = pairs[-2]
                (ctx["slot2"], ctx["value2"]) = pairs[-1]
        except Exception:
            pass

    if event["event"] == "objection":
        # IQ #21 — grounded social-proof story from the knowledge pack
        try:
            from knowledge_pack import get_store
            from language_router import resolve_lang_preset

            preset = resolve_lang_preset(live_config.get("lang_preset"), live_config)
            story = get_store().objection_story(transcript or "", preset)
            if story:
                ctx["story"] = story
        except Exception:
            pass
    return ctx


def _eligible(prim: Any, event: dict, ctx: dict, live_config: dict, state: dict, turn: int) -> bool:
    from iq_registry import PROMPT_CLASS

    if prim.latency_class != PROMPT_CLASS:
        return False
    disabled = live_config.get("iq_disabled_primitives") or ()
    if prim.pid in disabled:
        return False
    for key in prim.requires:
        if not live_config.get(key):
            return False
    for key in prim.requires_ctx:
        if not ctx.get(key):
            return False
    if prim.requires_missing_slot and not ctx.get("next_missing_slot"):
        return False
    if prim.min_filled_values and not (ctx.get("slot2") and ctx.get("value2")):
        return False
    if prim.stages and ctx.get("stage", event.get("stage", "")) not in prim.stages:
        return False
    if prim.to_stages and event.get("to", "") not in prim.to_stages:
        return False

    pstate = state.get(prim.pid) or {}
    if prim.max_fires_per_call and pstate.get("fires", 0) >= prim.max_fires_per_call:
        return False
    if prim.cooldown_turns and turn - pstate.get("last_turn", -999) <= prim.cooldown_turns:
        return False
    return True


def assemble_wave6(
    *,
    live_config: dict,
    planner: Any,
    transcript: str,
    emotion: str | None,
) -> str | None:
    """Returns the single [CONVERSATIONAL IQ] system message, or None."""
    t0 = time.perf_counter()
    try:
        if not live_config.get("conversational_iq_v2_enabled"):
            # Still drain events so the queue never grows unbounded when dark.
            if planner is not None:
                try:
                    planner.pop_iq_events()
                except Exception:
                    pass
            return None

        from iq_registry import find_by_event

        turn = 0
        try:
            turn = planner.turn_count if planner is not None else 0
        except Exception:
            pass

        state: dict = live_config.setdefault("_iq_state", {})
        pack = _resolve_pack(live_config)
        base = _base_context(live_config, planner)

        events = _collect_events(planner, transcript, emotion)
        candidates: list[tuple[int, str, str, Any]] = []
        preempt_all = False

        for event in events:
            ctx = _event_context(event, base, planner, live_config, transcript)
            for prim in find_by_event(event["event"]):
                if not _eligible(prim, event, ctx, live_config, state, turn):
                    continue
                template = prim.rule_for(pack)
                if not template:
                    continue
                try:
                    text = template.format_map(_SafeFormat(ctx))
                except (KeyError, IndexError):
                    continue  # missing context → degrade gracefully, skip
                candidates.append((prim.priority, prim.pid, text, prim))
                if prim.preempts_all:
                    preempt_all = True

        if not candidates:
            return None

        candidates.sort(key=lambda c: (c[0], c[1]))
        if preempt_all:
            candidates = [c for c in candidates if c[3].preempts_all][:1]

        # Pairwise preemption (#22 over #21), dedupe by pid, cap at 2
        preempted: set[str] = set()
        for _, _, _, prim in candidates:
            preempted.update(prim.preempts)
        chosen: list[tuple[int, str, str, Any]] = []
        seen: set[str] = set()
        for cand in candidates:
            if cand[1] in preempted or cand[1] in seen:
                continue
            seen.add(cand[1])
            chosen.append(cand)
            if len(chosen) >= RULE_CAP_PER_TURN:
                break

        if not chosen:
            return None

        for _, pid, _, _ in chosen:
            pstate = state.setdefault(pid, {})
            pstate["last_turn"] = turn
            pstate["fires"] = pstate.get("fires", 0) + 1

        return "[CONVERSATIONAL IQ]\n" + "\n".join(text for _, _, text, _ in chosen)
    except Exception as exc:
        logger.debug("[IQ-WAVE6] Assembler skipped: %s", exc)
        return None
    finally:
        record_wave6_latency((time.perf_counter() - t0) * 1000)
