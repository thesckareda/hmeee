"""
Circuit Breaker — Production resilience for all external services.

Implements the circuit-breaker pattern for LLM, TTS, STT, and external APIs.
Each service has independent health tracking with three states:
  CLOSED  → Healthy, requests pass through normally
  OPEN    → Failing, all requests routed to fallback immediately
  HALF_OPEN → Probing recovery, single request allowed through

Auto-fallback chains:
  LLM:  Gemini 2.5 Flash → hardcoded response templates
  TTS:  Cartesia → Gemini TTS → Sarvam → Pre-baked PCM
  STT:  Sarvam → confidence gating → "I didn't catch that" prompt

Dead-air prevention:
  If TTS produces zero frames → immediately retry with fallback provider.
  If LLM TTFT > threshold → pre-emptive filler injection.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("inbound-voice-agent")


# ══════════════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER CORE
# ══════════════════════════════════════════════════════════════════════════════

class CircuitState(str, Enum):
    CLOSED = "closed"        # Healthy
    OPEN = "open"            # Failing — use fallback
    HALF_OPEN = "half_open"  # Probing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker instance."""
    failure_threshold: int = 3        # Failures before opening
    recovery_timeout: float = 30.0    # Seconds before OPEN → HALF_OPEN probe
    success_threshold: int = 2        # Successes in HALF_OPEN before closing
    window_size: float = 60.0         # Rolling window for failure counting
    half_open_max_concurrent: int = 1 # Max concurrent requests in HALF_OPEN


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for a single service.

    Usage:
        breaker = CircuitBreaker("gemini_llm")
        if breaker.should_allow():
            try:
                result = await call_service()
                breaker.record_success()
            except Exception:
                breaker.record_failure()
                result = await fallback()
        else:
            result = await fallback()
    """
    name: str
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)

    # Internal state
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_timestamps: deque[float] = field(default_factory=deque, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _last_open_time: float = field(default=0.0, init=False)
    _half_open_successes: int = field(default=0, init=False)
    _half_open_in_flight: int = field(default=0, init=False)
    _total_failures: int = field(default=0, init=False)
    _total_successes: int = field(default=0, init=False)
    _total_fallbacks: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_healthy(self) -> bool:
        return self._state == CircuitState.CLOSED

    def _prune_old_failures(self, now: float) -> None:
        """Remove failures outside the rolling window."""
        cutoff = now - self.config.window_size
        while self._failure_timestamps and self._failure_timestamps[0] < cutoff:
            self._failure_timestamps.popleft()

    def should_allow(self) -> bool:
        """Check if a request should be allowed through."""
        now = time.monotonic()

        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if now - self._last_open_time >= self.config.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_successes = 0
                self._half_open_in_flight = 0
                logger.info(
                    "[CIRCUIT-BREAKER] %s: OPEN → HALF_OPEN (probing recovery after %.1fs)",
                    self.name, self.config.recovery_timeout,
                )
                # Fall through to HALF_OPEN logic below
            else:
                return False

        if self._state == CircuitState.HALF_OPEN:
            return self._half_open_in_flight < self.config.half_open_max_concurrent

        return True

    def record_success(self) -> None:
        """Record a successful request."""
        self._total_successes += 1

        if self._state == CircuitState.HALF_OPEN:
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
            self._half_open_successes += 1
            if self._half_open_successes >= self.config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_timestamps.clear()
                logger.info("[CIRCUIT-BREAKER] %s: HALF_OPEN → CLOSED (recovered)", self.name)

    def record_failure(self, error: Exception | None = None) -> None:
        """Record a failed request."""
        now = time.monotonic()
        self._total_failures += 1
        self._last_failure_time = now

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — reopen
            self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
            self._state = CircuitState.OPEN
            self._last_open_time = now
            logger.warning(
                "[CIRCUIT-BREAKER] %s: HALF_OPEN → OPEN (probe failed: %s)",
                self.name, error or "unknown",
            )
            return

        # CLOSED state — count failures in window
        self._failure_timestamps.append(now)
        self._prune_old_failures(now)

        if len(self._failure_timestamps) >= self.config.failure_threshold:
            self._state = CircuitState.OPEN
            self._last_open_time = now
            logger.error(
                "[CIRCUIT-BREAKER] %s: CLOSED → OPEN (%d failures in %.0fs window). Error: %s",
                self.name,
                len(self._failure_timestamps),
                self.config.window_size,
                error or "unknown",
            )

    def record_fallback(self) -> None:
        """Record that a fallback was used."""
        self._total_fallbacks += 1

    def force_open(self, reason: str = "") -> None:
        """Manually trip the circuit breaker."""
        self._state = CircuitState.OPEN
        self._last_open_time = time.monotonic()
        logger.warning("[CIRCUIT-BREAKER] %s: FORCE OPEN — %s", self.name, reason)

    def force_close(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_timestamps.clear()
        self._half_open_successes = 0
        logger.info("[CIRCUIT-BREAKER] %s: FORCE CLOSED (manual reset)", self.name)

    def stats(self) -> dict[str, Any]:
        """Get current stats."""
        return {
            "name": self.name,
            "state": self._state.value,
            "total_successes": self._total_successes,
            "total_failures": self._total_failures,
            "total_fallbacks": self._total_fallbacks,
            "failures_in_window": len(self._failure_timestamps),
            "last_failure_age_sec": round(time.monotonic() - self._last_failure_time, 1) if self._last_failure_time else None,
        }


# ══════════════════════════════════════════════════════════════════════════════
# SERVICE-SPECIFIC CIRCUIT BREAKERS (Singleton Registry)
# ══════════════════════════════════════════════════════════════════════════════

_SERVICE_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(service_name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreaker:
    """Get or create a circuit breaker for a named service."""
    if service_name not in _SERVICE_BREAKERS:
        cfg = config or _default_config_for_service(service_name)
        _SERVICE_BREAKERS[service_name] = CircuitBreaker(name=service_name, config=cfg)
    return _SERVICE_BREAKERS[service_name]


def _default_config_for_service(service_name: str) -> CircuitBreakerConfig:
    """Service-specific default configurations."""
    configs = {
        "gemini_llm": CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=45.0,
            success_threshold=2,
            window_size=60.0,
        ),
        "gemini_tts": CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=30.0,
            success_threshold=1,
            window_size=45.0,
        ),
        "sarvam_tts": CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=20.0,
            success_threshold=2,
            window_size=60.0,
        ),
        "cartesia_tts": CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=25.0,
            success_threshold=1,
            window_size=45.0,
        ),
        "sarvam_stt": CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=20.0,
            success_threshold=2,
            window_size=60.0,
        ),
        "supabase": CircuitBreakerConfig(
            failure_threshold=5,
            recovery_timeout=60.0,
            success_threshold=3,
            window_size=120.0,
        ),
        "whatsapp": CircuitBreakerConfig(
            failure_threshold=3,
            recovery_timeout=120.0,
            success_threshold=2,
            window_size=180.0,
        ),
        "deepseek": CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=60.0,
            success_threshold=1,
            window_size=60.0,
        ),
    }
    return configs.get(service_name, CircuitBreakerConfig())


def all_breaker_stats() -> list[dict[str, Any]]:
    """Get stats for all registered circuit breakers."""
    return [b.stats() for b in _SERVICE_BREAKERS.values()]


def reset_all_breakers() -> None:
    """Reset all circuit breakers to CLOSED."""
    for breaker in _SERVICE_BREAKERS.values():
        breaker.force_close()


# ══════════════════════════════════════════════════════════════════════════════
# LATENCY MONITORING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class LatencyTracker:
    """Track latency for a service and alert on SLA violations."""
    service_name: str
    sla_ms: float = 800.0            # Target latency in ms
    warning_threshold_ms: float = 600.0  # Warning threshold
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=50), init=False)
    sla_violations: int = field(default=0, init=False)

    def record(self, latency_ms: float) -> None:
        """Record a latency sample."""
        self.samples.append(latency_ms)
        if latency_ms > self.sla_ms:
            self.sla_violations += 1
            logger.warning(
                "[LATENCY] %s: SLA violation — %.0fms (target: %.0fms)",
                self.service_name, latency_ms, self.sla_ms,
            )

    @property
    def avg_ms(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)

    @property
    def p95_ms(self) -> float:
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def stats(self) -> dict[str, Any]:
        return {
            "service": self.service_name,
            "avg_ms": round(self.avg_ms, 1),
            "p95_ms": round(self.p95_ms, 1),
            "sla_violations": self.sla_violations,
            "total_samples": len(self.samples),
        }


# Service-level latency trackers
_LATENCY_TRACKERS: dict[str, LatencyTracker] = {}


def get_latency_tracker(service_name: str, sla_ms: float = 800.0) -> LatencyTracker:
    """Get or create a latency tracker."""
    if service_name not in _LATENCY_TRACKERS:
        _LATENCY_TRACKERS[service_name] = LatencyTracker(service_name=service_name, sla_ms=sla_ms)
    return _LATENCY_TRACKERS[service_name]


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK RESPONSE TEMPLATES (last-resort when all LLM providers fail)
# ══════════════════════════════════════════════════════════════════════════════

_FALLBACK_RESPONSES: dict[str, list[str]] = {
    "greeting": [
        "Namaste! Arjun here from VK Realty. How can I help you today?",
        "Hello! This is Arjun from VK Realty. What can I help you with?",
    ],
    "price_query": [
        "For the exact pricing and available units, let me check with my team and get back to you. Can I call you back in 10 minutes?",
        "The prices vary depending on the floor and configuration. Let me pull up the latest rate card and call you back shortly.",
    ],
    "availability": [
        "Let me check the latest availability and get back to you. Which configuration were you looking at — 2BHK or 3BHK?",
    ],
    "site_visit": [
        "Absolutely, I can arrange a site visit. Which day works best for you — weekday or weekend?",
    ],
    "general": [
        "Sure, let me note that down. Can I call you back in a few minutes with the details?",
        "That's a great question. Let me check with my team and get back to you right away.",
        "I want to give you the most accurate information. Let me verify and call you back shortly.",
    ],
    "cant_hear": [
        "I'm sorry, I didn't catch that clearly. Could you please repeat?",
        "Sorry, the connection seems a bit weak. Could you say that again?",
    ],
    "technical_issue": [
        "I'm having a slight network issue on my end. Can I call you back in 2 minutes?",
        "The connection is breaking up a bit. Let me call you right back.",
    ],
}


def get_fallback_response(category: str = "general") -> str:
    """Get a pre-built fallback response when all LLM providers fail."""
    import random
    options = _FALLBACK_RESPONSES.get(category, _FALLBACK_RESPONSES["general"])
    return random.choice(options)


# ══════════════════════════════════════════════════════════════════════════════
# DEAD-AIR PREVENTION TIMER
# ══════════════════════════════════════════════════════════════════════════════

class DeadAirGuard:
    """
    Monitors silence duration and triggers interventions.

    Graduated intervention:
      500ms  → inject filler ("Hmm...")
     1500ms  → inject bridge ("Let me check...")
     3000ms  → forced response ("Sorry, I was looking something up...")
     5000ms  → emergency recovery
    """

    def __init__(
        self,
        on_filler: Callable[[], Any] | None = None,
        on_bridge: Callable[[], Any] | None = None,
        on_forced_response: Callable[[], Any] | None = None,
        on_emergency: Callable[[], Any] | None = None,
        filler_threshold_ms: float = 500,
        bridge_threshold_ms: float = 1500,
        forced_threshold_ms: float = 3000,
        emergency_threshold_ms: float = 5000,
    ):
        self._on_filler = on_filler
        self._on_bridge = on_bridge
        self._on_forced_response = on_forced_response
        self._on_emergency = on_emergency
        self._filler_ms = filler_threshold_ms
        self._bridge_ms = bridge_threshold_ms
        self._forced_ms = forced_threshold_ms
        self._emergency_ms = emergency_threshold_ms
        self._last_audio_time: float = time.monotonic()
        self._agent_speaking: bool = False
        self._active: bool = False
        self._task: asyncio.Task | None = None
        self._filler_fired: bool = False
        self._bridge_fired: bool = False
        self._forced_fired: bool = False
        self._emergency_fired: bool = False

    def mark_audio_output(self) -> None:
        """Call whenever agent produces audio output."""
        self._last_audio_time = time.monotonic()
        self._filler_fired = False
        self._bridge_fired = False
        self._forced_fired = False
        self._emergency_fired = False

    def mark_agent_speaking(self, speaking: bool) -> None:
        """Track agent speaking state."""
        self._agent_speaking = speaking
        if speaking:
            self.mark_audio_output()

    def mark_user_speaking(self) -> None:
        """Reset timers when user is speaking (silence is expected)."""
        self._last_audio_time = time.monotonic()
        self._filler_fired = False
        self._bridge_fired = False
        self._forced_fired = False
        self._emergency_fired = False

    async def start(self) -> None:
        """Start the dead-air monitoring loop."""
        self._active = True
        self._last_audio_time = time.monotonic()
        self._task = asyncio.ensure_future(self._monitor_loop())

    async def stop(self) -> None:
        """Stop monitoring."""
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _monitor_loop(self) -> None:
        """Continuous monitoring loop."""
        while self._active:
            try:
                await asyncio.sleep(0.1)  # Check every 100ms
            except asyncio.CancelledError:
                return

            if self._agent_speaking:
                continue

            elapsed_ms = (time.monotonic() - self._last_audio_time) * 1000

            if elapsed_ms >= self._emergency_ms and not self._emergency_fired:
                self._emergency_fired = True
                logger.error("[DEAD-AIR] EMERGENCY: %.0fms silence — triggering recovery", elapsed_ms)
                if self._on_emergency:
                    try:
                        result = self._on_emergency()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("[DEAD-AIR] Emergency handler failed: %s", e)

            elif elapsed_ms >= self._forced_ms and not self._forced_fired:
                self._forced_fired = True
                logger.warning("[DEAD-AIR] Forced response at %.0fms silence", elapsed_ms)
                if self._on_forced_response:
                    try:
                        result = self._on_forced_response()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.warning("[DEAD-AIR] Forced response handler failed: %s", e)

            elif elapsed_ms >= self._bridge_ms and not self._bridge_fired:
                self._bridge_fired = True
                logger.info("[DEAD-AIR] Bridge injection at %.0fms silence", elapsed_ms)
                if self._on_bridge:
                    try:
                        result = self._on_bridge()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.debug("[DEAD-AIR] Bridge handler failed: %s", e)

            elif elapsed_ms >= self._filler_ms and not self._filler_fired:
                self._filler_fired = True
                if self._on_filler:
                    try:
                        result = self._on_filler()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.debug("[DEAD-AIR] Filler handler failed: %s", e)


# ══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE: Wrap an async call with circuit breaker + latency tracking
# ══════════════════════════════════════════════════════════════════════════════

async def protected_call(
    service_name: str,
    coro,
    *,
    fallback: Callable[[], Any] | None = None,
    fallback_value: Any = None,
    sla_ms: float = 800.0,
) -> Any:
    """
    Execute an async call with circuit breaker protection and latency tracking.

    Example:
        result = await protected_call(
            "gemini_llm",
            llm.generate(prompt),
            fallback=lambda: get_fallback_response("general"),
        )
    """
    breaker = get_breaker(service_name)
    tracker = get_latency_tracker(service_name, sla_ms=sla_ms)

    if not breaker.should_allow():
        breaker.record_fallback()
        logger.info("[CIRCUIT-BREAKER] %s: circuit OPEN — using fallback", service_name)
        if fallback:
            fb = fallback()
            if asyncio.iscoroutine(fb):
                return await fb
            return fb
        return fallback_value

    start = time.monotonic()
    try:
        result = await coro
        latency_ms = (time.monotonic() - start) * 1000
        tracker.record(latency_ms)
        breaker.record_success()
        return result
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        tracker.record(latency_ms)
        breaker.record_failure(exc)
        logger.warning(
            "[CIRCUIT-BREAKER] %s: call failed after %.0fms — %s",
            service_name, latency_ms, exc,
        )
        if fallback:
            breaker.record_fallback()
            fb = fallback()
            if asyncio.iscoroutine(fb):
                return await fb
            return fb
        if fallback_value is not None:
            breaker.record_fallback()
            return fallback_value
        raise


async def _resolve_fallback(
    fallback: Callable[[], Any] | None,
    fallback_value: Any,
) -> Any:
    if fallback:
        fb = fallback()
        if asyncio.iscoroutine(fb):
            return await fb
        return fb
    return fallback_value


async def protected_async_iter(
    service_name: str,
    stream,
    *,
    fallback: Callable[[], Any] | None = None,
    fallback_value: Any = None,
    sla_ms: float = 800.0,
):
    """
    Async-generator wrapper with circuit breaker protection (streaming LLM path).

    Mirrors ``protected_call`` semantics: when the circuit is OPEN or the stream
    fails before emitting any item, yields fallback content instead of crashing.
    """
    breaker = get_breaker(service_name)
    tracker = get_latency_tracker(service_name, sla_ms=sla_ms)

    if not breaker.should_allow():
        breaker.record_fallback()
        logger.info("[CIRCUIT-BREAKER] %s: circuit OPEN — using stream fallback", service_name)
        fb = await _resolve_fallback(fallback, fallback_value)
        if fb is not None:
            if isinstance(fb, str):
                yield fb
            else:
                for item in fb:
                    yield item
        return

    start = time.monotonic()
    got_any = False
    try:
        async for chunk in stream:
            got_any = True
            yield chunk
        latency_ms = (time.monotonic() - start) * 1000
        tracker.record(latency_ms)
        breaker.record_success()
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        tracker.record(latency_ms)
        breaker.record_failure(exc)
        logger.warning(
            "[CIRCUIT-BREAKER] %s: stream failed after %.0fms — %s",
            service_name,
            latency_ms,
            exc,
        )
        if got_any:
            raise
        breaker.record_fallback()
        fb = await _resolve_fallback(fallback, fallback_value)
        if fb is not None:
            if isinstance(fb, str):
                yield fb
            else:
                for item in fb:
                    yield item
