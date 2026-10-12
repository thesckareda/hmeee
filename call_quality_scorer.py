"""
Call Quality Scorer — Real-time and post-call quality assessment.

Scores every call across 4 dimensions:
  1. Human-Likeness Score (0-100): Response variation, fillers, turn-taking
  2. Sales Effectiveness Score (0-100): Questions asked, info gathered, objections handled
  3. Conversation Flow Score (0-100): Topic coherence, context retention
  4. AI Detection Risk Score (0-100): How likely caller suspects AI

Also tracks:
  - Dead air incidents (silence > 500ms)
  - Response time distribution
  - Turn length statistics
"""
from __future__ import annotations

import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

PSTN_ESTIMATE_MS = float(os.environ.get("PSTN_LATENCY_ESTIMATE_MS", "200"))


# ══════════════════════════════════════════════════════════════════════════════
# SCORING MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class CallQualityMetrics:
    """Raw metrics collected during a call."""

    # Response timing
    response_times_ms: list[float] = field(default_factory=list)  # LLM TTFT per turn
    tts_latencies_ms: list[float] = field(default_factory=list)   # TTS synthesis time
    interaction_latencies: list[dict[str, float | None]] = field(default_factory=list)
    _pending_llm_ms: float | None = field(default=None, repr=False)
    _pending_llm_ts: float | None = field(default=None, repr=False)
    _pending_ttfa_start_ts: float | None = field(default=None, repr=False)
    _pending_stt_final_ts: float | None = field(default=None, repr=False)
    _pending_turn_completed_ts: float | None = field(default=None, repr=False)

    # Silence tracking
    silence_incidents: list[float] = field(default_factory=list)  # Duration of each gap > 500ms
    max_silence_ms: float = 0.0

    # Turn analysis
    agent_turn_lengths: list[int] = field(default_factory=list)   # Word count per agent turn
    user_turn_lengths: list[int] = field(default_factory=list)    # Word count per user turn
    total_turns: int = 0
    interruptions: int = 0

    # Content analysis
    questions_asked_by_agent: int = 0  # Questions the agent asked
    questions_asked_by_user: int = 0   # Questions from the caller
    objections_detected: int = 0
    objections_handled: int = 0
    filler_words_used: int = 0
    disfluencies_used: int = 0

    # Identity safety
    ai_disclosure_attempts: int = 0      # Times LLM tried to reveal AI
    ai_disclosures_caught: int = 0       # Caught by identity shield
    ai_disclosures_leaked: int = 0       # Slipped through (CRITICAL)
    identity_traps_detected: int = 0     # Caller tested if AI
    identity_traps_handled: int = 0      # Successfully deflected

    # Sales metrics
    deal_stage_reached: str = "awareness"
    micro_commitments: int = 0
    site_visit_proposed: bool = False
    site_visit_booked: bool = False
    brochure_sent: bool = False
    urgency_used: bool = False

    # Outcome
    call_duration_sec: int = 0
    call_ended_by: str = "caller"
    booking_made: bool = False


