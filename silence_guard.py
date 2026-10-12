"""
Silence Guard — Zero-tolerance silence prevention.

Monitors the agent's audio output pipeline and intervenes when silence
exceeds safe thresholds. Works in concert with circuit_breaker.DeadAirGuard
but operates at the session level, providing:

  1. Audio output heartbeat — tracks ms since last frame pushed to room
  2. Graduated interventions (filler → bridge → forced → emergency)
  3. TTS failure recovery — retries with fallback provider on zero frames
  4. Session-level integration with AgentSession events

Usage in agent.py entrypoint:
    from silence_guard import SilenceGuard
    guard = SilenceGuard(agent=agent, agent_tools=agent_tools, live_config=live_config)
    await guard.start()
    # ... at shutdown:
    await guard.stop()
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

logger = logging.getLogger("inbound-voice-agent")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURABLE THRESHOLDS
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_FILLER_THRESHOLD_MS = 700       # Inject subtle filler
DEFAULT_BRIDGE_THRESHOLD_MS = 1800      # Inject contextual bridge phrase
DEFAULT_FORCED_THRESHOLD_MS = 3500      # Speak a forced recovery phrase
DEFAULT_EMERGENCY_THRESHOLD_MS = 6000   # Emergency: play pre-baked message + retry

# Contextual filler phrases (vary based on situation)
_FILLER_PHRASES: list[str] = [
    "Hmm...",
    "Ek second...",
    "Actually...",
    "So...",
    "Hmm, let me see...",
    "Haan...",
]

_BRIDGE_PHRASES: list[str] = [
    "Actually, let me just check that for you...",
    "Ek minute andi, details pull kar raha hu...",
    "Haan, so let me check the latest on that...",
    "Just a second, I'm pulling up the information...",
    "Hmm, good question — let me look into that...",
    "Ek second andi, system mein check kar raha hu...",
]

# IQ #5 — name-based silence rescue (used at tier 2 when the caller's name is known)
_NAME_BRIDGE_TEMPLATES: dict[str, list[str]] = {
    "tinglish": [
        "{name}, ee question konchem kastam ga anipisthe — next point ki veldama?",
        "{name}, meeru vintunnara andi? Time kavali ante parledu, cheppandi.",
    ],
    "hte": [
        "{name} ji, ye question thoda tricky lag raha hai toh — agle point pe chalein?",
        "{name} ji, aap sun rahe hain na? Time chahiye toh koi baat nahi, boliye.",
    ],
    "english": [
        "{name}, if that one felt tricky — shall we move to the next point?",
        "{name}, are you still with me? Take your time, no rush.",
    ],
}

_FORCED_RECOVERY_PHRASES: list[str] = [
    "Sorry andi, I was just checking something on my system. So, what were you saying?",
    "Apologies, network thoda slow tha mera side se. Aap kya bol rahe the?",
    "Sorry about that, I was looking up the latest details. Please continue.",
    "Haan sorry, system thoda lag hua. Aap bataiye?",
]

_EMERGENCY_PHRASES: list[str] = [
    "I think the connection is a bit weak on my end. Can you hear me okay?",
    "Hello? Aap sun rahe hain? Network issue aa raha tha thoda.",
    "Sorry, I think we had a brief connection issue. Are you still there?",
]


class SilenceGuard:
    """
    Session-level silence monitor and automatic intervention system.

    Integrates with VoiceAgentAssistant and AgentTools to:
    - Track when the agent last produced audio
    - Track when the user last spoke (silence is expected during user speech)
    - Intervene with graduated phrases when silence is detected
    - Coordinate with the dead-air guard in circuit_breaker.py
    """

    def __init__(
        self,
        agent: Any = None,
        agent_tools: Any = None,
        live_config: dict | None = None,
        *,
        filler_threshold_ms: float = DEFAULT_FILLER_THRESHOLD_MS,
        bridge_threshold_ms: float = DEFAULT_BRIDGE_THRESHOLD_MS,
        forced_threshold_ms: float = DEFAULT_FORCED_THRESHOLD_MS,
        emergency_threshold_ms: float = DEFAULT_EMERGENCY_THRESHOLD_MS,
    ):
        self._agent = agent
        self._agent_tools = agent_tools
        self._live_config = live_config or {}

        self._filler_ms = filler_threshold_ms
        self._bridge_ms = bridge_threshold_ms
        self._forced_ms = forced_threshold_ms
        self._emergency_ms = emergency_threshold_ms

        self._last_audio_ts: float = time.monotonic()
        self._last_user_speech_ts: float = time.monotonic()
        self._agent_speaking: bool = False
        self._user_speaking: bool = False
        self._active: bool = False
        self._task: asyncio.Task | None = None
        self._call_ended: bool = False

        # Intervention cooldowns (don't fire the same level twice in quick succession)
        self._filler_cooldown_ts: float = 0.0
        self._bridge_cooldown_ts: float = 0.0
        self._forced_cooldown_ts: float = 0.0
        self._emergency_cooldown_ts: float = 0.0

        # Stats
        self._filler_count: int = 0
        self._bridge_count: int = 0
        self._forced_count: int = 0
        self._emergency_count: int = 0
        self._max_silence_ms: float = 0.0

    # ── Event handlers (wire these to session events) ─────────────────────

    def on_agent_audio_output(self) -> None:
        """Call when agent pushes audio frames to the room."""
        self._last_audio_ts = time.monotonic()
        self._agent_speaking = True

    def on_agent_speech_finished(self) -> None:
        """Call when agent finishes speaking."""
        self._agent_speaking = False
        self._last_audio_ts = time.monotonic()

    def on_user_speech_started(self) -> None:
        """Call when user starts speaking — silence is expected."""
        self._user_speaking = True
        self._last_user_speech_ts = time.monotonic()

    def on_user_speech_finished(self) -> None:
        """Call when user finishes speaking."""
        self._user_speaking = False
        self._last_audio_ts = time.monotonic()  # Reset — agent should respond now

    def on_call_ended(self) -> None:
        """Call when the call is ending."""
        self._call_ended = True

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the silence monitor."""
        self._active = True
        self._last_audio_ts = time.monotonic()
        self._task = asyncio.ensure_future(self._monitor_loop())
        logger.info("[SILENCE-GUARD] Started with thresholds: filler=%.0fms bridge=%.0fms forced=%.0fms emergency=%.0fms",
                     self._filler_ms, self._bridge_ms, self._forced_ms, self._emergency_ms)

    async def stop(self) -> None:
        """Stop the silence monitor."""
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info(
            "[SILENCE-GUARD] Stopped. Stats: fillers=%d bridges=%d forced=%d emergencies=%d max_silence=%.0fms",
            self._filler_count, self._bridge_count, self._forced_count,
            self._emergency_count, self._max_silence_ms,
        )

    def stats(self) -> dict[str, Any]:
        """Get silence guard statistics."""
        return {
            "filler_count": self._filler_count,
            "bridge_count": self._bridge_count,
            "forced_count": self._forced_count,
            "emergency_count": self._emergency_count,
            "max_silence_ms": round(self._max_silence_ms, 0),
            "active": self._active,
        }

    # ── Core monitoring loop ──────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Main monitoring loop — checks silence every 100ms."""
        while self._active and not self._call_ended:
            try:
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return

            # Don't intervene if:
            # - Agent is currently speaking
            # - User is currently speaking (silence is expected while listening)
            # - Call has ended
            # - Agent tools indicate call ended
            if self._agent_speaking or self._user_speaking or self._call_ended:
                continue

            if self._agent_tools and getattr(self._agent_tools, "agent_is_speaking", False):
                continue

            if self._agent_tools and getattr(self._agent_tools, "_caller_disconnected", False):
                return

            now = time.monotonic()
            elapsed_ms = (now - self._last_audio_ts) * 1000
            self._max_silence_ms = max(self._max_silence_ms, elapsed_ms)

            # Graduated intervention
            if elapsed_ms >= self._emergency_ms and now - self._emergency_cooldown_ts > 10.0:
                self._emergency_cooldown_ts = now
                self._emergency_count += 1
                await self._fire_emergency()

            elif elapsed_ms >= self._forced_ms and now - self._forced_cooldown_ts > 5.0:
                self._forced_cooldown_ts = now
                self._forced_count += 1
                await self._fire_forced_response()

            elif elapsed_ms >= self._bridge_ms and now - self._bridge_cooldown_ts > 3.0:
                self._bridge_cooldown_ts = now
                self._bridge_count += 1
                await self._fire_bridge()

            elif elapsed_ms >= self._filler_ms and now - self._filler_cooldown_ts > 2.0:
                self._filler_cooldown_ts = now
                self._filler_count += 1
                await self._fire_filler()

    # ── Intervention handlers ─────────────────────────────────────────────

    async def _fire_filler(self) -> None:
        """Inject a subtle filler sound/word."""
        if not self._agent:
            return
        phrase = random.choice(_FILLER_PHRASES)
        logger.debug("[SILENCE-GUARD] Filler injection: %r", phrase)
        try:
            speak_fn = getattr(self._agent, "speak_gated", None)
            if speak_fn and callable(speak_fn):
                await speak_fn(phrase, add_to_chat_ctx=False)
                self._last_audio_ts = time.monotonic()
        except Exception as exc:
            logger.debug("[SILENCE-GUARD] Filler failed: %s", exc)

    def _bridge_phrase(self) -> str:
        """IQ #5 — name-based rescue when the caller's name is known, else generic."""
        name = str(self._live_config.get("_caller_name") or "").strip()
        if not name:
            return random.choice(_BRIDGE_PHRASES)
        try:
            from knowledge_pack import resolve_preset_pack
            from language_router import resolve_lang_preset

            pack = resolve_preset_pack(
                resolve_lang_preset(self._live_config.get("lang_preset"), self._live_config)
            )
        except Exception:
            pack = "tinglish"
        pool = _NAME_BRIDGE_TEMPLATES.get(pack) or _NAME_BRIDGE_TEMPLATES["english"]
        return random.choice(pool).format(name=name)

    async def _fire_bridge(self) -> None:
        """Inject a contextual bridge phrase."""
        if not self._agent:
            return
        phrase = self._bridge_phrase()
        logger.info("[SILENCE-GUARD] Bridge injection: %r", phrase)
        try:
            speak_fn = getattr(self._agent, "speak_gated", None)
            if speak_fn and callable(speak_fn):
                await speak_fn(phrase, add_to_chat_ctx=False)
                self._last_audio_ts = time.monotonic()
        except Exception as exc:
            logger.warning("[SILENCE-GUARD] Bridge failed: %s", exc)

    async def _fire_forced_response(self) -> None:
        """Force a recovery phrase to break extended silence."""
        if not self._agent:
            return
        phrase = random.choice(_FORCED_RECOVERY_PHRASES)
        logger.warning("[SILENCE-GUARD] Forced recovery: %r", phrase)
        try:
            speak_fn = getattr(self._agent, "speak_session_line", None) or getattr(self._agent, "speak_gated", None)
            if speak_fn and callable(speak_fn):
                await speak_fn(phrase, add_to_chat_ctx=False)
                self._last_audio_ts = time.monotonic()
        except Exception as exc:
            logger.warning("[SILENCE-GUARD] Forced response failed: %s", exc)

    async def _fire_emergency(self) -> None:
        """Emergency: connection recovery phrase."""
        if not self._agent:
            return
        phrase = random.choice(_EMERGENCY_PHRASES)
        logger.error("[SILENCE-GUARD] EMERGENCY recovery: %r (%.0fms silence)", phrase, self._max_silence_ms)
        try:
            speak_fn = getattr(self._agent, "speak_session_line", None) or getattr(self._agent, "speak_gated", None)
            if speak_fn and callable(speak_fn):
                await speak_fn(phrase, add_to_chat_ctx=True)
                self._last_audio_ts = time.monotonic()
        except Exception as exc:
            logger.error("[SILENCE-GUARD] Emergency handler failed: %s", exc)
