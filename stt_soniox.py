"""
Streaming STT via the official Soniox Python SDK (realtime WebSocket).

Sends 100ms PCM chunks as caller speaks.
Emits partial and final transcripts via async generators.
Swappable with SarvamSTTStream — identical public interface.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import TYPE_CHECKING

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

if TYPE_CHECKING:
    from soniox.realtime.async_stt import AsyncRealtimeSTTSession

logger = logging.getLogger("inbound-voice-agent")

DEFAULT_SONIOX_MODEL = "stt-rt-v5"
SONIOX_RT_MODELS = frozenset({"stt-rt-v5", "stt-rt-v4", "stt-rt-v3"})
SONIOX_ASYNC_MODELS = frozenset({"stt-async-v5", "stt-async-v4", "stt-async-v3"})
SONIOX_RT_WSS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
DEFAULT_LANGUAGE_HINTS = ["te", "en", "hi"]
SONIOX_SPECIAL_TOKEN_TEXT = frozenset({"<fin>", "<end>"})
_LANG_TAG_RE = re.compile(r"^\[[\w-]+\]\s*")


def _token_text(tokens: list) -> str:
    parts = []
    for token in tokens:
        text = token.text or ""
        if text in SONIOX_SPECIAL_TOKEN_TEXT:
            continue
        parts.append(text)
    return _clean_transcript_text("".join(parts))


def _clean_transcript_text(text: str) -> str:
    cleaned = (text or "").replace("<fin>", "").replace("<end>", "").strip()
    cleaned = _LANG_TAG_RE.sub("", cleaned).strip()
    return cleaned


def _token_confidence(tokens: list) -> float:
    scores = [t.confidence for t in tokens if t.confidence is not None]
    return sum(scores) / len(scores) if scores else 1.0


def _token_language(tokens: list, fallback: str) -> str:
    for token in reversed(tokens):
        if token.language:
            return token.language
    return fallback


def _is_final_token(token) -> bool:
    return token.is_final is True


def _render_transcript(final_tokens: list, non_final_tokens: list) -> str:
    """Plain transcript from accumulated finals + current non-finals (no speaker/lang markup)."""
    return _token_text(final_tokens + non_final_tokens)


def normalize_soniox_realtime_model(model: str) -> str:
    """
    Live phone calls require Soniox *realtime* STT (stt-rt-v5).

    stt-async-v5 is batch/file transcription only — upload, poll, download.
    It cannot keep up with a SIP conversation.
    """
    raw = (model or "").strip()
    if not raw or raw.lower() in ("soniox", "soniox-v2", "default"):
        return DEFAULT_SONIOX_MODEL
    lowered = raw.lower()
    if lowered in SONIOX_ASYNC_MODELS or lowered.startswith("stt-async"):
        raise ValueError(
            f"Soniox model '{raw}' is async/batch-only (file upload + wait). "
            f"For live calls set stt_model to '{DEFAULT_SONIOX_MODEL}'."
        )
    if lowered not in SONIOX_RT_MODELS and not lowered.startswith("stt-rt"):
        logger.warning("[SONIOX-STT] Unknown model '%s' — using %s", raw, DEFAULT_SONIOX_MODEL)
        return DEFAULT_SONIOX_MODEL
    return raw


def _default_realtime_context():
    """Domain vocabulary for VK Realty inbound calls."""
    try:
        from soniox.types import StructuredContext, StructuredContextGeneralItem
    except ImportError:
        return None
    return StructuredContext(
        general=[
            StructuredContextGeneralItem(key="domain", value="Real estate sales"),
            StructuredContextGeneralItem(key="company", value="VK Realty"),
            StructuredContextGeneralItem(key="locations", value="Hyderabad Kokapet Neopolis Narsingi Gachibowli"),
        ],
        terms=[
            "VK Realty",
            "Kokapet",
            "Neopolis",
            "Narsingi",
            "Gachibowli",
            "BHK",
            "site visit",
            "Crore",
            "Telugu",
            "Hinglish",
        ],
    )


class SonioxSTTStream:
    """Streaming STT via Soniox stt-rt WebSocket (official Python SDK)."""

    CHUNK_MS = 100
    SAMPLE_RATE = 16000
    CHANNELS = 1
    BYTES_PER_SAMPLE = 2
    CHUNK_BYTES = int(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_MS / 1000)

    def __init__(
        self,
        api_key: str,
        language_code: str = "unknown",
        model: str = DEFAULT_SONIOX_MODEL,
        endpointing_ms: int = 700,
        confidence_threshold: float = 0.6,
        language_hints: list[str] | None = None,
        endpoint_sensitivity: float = 0.35,
    ):
        self.api_key = api_key
        self.language_code = language_code
        self.model = normalize_soniox_realtime_model(model)
        self.endpointing_ms = endpointing_ms
        self.endpoint_sensitivity = max(-1.0, min(float(endpoint_sensitivity), 1.0))
        self.confidence_threshold = confidence_threshold
        if language_hints:
            self.language_hints = language_hints
        elif not language_code or language_code in ("unknown", "auto"):
            self.language_hints = list(DEFAULT_LANGUAGE_HINTS)
        else:
            self.language_hints = [language_code.split("-")[0].lower()]
        # Multilingual calls: strict mode hurts accuracy with multiple hints.
        self.language_hints_strict = len(self.language_hints) == 1

        self._client = None
        self._session: AsyncRealtimeSTTSession | None = None
        self._partial_queue: asyncio.Queue = asyncio.Queue()
        self._final_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._first_partial_recorded = False
        self._audio_send_times: list[float] = []
        self._last_partial_text = ""
        self._connect_lock = asyncio.Lock()
        self._connecting = False
        self._keepalive_task: asyncio.Task | None = None
        self._utterance_final_tokens: list = []
        self._pending_finalize = False
        self._last_emitted_final = ""
        self._last_emitted_final_at = 0.0
        self._last_queued_final = ""
        self._events_seen = 0
        self._receive_generation = 0
        self._closed = False

    def _build_config(self):
        from soniox.types import RealtimeSTTConfig

        max_endpoint_delay_ms = max(500, min(int(self.endpointing_ms), 3000))
        context = _default_realtime_context()
        kwargs: dict = {
            "model": self.model,
            "audio_format": "pcm_s16le",
            "sample_rate": self.SAMPLE_RATE,
            "num_channels": self.CHANNELS,
            "language_hints": self.language_hints,
            "language_hints_strict": self.language_hints_strict,
            "context": context,
            "enable_language_identification": True,
            "enable_endpoint_detection": True,
            "max_endpoint_delay_ms": max_endpoint_delay_ms,
        }
        if "v5" in (self.model or "").lower():
            kwargs["endpoint_sensitivity"] = self.endpoint_sensitivity
            kwargs["endpoint_latency_adjustment_level"] = 2
        return RealtimeSTTConfig(**kwargs)

    async def _stop_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
        self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        try:
            while self._running and self._session:
                await asyncio.sleep(4.0)
                if self._session and self._running:
                    await self._session.keep_alive()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("[SONIOX-STT] Keepalive ended: %s", e)

    async def _teardown_session(self) -> None:
        await self._stop_keepalive()
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        self._running = False
        self._connecting = False

    async def connect(self) -> None:
        from soniox.client import AsyncSonioxClient

        await self._teardown_session()
        if self._client is None:
            self._client = AsyncSonioxClient(
                api_key=self.api_key,
                websocket_base_url=SONIOX_RT_WSS_URL,
            )
        config = self._build_config()
        self._session = self._client.realtime.stt.connect(config=config)
        await self._session.__aenter__()

        self._running = True
        self._connecting = False
        self._utterance_final_tokens = []
        self._pending_finalize = False
        self._receive_generation += 1
        generation = self._receive_generation
        asyncio.create_task(
            self._receive_loop(generation),
            name=f"soniox_stt_receive_{generation}",
        )
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="soniox_stt_keepalive")
        logger.info(
            "[SONIOX-STT] Connected %s — model=%s hints=%s strict=%s fmt=pcm_s16le/%sHz",
            SONIOX_RT_WSS_URL,
            self.model,
            self.language_hints,
            self.language_hints_strict,
            self.SAMPLE_RATE,
        )

    async def _ensure_connected(self) -> None:
        if self._session and self._running:
            return
        if self._connecting:
            for _ in range(50):
                await asyncio.sleep(0.05)
                if self._session and self._running:
                    return
            return
        async with self._connect_lock:
            if self._session and self._running:
                return
            self._connecting = True
            try:
                await self.connect()
            except Exception:
                self._connecting = False
                raise

    async def _emit_final(self, text: str, tokens: list) -> None:
        text = _clean_transcript_text(text)
        if not text or len(text) < 2:
            return
        now = time.perf_counter()
        if text == self._last_emitted_final and (now - self._last_emitted_final_at) < 3.0:
            return
        self._last_emitted_final = text
        self._last_emitted_final_at = now
        self._last_partial_text = ""
        if text == self._last_queued_final:
            return
        self._last_queued_final = text
        logger.info("[SONIOX-STT] Final: '%s'", text)
        await self._final_queue.put({
            "text": text,
            "confidence": _token_confidence(tokens) if tokens else 1.0,
            "language_detected": _token_language(tokens, self.language_code),
        })

    async def _receive_loop(self, generation: int) -> None:
        if not self._session:
            return
        try:
            async for event in self._session.receive_events():
                self._events_seen += 1
                if event.error_code:
                    logger.error(
                        "[SONIOX-STT] Server error %s: %s",
                        event.error_code,
                        event.error_message,
                    )
                    break

                if not event.tokens:
                    continue

                final_new = [t for t in event.tokens if _is_final_token(t)]
                non_final_tokens = [t for t in event.tokens if not _is_final_token(t)]
                if final_new:
                    self._utterance_final_tokens.extend(final_new)

                display_text = _render_transcript(self._utterance_final_tokens, non_final_tokens)

                if self._events_seen <= 3 or (display_text and self._events_seen % 50 == 0):
                    logger.debug(
                        "[SONIOX-STT] Event #%s tokens=%s final_new=%s non_final=%s text='%s'",
                        self._events_seen,
                        len(event.tokens),
                        len(final_new),
                        len(non_final_tokens),
                        display_text[:80],
                    )

                if non_final_tokens and display_text:
                    self._last_queued_final = ""
                    self._last_emitted_final = ""
                    if display_text != self._last_partial_text:
                        if not self._first_partial_recorded and self._audio_send_times:
                            elapsed_ms = (time.perf_counter() - self._audio_send_times[0]) * 1000
                            try:
                                from re_voice_memory.metrics import record_stt_partial_latency
                                record_stt_partial_latency(elapsed_ms)
                            except Exception:
                                pass
                            self._first_partial_recorded = True
                        self._last_partial_text = display_text
                        await self._partial_queue.put({
                            "text": display_text,
                            "confidence": _token_confidence(event.tokens),
                            "is_partial": True,
                        })

                utterance_complete = bool(final_new and not non_final_tokens)
                if utterance_complete:
                    utterance_text = _render_transcript(self._utterance_final_tokens, [])
                    if utterance_text:
                        await self._emit_final(utterance_text, self._utterance_final_tokens)
                    self._utterance_final_tokens = []
                    self._pending_finalize = False
                    self._last_partial_text = ""

                if event.finished:
                    leftover = display_text or self._last_partial_text
                    if leftover and self._utterance_final_tokens:
                        await self._emit_final(leftover, self._utterance_final_tokens)
                    self._utterance_final_tokens = []
                    self._pending_finalize = False
                    break
        except Exception as e:
            logger.error("[SONIOX-STT] Receive loop error: %s", e)
        finally:
            if generation == self._receive_generation:
                self._running = False

    async def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        try:
            await self._ensure_connected()
            if not self._session or not self._running:
                return
            if not self._audio_send_times:
                self._audio_send_times.append(time.perf_counter())
            await self._session.send_byte_chunk(pcm_bytes)
        except Exception as e:
            logger.warning("[SONIOX-STT] Send error: %s — reconnecting", e)
            self._running = False
            async with self._connect_lock:
                try:
                    await self.connect()
                    if self._session and self._running:
                        await self._session.send_byte_chunk(pcm_bytes)
                except Exception as retry_exc:
                    logger.error("[SONIOX-STT] Reconnect failed: %s", retry_exc)
                    self._running = False

    async def finalize_utterance(self) -> None:
        """Ask Soniox to finalize pending tokens (end of user turn)."""
        self._pending_finalize = True
        if self._session and self._running:
            try:
                await self._session.finalize()
            except Exception as e:
                logger.debug("[SONIOX-STT] finalize failed: %s", e)

    async def partials(self):
        while not self._closed:
            try:
                yield await asyncio.wait_for(self._partial_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

    async def finals(self):
        while not self._closed:
            try:
                yield await asyncio.wait_for(self._final_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def close(self) -> None:
        self._closed = True
        await self._teardown_session()
        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None