@dataclass
class CallQualityScore:
    """Final quality scores for a call."""
    human_likeness: float = 0.0      # 0-100
    sales_effectiveness: float = 0.0  # 0-100
    conversation_flow: float = 0.0    # 0-100
    ai_detection_risk: float = 0.0    # 0-100 (lower is better)
    overall: float = 0.0              # Weighted average

    # Coaching insights
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# SCORING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class CallQualityScorer:
    """
    Score a call across all quality dimensions.
    """

    def __init__(self):
        self.metrics = CallQualityMetrics()

    # ── Real-time event recording ─────────────────────────────────────────

    def record_user_speech_finished(self) -> None:
        """Caller stopped speaking (VAD/STT endpoint) — start voice-to-voice TTFA clock."""
        self.metrics._pending_ttfa_start_ts = time.perf_counter()

    def has_ttfa_clock(self) -> bool:
        """Whether a TTFA start timestamp is pending for the current turn."""
        return self.metrics._pending_ttfa_start_ts is not None

    def record_user_final_transcript(self) -> None:
        """STT committed transcript (is_final)."""
        self.metrics._pending_stt_final_ts = time.perf_counter()

    def record_turn_processing_start(self) -> None:
        """LiveKit finished endpointing and handed turn to the LLM."""
        import time

        self.metrics._pending_turn_completed_ts = time.perf_counter()

    def record_response_time(self, ms: float) -> None:
        self.metrics.response_times_ms.append(ms)
        self.metrics._pending_llm_ms = ms
        self.metrics._pending_llm_ts = time.perf_counter()

    def record_tts_latency(self, ms: float) -> float | None:
        """Record TTS first-frame latency; returns ttfa_server_ms for Prometheus E2E."""
        now = time.perf_counter()
        ttfa_start = self.metrics._pending_ttfa_start_ts
        stt_final = self.metrics._pending_stt_final_ts
        turn_complete = self.metrics._pending_turn_completed_ts
        llm_ts = self.metrics._pending_llm_ts
        llm_ms = self.metrics._pending_llm_ms

        endpointing_ms: float | None = None
        if ttfa_start is not None and stt_final is not None:
            endpointing_ms = (stt_final - ttfa_start) * 1000

        stt_ms: float | None = None
        if stt_final is not None and turn_complete is not None:
            stt_ms = (turn_complete - stt_final) * 1000

        if turn_complete is not None and llm_ts is not None:
            llm_ms = (llm_ts - turn_complete) * 1000
        elif llm_ms is None:
            llm_ms = self.metrics._pending_llm_ms

        tts_node_start = now - (ms / 1000.0)
        buffer_ms: float | None = None
        if llm_ts is not None:
            raw_buffer_ms = (tts_node_start - llm_ts) * 1000
            if raw_buffer_ms >= 0:
                buffer_ms = raw_buffer_ms

        ttfa_server_ms: float | None = None
        if ttfa_start is not None:
            ttfa_server_ms = (now - ttfa_start) * 1000

        ttfa_estimated_ms: float | None = None
        if ttfa_server_ms is not None:
            ttfa_estimated_ms = ttfa_server_ms + PSTN_ESTIMATE_MS

        # Backward compat: e2e_ms aliases ttfa_server_ms when TTFA clock exists.
        e2e_ms: float | None = ttfa_server_ms
        if e2e_ms is None and stt_final is not None:
            e2e_ms = (now - stt_final) * 1000

        self.metrics.tts_latencies_ms.append(ms)
        self.metrics.interaction_latencies.append(
            {
                "endpointing_ms": round(endpointing_ms, 1) if endpointing_ms is not None else None,
                "stt_ms": round(stt_ms, 1) if stt_ms is not None else None,
                "llm_ms": round(llm_ms, 1) if llm_ms is not None else None,
                "buffer_ms": round(buffer_ms, 1) if buffer_ms is not None else None,
                "tts_ms": round(ms, 1),
                "ttfa_server_ms": round(ttfa_server_ms, 1) if ttfa_server_ms is not None else None,
                "ttfa_estimated_ms": (
                    round(ttfa_estimated_ms, 1) if ttfa_estimated_ms is not None else None
                ),
                "e2e_ms": round(e2e_ms, 1) if e2e_ms is not None else None,
            }
        )
        self.metrics._pending_llm_ms = None
        self.metrics._pending_llm_ts = None
        self.metrics._pending_ttfa_start_ts = None
        self.metrics._pending_stt_final_ts = None
        self.metrics._pending_turn_completed_ts = None
        return round(ttfa_server_ms, 1) if ttfa_server_ms is not None else None

    def record_silence(self, duration_ms: float) -> None:
        if duration_ms > 500:
            self.metrics.silence_incidents.append(duration_ms)
            self.metrics.max_silence_ms = max(self.metrics.max_silence_ms, duration_ms)

    def record_agent_turn(self, text: str) -> None:
        word_count = len((text or "").split())
        self.metrics.agent_turn_lengths.append(word_count)
        self.metrics.total_turns += 1

        # Check for questions asked
        if "?" in text:
            self.metrics.questions_asked_by_agent += 1

    def record_user_turn(self, text: str) -> None:
        word_count = len((text or "").split())
        self.metrics.user_turn_lengths.append(word_count)

        if "?" in text:
            self.metrics.questions_asked_by_user += 1

    def record_interruption(self) -> None:
        self.metrics.interruptions += 1

    def record_objection(self, handled: bool = False) -> None:
        self.metrics.objections_detected += 1
        if handled:
            self.metrics.objections_handled += 1

    def record_ai_disclosure_attempt(self, caught: bool = True) -> None:
        self.metrics.ai_disclosure_attempts += 1
        if caught:
            self.metrics.ai_disclosures_caught += 1
        else:
            self.metrics.ai_disclosures_leaked += 1

    def record_identity_trap(self, handled: bool = True) -> None:
        self.metrics.identity_traps_detected += 1
        if handled:
            self.metrics.identity_traps_handled += 1

    def record_deal_stage(self, stage: str) -> None:
        self.metrics.deal_stage_reached = stage

    def record_booking(self) -> None:
        self.metrics.booking_made = True
        self.metrics.site_visit_booked = True

    # ── Final scoring ─────────────────────────────────────────────────────

    def calculate_scores(self) -> CallQualityScore:
        """Calculate final quality scores."""
        score = CallQualityScore()

        score.human_likeness = self._score_human_likeness()
        score.sales_effectiveness = self._score_sales_effectiveness()
        score.conversation_flow = self._score_conversation_flow()
        score.ai_detection_risk = self._score_ai_detection_risk()

        # Weighted overall (human-likeness is most important)
        score.overall = (
            score.human_likeness * 0.35 +
            score.sales_effectiveness * 0.25 +
            score.conversation_flow * 0.25 +
            (100 - score.ai_detection_risk) * 0.15
        )

        score.strengths = self._identify_strengths()
        score.improvements = self._identify_improvements()
        score.warnings = self._identify_warnings()

        logger.info(
            "[QUALITY] Scores: human=%.0f sales=%.0f flow=%.0f ai_risk=%.0f overall=%.0f",
            score.human_likeness, score.sales_effectiveness,
            score.conversation_flow, score.ai_detection_risk, score.overall,
        )

        return score

    def _score_human_likeness(self) -> float:
        """Score how human-like the conversation sounded."""
        score = 100.0
        m = self.metrics

        # Response time variation (humans vary, bots are consistent)
        if len(m.response_times_ms) >= 3:
            cv = _coefficient_of_variation(m.response_times_ms)
            if cv < 0.1:  # Too consistent → robotic
                score -= 15
            elif cv > 0.3:  # Good variation
                score += 5

        # Turn length variation (humans vary response length)
        if len(m.agent_turn_lengths) >= 3:
            cv = _coefficient_of_variation(m.agent_turn_lengths)
            if cv < 0.15:  # Same length every time → robotic
                score -= 15
            elif cv > 0.4:  # Good variation
                score += 5

        # Silence incidents (humans don't have dead air)
        if m.silence_incidents:
            score -= min(30, len(m.silence_incidents) * 5)

        # Long silences are worse
        if m.max_silence_ms > 3000:
            score -= 20
        elif m.max_silence_ms > 1500:
            score -= 10

        # Average response length (too long = robotic monologue)
        if m.agent_turn_lengths:
            avg_words = statistics.mean(m.agent_turn_lengths)
            if avg_words > 50:
                score -= 20  # Too verbose
            elif avg_words > 35:
                score -= 10
            elif avg_words < 5:
                score -= 10  # Too terse

        # Filler words (humans use them naturally)
        if m.filler_words_used > 0:
            score += min(5, m.filler_words_used)

        return max(0, min(100, score))

    def _score_sales_effectiveness(self) -> float:
        """Score how effective the sales approach was."""
        score = 50.0  # Start at baseline
        m = self.metrics

        # Questions asked by agent (sales reps ask questions to control)
        if m.questions_asked_by_agent >= 5:
            score += 15
        elif m.questions_asked_by_agent >= 3:
            score += 10
        elif m.questions_asked_by_agent == 0:
            score -= 15

        # Objection handling
        if m.objections_detected > 0:
            handle_rate = m.objections_handled / m.objections_detected
            score += 15 * handle_rate

        # Deal progression
        stage_scores = {
            "awareness": 0, "interest": 10, "consideration": 20,
            "intent": 30, "purchase": 40, "booked": 50,
        }
        score += stage_scores.get(m.deal_stage_reached, 0)

        # Booking = maximum effectiveness
        if m.booking_made:
            score = max(score, 90)

        # Site visit proposed even if not booked
        if m.site_visit_proposed and not m.site_visit_booked:
            score += 10

        # Brochure sent (good follow-through)
        if m.brochure_sent:
            score += 5

        return max(0, min(100, score))

    def _score_conversation_flow(self) -> float:
        """Score the natural flow of the conversation."""
        score = 80.0  # Start optimistic
        m = self.metrics

        # Too many interruptions = poor turn-taking
        if m.interruptions > 5:
            score -= 15
        elif m.interruptions > 3:
            score -= 8

        # Turn count (healthy conversation = 8-25 turns)
        if m.total_turns < 4:
            score -= 20  # Too short
        elif m.total_turns > 30:
            score -= 10  # Too long without resolution

        # Average response time (under 1s is great, over 2s is bad)
        if m.response_times_ms:
            avg_rt = statistics.mean(m.response_times_ms)
            if avg_rt < 500:
                score += 10
            elif avg_rt < 1000:
                score += 5
            elif avg_rt > 2000:
                score -= 15
            elif avg_rt > 1500:
                score -= 8

        return max(0, min(100, score))

    def _score_ai_detection_risk(self) -> float:
        """Score how likely the caller suspects AI (lower = better)."""
        risk = 10.0  # Start low risk
        m = self.metrics

        # Any AI disclosure that leaked = massive risk
        if m.ai_disclosures_leaked > 0:
            risk = 100.0  # Game over
            return risk

        # Identity traps that weren't handled well
        if m.identity_traps_detected > 0:
            unhandled = m.identity_traps_detected - m.identity_traps_handled
            risk += unhandled * 25

        # Multiple identity traps = caller is suspicious
        if m.identity_traps_detected > 2:
            risk += 20

        # Consistent response timing → robotic pattern
        if len(m.response_times_ms) >= 5:
            cv = _coefficient_of_variation(m.response_times_ms)
            if cv < 0.1:
                risk += 15

        # Uniform response lengths → robotic
        if len(m.agent_turn_lengths) >= 5:
            cv = _coefficient_of_variation(m.agent_turn_lengths)
            if cv < 0.1:
                risk += 15

        # No fillers or disfluencies → too perfect
        if m.total_turns > 5 and m.filler_words_used == 0 and m.disfluencies_used == 0:
            risk += 10

        # Long silences that got noticed
        if m.max_silence_ms > 3000:
            risk += 15

        return max(0, min(100, risk))

    def _identify_strengths(self) -> list[str]:
        """Identify what went well."""
        strengths = []
        m = self.metrics

        if m.booking_made:
            strengths.append("Successfully booked a site visit")
        if m.objections_handled > 0:
            strengths.append(f"Handled {m.objections_handled} objection(s) effectively")
        if m.ai_disclosures_leaked == 0 and m.ai_disclosure_attempts > 0:
            strengths.append(f"Identity shield caught {m.ai_disclosures_caught} AI disclosure(s)")
        if m.identity_traps_handled > 0:
            strengths.append(f"Deflected {m.identity_traps_handled} identity test(s) naturally")
        if m.questions_asked_by_agent >= 5:
            strengths.append("Strong questioning — maintained conversation control")
        if not m.silence_incidents:
            strengths.append("Zero dead-air incidents — smooth conversation")

        return strengths

    def _identify_improvements(self) -> list[str]:
        """Identify areas for improvement."""
        improvements = []
        m = self.metrics

        if m.questions_asked_by_agent < 3 and m.total_turns > 5:
            improvements.append("Ask more questions to understand caller needs and maintain control")
        if m.deal_stage_reached == "awareness" and m.total_turns > 10:
            improvements.append("Failed to advance deal stage despite long conversation — be more proactive")
        if m.total_turns > 5 and not m.site_visit_proposed:
            improvements.append("Missed opportunity to propose a site visit")
        if m.agent_turn_lengths and statistics.mean(m.agent_turn_lengths) > 40:
            improvements.append("Responses too long — keep it conversational (under 30 words avg)")
        if m.objections_detected > m.objections_handled:
            improvements.append(f"{m.objections_detected - m.objections_handled} objection(s) not addressed")

        return improvements

    def _identify_warnings(self) -> list[str]:
        """Identify critical warnings."""
        warnings = []
        m = self.metrics

        if m.ai_disclosures_leaked > 0:
            warnings.append(f"CRITICAL: {m.ai_disclosures_leaked} AI disclosure(s) leaked to caller!")
        if m.max_silence_ms > 3000:
            warnings.append(f"Dead air detected: {m.max_silence_ms:.0f}ms silence gap")
        if m.response_times_ms and max(m.response_times_ms) > 3000:
            warnings.append(f"Slow response: {max(m.response_times_ms):.0f}ms on worst turn")

        return warnings

    def latency_per_interaction(self) -> list[dict[str, Any]]:
        """Per-turn latency rows for call-log charts (milliseconds; UI converts to seconds)."""
        rows: list[dict[str, Any]] = []
        for idx, item in enumerate(self.metrics.interaction_latencies):
            rows.append(
                {
                    "turn": idx + 1,
                    "endpointing_ms": (
                        round(item.get("endpointing_ms"), 1)
                        if item.get("endpointing_ms") is not None
                        else None
                    ),
                    "stt_ms": (
                        round(item.get("stt_ms"), 1) if item.get("stt_ms") is not None else None
                    ),
                    "llm_ms": (
                        round(item.get("llm_ms"), 1) if item.get("llm_ms") is not None else None
                    ),
                    "buffer_ms": (
                        round(item.get("buffer_ms"), 1) if item.get("buffer_ms") is not None else None
                    ),
                    "tts_ms": (
                        round(item.get("tts_ms"), 1) if item.get("tts_ms") is not None else None
                    ),
                    "ttfa_server_ms": (
                        round(item.get("ttfa_server_ms"), 1)
                        if item.get("ttfa_server_ms") is not None
                        else None
                    ),
                    "ttfa_estimated_ms": (
                        round(item.get("ttfa_estimated_ms"), 1)
                        if item.get("ttfa_estimated_ms") is not None
                        else None
                    ),
                    "e2e_ms": (
                        round(item.get("e2e_ms"), 1) if item.get("e2e_ms") is not None else None
                    ),
                }
            )
        if self.metrics._pending_llm_ms is not None:
            rows.append(
                {
                    "turn": len(rows) + 1,
                    "llm_ms": round(self.metrics._pending_llm_ms, 1),
                    "tts_ms": None,
                }
            )
        return rows

    def to_dict(self) -> dict[str, Any]:
        """Export scores and metrics as a dictionary for DB storage."""
        scores = self.calculate_scores()
        per_interaction = self.latency_per_interaction()
        return {
            "scores": {
                "human_likeness": round(scores.human_likeness, 1),
                "sales_effectiveness": round(scores.sales_effectiveness, 1),
                "conversation_flow": round(scores.conversation_flow, 1),
                "ai_detection_risk": round(scores.ai_detection_risk, 1),
                "overall": round(scores.overall, 1),
            },
            "strengths": scores.strengths,
            "improvements": scores.improvements,
            "warnings": scores.warnings,
            "metrics": {
                "total_turns": self.metrics.total_turns,
                "avg_response_time_ms": round(statistics.mean(self.metrics.response_times_ms), 0) if self.metrics.response_times_ms else None,
                "response_times_ms": [round(v, 1) for v in self.metrics.response_times_ms],
                "tts_latencies_ms": [round(v, 1) for v in self.metrics.tts_latencies_ms],
                "latency_per_interaction": per_interaction,
                "max_silence_ms": round(self.metrics.max_silence_ms, 0),
                "silence_incidents": len(self.metrics.silence_incidents),
                "questions_asked": self.metrics.questions_asked_by_agent,
                "objections_handled": self.metrics.objections_handled,
                "deal_stage": self.metrics.deal_stage_reached,
                "ai_disclosure_attempts": self.metrics.ai_disclosure_attempts,
                "ai_disclosures_caught": self.metrics.ai_disclosures_caught,
                "ai_disclosures_leaked": self.metrics.ai_disclosures_leaked,
                "identity_traps": self.metrics.identity_traps_detected,
                "booking_made": self.metrics.booking_made,
            },
        }


def _coefficient_of_variation(values: list[float]) -> float:
    """Calculate coefficient of variation (std/mean). Higher = more variation."""
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0
    return statistics.stdev(values) / mean
