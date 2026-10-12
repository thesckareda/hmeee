"""
Per-turn context injection — sequential and parallel execution paths.

Dependency map (verified against agent.py on_user_turn_completed):

  Wave 0 (sequential, hard gate — runs in agent.py before this module):
    1. speculative_reply — independent; injected in agent.py
    2. language_router — updates live_config; trap uses local turn_lang
    3. identity_shield — short-circuits steps 4–12 on trap (agent.py)

  Wave 1 (parallel — no cross-reads):
    4. knowledge_pack.resolve_turn_async(transcript)
    5. TopicMemory.extract_topics + build_no_repeat (mutates topic_mem only)

  Wave 2 (sequential — reads wave 1 outputs):
    6. ProactiveInsightEngine — needs turn_knowledge.faq AND topic_mem._discussed_topics

  Wave 3 (parallel process, sequential advance):
    7–8. tracker.process_utterance || planner.process_utterance (different objects)
         then planner.advance_for_deal_stage(tracker.stage)  [planner reads tracker.stage]
         then planner.get_instruction + tracker.get_strategy_instruction

  Wave 4 (sequential — step 10 gated on step 9 obj_type):
    9. detect_objection + hint (reads turn_knowledge.objection_hint from wave 1)
   10. get_failed_scripts — only if obj_type truthy; lookup uses phone only

  Wave 5 (independent, order fixed at assembly):
   11. emotion_detector
   12. turn-limit goodbye

Steps 7 and 8 share no live_config keys; planner.advance_for_deal_stage must run
after tracker.process_utterance completes (reads tracker.stage).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("inbound-voice-agent")

_LATENCY_MS: list[float] = []
_LATENCY_PARALLEL: list[bool] = []


@dataclass
class ObjectionSideEffect:
    log_type: str
    transcript_snippet: str
    script_used: str
    outcome: str = "resolved"


@dataclass
class TurnInjectionResult:
    """Ordered system messages for turn_ctx.add_message (steps 4–12)."""
    messages: list[str] = field(default_factory=list)
    emotion: str | None = None
    record_objection_handled: bool = False
    objection_side_effect: ObjectionSideEffect | None = None


def parallel_turn_injectors_enabled(live_config: dict | None) -> bool:
    return bool((live_config or {}).get("parallel_turn_injectors_enabled", False))


def record_turn_inject_latency(elapsed_ms: float, *, parallel: bool) -> None:
    _LATENCY_MS.append(elapsed_ms)
    _LATENCY_PARALLEL.append(parallel)
    if len(_LATENCY_MS) > 5000:
        del _LATENCY_MS[:2500]
        del _LATENCY_PARALLEL[:2500]


def get_turn_inject_latency_stats() -> dict[str, Any]:
    if not _LATENCY_MS:
        return {"count": 0}

    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = int(round((pct / 100.0) * (len(ordered) - 1)))
        return round(ordered[idx], 3)

    seq = [ms for ms, par in zip(_LATENCY_MS, _LATENCY_PARALLEL) if not par]
    par = [ms for ms, par in zip(_LATENCY_MS, _LATENCY_PARALLEL) if par]

    def _stats(vals: list[float]) -> dict[str, float]:
        if not vals:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
        return {
            "count": len(vals),
            "p50": _percentile(vals, 50),
            "p95": _percentile(vals, 95),
            "p99": _percentile(vals, 99),
        }

    return {
        "sequential": _stats(seq),
        "parallel": _stats(par),
        "all": _stats(_LATENCY_MS),
    }


def _proactive_message(turn_knowledge: Any, topic_mem: Any) -> str | None:
    from conversational_iq import ProactiveInsightEngine

    proactive_topic = None
    if turn_knowledge.faq and turn_knowledge.faq.confidence >= 0.70:
        proactive_topic = turn_knowledge.faq.category
    else:
        for topic in ("price", "location", "site_visit", "size", "possession"):
            if topic in topic_mem._discussed_topics:
                proactive_topic = topic
                break
    if not proactive_topic:
        return None
    followup = ProactiveInsightEngine.get_proactive_followup(
        proactive_topic,
        topics_covered=topic_mem._discussed_topics,
    )
    if not followup:
        return None
    return f"[PROACTIVE FOLLOW-UP] After answering, add: {followup}"


def _planner_tracker_messages(
    transcript: str,
    planner: Any,
    tracker: Any,
) -> list[str]:
    from sales_playbook import DealStage

    messages: list[str] = []
    prev_stage = tracker.stage
    tracker.process_utterance(transcript)
    if planner:
        planner.process_utterance(transcript)
        planner.advance_for_deal_stage(tracker.stage)
        messages.append(planner.get_instruction())
    if tracker.stage != prev_stage or tracker.stage in (
        DealStage.INTENT,
        DealStage.PURCHASE,
        DealStage.BOOKED,
    ):
        messages.append(tracker.get_strategy_instruction())
    return messages


async def _planner_tracker_messages_parallel(
    transcript: str,
    planner: Any,
    tracker: Any,
) -> list[str]:
    from sales_playbook import DealStage

    prev_stage = tracker.stage

    async def _run_tracker() -> None:
        await asyncio.to_thread(tracker.process_utterance, transcript)

    async def _run_planner() -> None:
        if planner:
            await asyncio.to_thread(planner.process_utterance, transcript)

    await asyncio.gather(_run_tracker(), _run_planner())

    messages: list[str] = []
    if planner:
        planner.advance_for_deal_stage(tracker.stage)
        messages.append(planner.get_instruction())
    if tracker.stage != prev_stage or tracker.stage in (
        DealStage.INTENT,
        DealStage.PURCHASE,
        DealStage.BOOKED,
    ):
        messages.append(tracker.get_strategy_instruction())
    return messages


def _objection_messages(
    transcript: str,
    turn_knowledge: Any,
    agent_tools: Any,
) -> tuple[list[str], bool, ObjectionSideEffect | None]:
    from sales_playbook import detect_objection, get_objection_response
    from re_voice_memory.objection_log import PLAYBOOK_SCRIPTS, get_failed_scripts

    messages: list[str] = []
    record_handled = False
    side_effect: ObjectionSideEffect | None = None

    obj_type, _obj_entry = detect_objection(transcript)
    if obj_type and not turn_knowledge.objection_hint:
        obj_hint = get_objection_response(obj_type)
        if obj_hint:
            messages.append(f"[OBJECTION RESPONSE HINT] {obj_hint[:320]}")
            record_handled = True
            _SALES_TO_LOG = {
                "too_expensive": "price",
                "location_far": "location",
                "not_ready": "timing",
                "need_to_discuss": "timing",
                "comparing_other": "other",
                "construction_delay": "timing",
                "resale_value": "other",
                "no_interest": "timing",
                "vastu_concern": "amenity",
                "loan_concern": "process",
            }
            log_type = _SALES_TO_LOG.get(obj_type, "other")
            script = PLAYBOOK_SCRIPTS.get(log_type, "generic")
            side_effect = ObjectionSideEffect(
                log_type=log_type,
                transcript_snippet=transcript[:200],
                script_used=script,
            )

    if obj_type:
        phone = getattr(agent_tools, "_re_lead_phone", "") or ""
        failed = get_failed_scripts(phone) if phone and phone != "unknown" else []
        if failed:
            messages.append(
                "[OBJECTION MEMORY] Prior calls: these scripts FAILED — "
                f"do NOT reuse: {', '.join(failed)}. Use a fresh angle."
            )

    return messages, record_handled, side_effect


def _emotion_for_transcript(transcript: str) -> str | None:
    from emotion_detector import detect_emotion

    return detect_emotion(transcript)


def _conversation_health_message(live_config: dict) -> str | None:
    """Wave: correct verbosity if last 2 agent turns were too long or had no question."""
    import re

    recent = live_config.get("_recent_agent_replies") or []
    if len(recent) < 2:
        return None

    problems: list[str] = []
    for text in recent[-2:]:
        stripped = (text or "").strip()
        if not stripped:
            continue
        sentences = [s for s in re.split(r"[.!?।]+", stripped) if s.strip()]
        if len(sentences) > 2:
            problems.append("last turns had more than 2 sentences")
        if "?" not in stripped:
            problems.append("last turns ended without a question")

    if not problems:
        return None

    return (
        "[CONVERSATION HEALTH] "
        + "; ".join(dict.fromkeys(problems))
        + ". Next reply: max 2 short sentences (≤14 words each), ONE question at the end. "
        "END every turn with a question or an invitation to respond — never on a bare fact."
    )


def _capture_name_and_relations(transcript: str, live_config: dict) -> None:
    """Wave 0.5 — live name/relation capture (IQ 0.3/#4). Regex-only, ~0ms."""
    try:
        from name_capture import capture_from_transcript

        capture_from_transcript(transcript, live_config)
    except Exception:
        pass


def _wave6_message(
    *,
    live_config: dict,
    planner: Any,
    transcript: str,
    emotion: str | None,
) -> str | None:
    """Wave 6 — conversational IQ assembler (IQ plan 0.5). Never raises."""
    try:
        from iq_assembler import assemble_wave6

        return assemble_wave6(
            live_config=live_config,
            planner=planner,
            transcript=transcript,
            emotion=emotion,
        )
    except Exception:
        return None


def _final_turn_message(tc: int, max_t: int) -> str | None:
    if tc >= max_t:
        return (
            "This is the final turn. Politely wrap up, thank the caller, "
            "and say a warm goodbye."
        )
    return None


async def compute_turn_injections_sequential(
    *,
    transcript: str,
    tc: int,
    max_t: int,
    live_config: dict,
    agent_tools: Any,
) -> TurnInjectionResult:
    from knowledge_pack import format_turn_injection, get_store
    from language_router import resolve_lang_preset
    from sales_playbook import DealStageTracker

    preset_key = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    _capture_name_and_relations(transcript, live_config)
    turn_knowledge = await get_store().resolve_turn_async(transcript, preset_key)

    messages: list[str] = []
    knowledge_injection = format_turn_injection(turn_knowledge, preset=preset_key)
    if knowledge_injection:
        messages.append(knowledge_injection)

    topic_mem = live_config.get("_topic_memory")
    if topic_mem:
        topic_mem.extract_topics_from_transcript("user", transcript)
        no_repeat = topic_mem.build_no_repeat_instruction()
        if no_repeat:
            messages.append(no_repeat)
        proactive = _proactive_message(turn_knowledge, topic_mem)
        if proactive:
            messages.append(proactive)

    planner = live_config.get("_call_planner")
    tracker: DealStageTracker = live_config.get("_deal_tracker")
    if tracker is None:
        tracker = DealStageTracker()
        live_config["_deal_tracker"] = tracker
    messages.extend(_planner_tracker_messages(transcript, planner, tracker))

    obj_msgs, record_handled, side_effect = _objection_messages(
        transcript, turn_knowledge, agent_tools
    )
    messages.extend(obj_msgs)

    health = _conversation_health_message(live_config)
    if health:
        messages.append(health)

    emotion = _emotion_for_transcript(transcript)

    iq_msg = _wave6_message(
        live_config=live_config, planner=planner, transcript=transcript, emotion=emotion
    )
    if iq_msg:
        messages.append(iq_msg)

    final_msg = _final_turn_message(tc, max_t)
    if final_msg:
        messages.append(final_msg)

    return TurnInjectionResult(
        messages=messages,
        emotion=emotion,
        record_objection_handled=record_handled,
        objection_side_effect=side_effect,
    )


async def compute_turn_injections_parallel(
    *,
    transcript: str,
    tc: int,
    max_t: int,
    live_config: dict,
    agent_tools: Any,
) -> TurnInjectionResult:
    from knowledge_pack import format_turn_injection, get_store
    from language_router import resolve_lang_preset
    from sales_playbook import DealStageTracker

    preset_key = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    _capture_name_and_relations(transcript, live_config)
    topic_mem = live_config.get("_topic_memory")

    async def _knowledge_task():
        return await get_store().resolve_turn_async(transcript, preset_key)

    def _topic_task() -> str | None:
        if not topic_mem:
            return None
        topic_mem.extract_topics_from_transcript("user", transcript)
        return topic_mem.build_no_repeat_instruction()

    turn_knowledge, no_repeat = await asyncio.gather(
        _knowledge_task(),
        asyncio.to_thread(_topic_task),
    )

    messages: list[str] = []
    knowledge_injection = format_turn_injection(turn_knowledge, preset=preset_key)
    if knowledge_injection:
        messages.append(knowledge_injection)
    if no_repeat:
        messages.append(no_repeat)
    if topic_mem:
        proactive = _proactive_message(turn_knowledge, topic_mem)
        if proactive:
            messages.append(proactive)

    planner = live_config.get("_call_planner")
    tracker: DealStageTracker = live_config.get("_deal_tracker")
    if tracker is None:
        tracker = DealStageTracker()
        live_config["_deal_tracker"] = tracker
    messages.extend(
        await _planner_tracker_messages_parallel(transcript, planner, tracker)
    )

    obj_msgs, record_handled, side_effect = _objection_messages(
        transcript, turn_knowledge, agent_tools
    )
    messages.extend(obj_msgs)

    health = _conversation_health_message(live_config)
    if health:
        messages.append(health)

    emotion = _emotion_for_transcript(transcript)

    iq_msg = _wave6_message(
        live_config=live_config, planner=planner, transcript=transcript, emotion=emotion
    )
    if iq_msg:
        messages.append(iq_msg)

    final_msg = _final_turn_message(tc, max_t)
    if final_msg:
        messages.append(final_msg)

    return TurnInjectionResult(
        messages=messages,
        emotion=emotion,
        record_objection_handled=record_handled,
        objection_side_effect=side_effect,
    )


async def compute_turn_injections(
    *,
    transcript: str,
    tc: int,
    max_t: int,
    live_config: dict,
    agent_tools: Any,
) -> TurnInjectionResult:
    parallel = parallel_turn_injectors_enabled(live_config)
    start = time.perf_counter()
    if parallel:
        result = await compute_turn_injections_parallel(
            transcript=transcript,
            tc=tc,
            max_t=max_t,
            live_config=live_config,
            agent_tools=agent_tools,
        )
    else:
        result = await compute_turn_injections_sequential(
            transcript=transcript,
            tc=tc,
            max_t=max_t,
            live_config=live_config,
            agent_tools=agent_tools,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000
    record_turn_inject_latency(elapsed_ms, parallel=parallel)
    logger.debug(
        "[TURN-INJECT] %s path %.2fms messages=%d",
        "parallel" if parallel else "sequential",
        elapsed_ms,
        len(result.messages),
    )
    return result


def apply_turn_injection_result(
    turn_ctx: Any,
    result: TurnInjectionResult,
    *,
    agent_tools: Any,
    quality_scorer: Any | None,
) -> None:
    for content in result.messages:
        try:
            turn_ctx.add_message(role="system", content=content)
        except Exception:
            pass

    if result.record_objection_handled and quality_scorer:
        quality_scorer.record_objection(handled=True)

    if result.objection_side_effect:
        try:
            se = result.objection_side_effect
            agent_tools.buffer_objection_handled(
                se.log_type,
                se.transcript_snippet,
                script_used=se.script_used,
                outcome=se.outcome,
            )
        except Exception:
            pass

    if result.emotion:
        try:
            from emotion_detector import inject_emotion_annotation

            inject_emotion_annotation(turn_ctx, result.emotion)
        except Exception:
            pass


def clone_turn_state(live_config: dict) -> dict:
    """Deep-copy mutable per-call state for equivalence tests."""
    from call_planner import CallPlanner, DiscoveryState
    from conversational_iq import TopicMemory
    from sales_playbook import DealStageTracker
    import copy

    cloned = dict(live_config)
    if live_config.get("_topic_memory"):
        tm = live_config["_topic_memory"]
        new_tm = TopicMemory()
        new_tm._discussed_topics = set(tm._discussed_topics)
        cloned["_topic_memory"] = new_tm
    if live_config.get("_call_planner"):
        cp = live_config["_call_planner"]
        new_cp = CallPlanner()
        new_cp._stage = cp._stage
        new_cp._slots = DiscoveryState(
            budget=cp._slots.budget.copy(),
            location=cp._slots.location.copy(),
            bhk=cp._slots.bhk.copy(),
            timeline=cp._slots.timeline.copy(),
            purpose=cp._slots.purpose.copy(),
        )
        new_cp._budget_mentioned = cp._budget_mentioned
        new_cp._area_mentioned = cp._area_mentioned
        new_cp._project_discussed = cp._project_discussed
        new_cp._visit_intent = cp._visit_intent
        new_cp._objection_active = getattr(cp, "_objection_active", False)
        new_cp._turn_count = getattr(cp, "_turn_count", 0)
        new_cp._iq_events = [dict(e) for e in getattr(cp, "_iq_events", [])]
        cloned["_call_planner"] = new_cp
    if live_config.get("_iq_state"):
        cloned["_iq_state"] = {
            k: dict(v) for k, v in live_config["_iq_state"].items()
        }
    if live_config.get("_relations"):
        cloned["_relations"] = [dict(r) for r in live_config["_relations"]]
    if live_config.get("_deal_tracker"):
        dt = live_config["_deal_tracker"]
        new_dt = DealStageTracker()
        new_dt._stage = dt._stage
        new_dt._signals = list(dt._signals)
        new_dt._stage_history = copy.deepcopy(dt._stage_history)
        cloned["_deal_tracker"] = new_dt
    return cloned