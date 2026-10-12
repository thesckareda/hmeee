"""
Gemini 3.1 Flash TTS — LiveKit plugin with chunked streaming PCM.

Streaming semantics (Gemini 3.1 Flash TTS)
------------------------------------------
Gemini 3.1 Flash TTS **does support streaming**, via:

  - ``google.genai.Client.models.generate_content_stream`` (SDK), or
  - Interactions API with ``"stream": true`` (SSE)

That is **chunked low-latency streaming**, not true real-time conversational
streaming at sub-sentence / phoneme granularity. The API emits audio as
**completed generation chunks** (per request segment); within each chunk, PCM
arrives incrementally over the HTTP/SSE or SDK stream.

Implication for LiveKit Agents
------------------------------
- If the TTS plugin exposes a working ``.stream()`` with
  ``TTSCapabilities(streaming=True)``, AgentSession can drive TTS directly —
  **no external StreamAdapter wrapper is required**.
- If ``.stream()`` is missing or ``streaming=False``, LiveKit raises
  ``NotImplementedError`` on the generate_reply path and you must wrap the
  plugin with ``tts.StreamAdapter`` (sentence tokenizer → repeated
  ``synthesize()`` per chunk).

This module implements native ``.stream()``: LLM tokens are sentence-batched,
each sentence triggers one Gemini generation whose PCM is streamed back in
20 ms frames. Latency is bounded by sentence length + first-chunk TTFB, not
by token-level duplex audio.

Transport: SDK ``generate_content_stream`` first; Interactions SSE fallback.
Provider switch: ``tts_provider = "gemini" | "sarvam"`` in config.json.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import aiohttp

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
    utils,
)

logger = logging.getLogger("inbound-voice-agent")

# Set False to disable multi-key / SSE / Sarvam TTS fallbacks (Gemini voice key only).
TTS_FALLBACKS_ENABLED = True


def tts_fallbacks_enabled() -> bool:
    return TTS_FALLBACKS_ENABLED


DEFAULT_GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_GEMINI_VOICE = "Rasalgethi"
SAMPLE_RATE = 24000
NUM_CHANNELS = 1
FRAME_MS = 20
FRAME_BYTES = int(SAMPLE_RATE * 2 * FRAME_MS / 1000 * NUM_CHANNELS)

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
_STREAMING_MODELS = frozenset({
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
})

# All 30 Gemini TTS voice_name values (official preview voices).
GEMINI_VOICE_OPTIONS: list[dict[str, str]] = [
    {"id": "Zephyr", "style": "Bright"},
    {"id": "Puck", "style": "Upbeat"},
    {"id": "Charon", "style": "Informative"},
    {"id": "Kore", "style": "Firm"},
    {"id": "Fenrir", "style": "Excitable"},
    {"id": "Leda", "style": "Youthful"},
    {"id": "Orus", "style": "Firm"},
    {"id": "Aoede", "style": "Breezy"},
    {"id": "Callirrhoe", "style": "Easy-going"},
    {"id": "Autonoe", "style": "Bright"},
    {"id": "Enceladus", "style": "Breathy"},
    {"id": "Iapetus", "style": "Clear"},
    {"id": "Umbriel", "style": "Easy-going"},
    {"id": "Algieba", "style": "Smooth"},
    {"id": "Despina", "style": "Smooth"},
    {"id": "Erinome", "style": "Clear"},
    {"id": "Algenib", "style": "Gravelly"},
    {"id": "Rasalgethi", "style": "Informative"},
    {"id": "Laomedeia", "style": "Upbeat"},
    {"id": "Achernar", "style": "Soft"},
    {"id": "Alnilam", "style": "Firm"},
    {"id": "Schedar", "style": "Even"},
    {"id": "Gacrux", "style": "Mature"},
    {"id": "Pulcherrima", "style": "Forward"},
    {"id": "Achird", "style": "Friendly"},
    {"id": "Zubenelgenubi", "style": "Casual"},
    {"id": "Vindemiatrix", "style": "Gentle"},
    {"id": "Sadachbia", "style": "Lively"},
    {"id": "Sadaltager", "style": "Knowledgeable"},
    {"id": "Sulafat", "style": "Warm"},
]

GEMINI_VOICES = frozenset(v["id"] for v in GEMINI_VOICE_OPTIONS)


def gemini_voice_catalog_entries() -> list[dict[str, str]]:
    """Dashboard voice picker entries for the Google/Gemini tab."""
    return [
        {
            "id": v["id"],
            "name": v["id"],
            "gender": "neutral",
            "quality": v["style"],
            "provider": "gemini",
        }
        for v in GEMINI_VOICE_OPTIONS
    ]

_http_session: aiohttp.ClientSession | None = None
_http_session_lock = asyncio.Lock()


async def _shared_http_session() -> aiohttp.ClientSession:
    global _http_session
    async with _http_session_lock:
        if _http_session is None or _http_session.closed:
            _http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45, connect=5),
                connector=aiohttp.TCPConnector(limit=8, ttl_dns_cache=300),
            )
        return _http_session


async def close_shared_http_session() -> None:
    global _http_session
    async with _http_session_lock:
        if _http_session and not _http_session.closed:
            await _http_session.close()
        _http_session = None


_PLACEHOLDER_API_KEYS = frozenset({
    "",
    "YOUR_GEMINI_API_KEY",
    "your-gemini-key",
    "your-google-api-key",
})


def is_usable_gemini_api_key(key: str | None) -> bool:
    """Reject empty strings and template placeholders from config.json."""
    k = (key or "").strip()
    if not k or k in _PLACEHOLDER_API_KEYS:
        return False
    if k.startswith("YOUR_") or k.startswith("your-"):
        return False
    return True


def resolve_gemini_api_key(explicit: str | None = None) -> str:
    if is_usable_gemini_api_key(explicit):
        return str(explicit).strip()
    key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if is_usable_gemini_api_key(key):
        return key.strip()
    raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required for Gemini TTS")


def resolve_gemini_tts_api_keys_for_call(
    live_config: dict | None = None,
    get_secret: Any = None,
) -> list[str]:
    """Ordered TTS keys: GOOGLE (voice) first; GEMINI second when fallbacks enabled."""
    keys: list[str] = []
    candidates: list[str | None] = []
    if live_config:
        candidates.append(live_config.get("google_api_key"))
        if tts_fallbacks_enabled():
            candidates.append(live_config.get("gemini_api_key"))
    if get_secret is not None:
        candidates.append(get_secret("GOOGLE_API_KEY"))
        if tts_fallbacks_enabled():
            candidates.append(get_secret("GEMINI_API_KEY"))
    for candidate in candidates:
        if not is_usable_gemini_api_key(candidate):
            continue
        key = str(candidate).strip()
        if key not in keys:
            keys.append(key)
    if keys:
        return keys
    return [resolve_gemini_api_key()]


def resolve_gemini_api_key_for_call(
    live_config: dict | None = None,
    get_secret: Any = None,
) -> str:
    """Primary TTS key (first in the fallback chain)."""
    keys = resolve_gemini_tts_api_keys_for_call(live_config, get_secret)
    if not keys:
        raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required for Gemini TTS")
    return keys[0]


class GeminiTTSRateLimitError(APIConnectionError):
    """Gemini TTS quota / per-minute rate limit exceeded."""


# Free tier: 10 generate requests/min per model — stay under with spacing + shared limiter.
GEMINI_TTS_REQUESTS_PER_MINUTE = 9
GEMINI_TTS_MIN_INTERVAL_SEC = 60.0 / GEMINI_TTS_REQUESTS_PER_MINUTE

_rate_lock = asyncio.Lock()
_last_gemini_tts_request = 0.0
_gemini_tts_cooldown_until = 0.0


def is_gemini_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("429", "resource_exhausted", "quota exceeded", "rate limit", "rate-limit")
    )


def is_gemini_api_blocked_error(exc: BaseException) -> bool:
    """API key cannot call generativelanguage.googleapis.com (403 / API_KEY_SERVICE_BLOCKED)."""
    msg = str(exc).lower()
    return (
        "api_key_service_blocked" in msg
        or ("permission_denied" in msg and "blocked" in msg)
        or ("403" in msg and "generativelanguage" in msg)
    )


def is_gemini_tts_in_cooldown() -> bool:
    """True when a recent 429 means live calls should skip Gemini immediately."""
    return time.monotonic() < _gemini_tts_cooldown_until


def mark_gemini_tts_rate_limited(*, retry_after_sec: float = 45.0) -> None:
    """After 429, pause live Gemini TTS so Sarvam fallback kicks in without delay."""
    global _gemini_tts_cooldown_until
    _gemini_tts_cooldown_until = time.monotonic() + max(5.0, retry_after_sec)
    logger.warning(
        "[GEMINI-TTS] Rate limit cooldown %.0fs — live calls will use Sarvam fallback",
        retry_after_sec,
    )


async def acquire_gemini_tts_slot() -> None:
    """Throttle prewarm-only Gemini calls (never used on live speech)."""
    global _last_gemini_tts_request
    async with _rate_lock:
        now = time.monotonic()
        wait = GEMINI_TTS_MIN_INTERVAL_SEC - (now - _last_gemini_tts_request)
        if wait > 0:
            logger.debug("[GEMINI-TTS] Prewarm rate limiter waiting %.1fs", wait)
            await asyncio.sleep(wait)
        _last_gemini_tts_request = time.monotonic()


def normalize_gemini_voice(voice: str | None, *, fallback: str = DEFAULT_GEMINI_VOICE) -> str:
    raw = (voice or "").strip()
    if not raw:
        return fallback
    for v in GEMINI_VOICES:
        if v.lower() == raw.lower():
            return v
    logger.warning("[GEMINI-TTS] Unknown voice '%s' — using %s", voice, fallback)
    return fallback


def _chunk_pcm(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[bytes]:
    if not pcm:
        return []
    return [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes) if pcm[i : i + frame_bytes]]


def _extract_audio_b64(obj: dict[str, Any]) -> str | None:
    if not isinstance(obj, dict):
        return None
    delta = obj.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "audio":
        data = delta.get("data")
        if isinstance(data, str) and data:
            return data
    steps = obj.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            for item in step.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "audio":
                    data = item.get("data")
                    if isinstance(data, str) and data:
                        return data
    output_audio = obj.get("output_audio")
    if isinstance(output_audio, dict):
        data = output_audio.get("data")
        if isinstance(data, str) and data:
            return data
    candidates = obj.get("candidates")
    if isinstance(candidates, list):
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            content = cand.get("content") or {}
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                data = inline.get("data")
                if isinstance(data, str) and data:
                    return data
    return None


def _decode_inline_audio(data: Any) -> bytes | None:
    if data is None:
        return None
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        try:
            return base64.b64decode(data)
        except Exception:
            return None
    return None


def _iter_sdk_stream(
    text: str,
    *,
    api_key: str,
    model: str,
    voice: str,
) -> Iterator[bytes]:
    from google import genai
    from google.genai import types

    from gemini_client_config import gemini_afc_disabled

    client = genai.Client(api_key=api_key)
    stream = client.models.generate_content_stream(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice,
                    )
                )
            ),
            automatic_function_calling=gemini_afc_disabled(),
        ),
    )
    for chunk in stream:
        try:
            for part in chunk.candidates[0].content.parts:
                inline = getattr(part, "inline_data", None)
                if inline is None:
                    continue
                pcm = _decode_inline_audio(inline.data)
                if pcm:
                    yield pcm
        except (IndexError, AttributeError, TypeError):
            continue


async def _async_sdk_pcm_chunks(
    text: str,
    *,
    api_key: str,
    model: str,
    voice: str,
) -> AsyncIterator[bytes]:
    """Bridge sync SDK stream → async — yield 20ms chunks as they arrive."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            for pcm in _iter_sdk_stream(text, api_key=api_key, model=model, voice=voice):
                for chunk in _chunk_pcm(pcm):
                    future = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
                    future.result(timeout=30)
        except BaseException as exc:
            errors.append(exc)
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    threading.Thread(target=_worker, daemon=True, name="gemini_tts_sdk").start()

    while True:
        item = await queue.get()
        if item is None:
            if errors:
                raise errors[0]
            break
        yield item


async def _iter_interactions_sse(
    text: str,
    *,
    api_key: str,
    model: str,
    voice: str,
    session: aiohttp.ClientSession,
    timeout: aiohttp.ClientTimeout,
) -> AsyncIterator[bytes]:
    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
        "stream": True,
    }

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    async with session.post(INTERACTIONS_URL, json=payload, headers=headers, timeout=timeout) as res:
        if res.status >= 400:
            body = await res.text()
            raise APIStatusError(
                f"Gemini TTS interactions error {res.status}: {body[:300]}",
                status_code=res.status,
                request_id="",
                body=body,
            )
        ctype = (res.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in ctype:
            body = await res.json()
            b64 = _extract_audio_b64(body)
            if not b64:
                raise APIConnectionError("Gemini TTS returned no audio payload")
            for chunk in _chunk_pcm(base64.b64decode(b64)):
                yield chunk
            return

        buffer = ""
        async for raw in res.content.iter_chunked(1024):
            buffer += raw.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data_line = line[5:].strip()
                if not data_line or data_line == "[DONE]":
                    continue
                try:
                    obj = json.loads(data_line)
                except json.JSONDecodeError:
                    continue
                b64 = _extract_audio_b64(obj)
                if b64:
                    for chunk in _chunk_pcm(base64.b64decode(b64)):
                        yield chunk


async def iter_gemini_pcm_frames_with_keys(
    text: str,
    *,
    api_keys: list[str],
    model: str = DEFAULT_GEMINI_TTS_MODEL,
    voice: str = DEFAULT_GEMINI_VOICE,
    conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    rate_limit: bool = False,
) -> AsyncIterator[Any]:
    """Try each API key in order — voice key first, conversation key as fallback."""
    keys = [k for k in api_keys if is_usable_gemini_api_key(k)]
    if not keys:
        raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY required for Gemini TTS")

    if tts_fallbacks_enabled() and is_gemini_tts_in_cooldown():
        raise GeminiTTSRateLimitError("Gemini TTS in cooldown after recent 429")

    if not tts_fallbacks_enabled():
        keys = keys[:1]

    errors: list[BaseException] = []
    for i, key in enumerate(keys):
        got_audio = False
        try:
            async for frame in iter_gemini_pcm_frames(
                text,
                api_key=key,
                model=model,
                voice=voice,
                conn_options=conn_options,
                rate_limit=rate_limit,
                check_cooldown=False,
            ):
                got_audio = True
                yield frame
            if got_audio:
                if i > 0:
                    logger.info("[GEMINI-TTS] Succeeded with fallback key #%d", i + 1)
                return
        except Exception as exc:
            errors.append(exc)
            can_retry = tts_fallbacks_enabled() and i < len(keys) - 1 and (
                is_gemini_api_blocked_error(exc) or is_gemini_rate_limit_error(exc)
            )
            if can_retry:
                logger.warning(
                    "[GEMINI-TTS] Key #%d failed (%s) — trying next key",
                    i + 1,
                    type(exc).__name__,
                )
                continue
            if is_gemini_rate_limit_error(exc):
                mark_gemini_tts_rate_limited()
                raise GeminiTTSRateLimitError(f"Gemini TTS rate limited: {exc}") from exc
            raise

    if errors and all(is_gemini_rate_limit_error(e) for e in errors):
        mark_gemini_tts_rate_limited()
        raise GeminiTTSRateLimitError(f"Gemini TTS rate limited: {errors[-1]}") from errors[-1]
    if errors:
        raise errors[-1]
    raise APIConnectionError(f"Gemini TTS produced no audio for: {(text or '')[:80]!r}")


async def iter_gemini_pcm_frames(
    text: str,
    *,
    api_key: str,
    model: str = DEFAULT_GEMINI_TTS_MODEL,
    voice: str = DEFAULT_GEMINI_VOICE,
    conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    rate_limit: bool = False,
    check_cooldown: bool = True,
) -> AsyncIterator[Any]:
    """Async generator of 20ms LiveKit frames — SDK stream first for lowest latency."""
    import livekit.rtc as rtc

    stripped = (text or "").strip()
    if not stripped:
        return

    if check_cooldown and tts_fallbacks_enabled() and is_gemini_tts_in_cooldown():
        raise GeminiTTSRateLimitError("Gemini TTS in cooldown after recent 429")

    t0 = time.perf_counter()
    first_frame_logged = False
    got_audio = False

    def _yield_frame(pcm_chunk: bytes):
        nonlocal got_audio, first_frame_logged
        got_audio = True
        if not first_frame_logged:
            logger.info(
                "[GEMINI-TTS] First audio frame in %.0fms (%d chars)",
                (time.perf_counter() - t0) * 1000,
                len(stripped),
            )
            first_frame_logged = True
        samples = len(pcm_chunk) // 2
        return rtc.AudioFrame(
            data=pcm_chunk,
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            samples_per_channel=samples,
        )

    if rate_limit:
        await acquire_gemini_tts_slot()
    try:
        async for pcm_chunk in _async_sdk_pcm_chunks(
            stripped, api_key=api_key, model=model, voice=voice
        ):
            yield _yield_frame(pcm_chunk)
        if got_audio:
            logger.info(
                "[GEMINI-TTS] SDK stream done in %.0fms",
                (time.perf_counter() - t0) * 1000,
            )
            return
    except Exception as exc:
        if is_gemini_rate_limit_error(exc):
            raise GeminiTTSRateLimitError(f"Gemini TTS rate limited: {exc}") from exc
        if is_gemini_api_blocked_error(exc):
            raise APIConnectionError(f"Gemini TTS API key blocked: {exc}") from exc
        if not tts_fallbacks_enabled():
            raise APIConnectionError(f"Gemini TTS SDK failed: {exc}") from exc
        logger.warning("[GEMINI-TTS] SDK stream failed (%s) — trying Interactions SSE", exc)

    if not got_audio and not tts_fallbacks_enabled():
        raise APIConnectionError(f"Gemini TTS SDK produced no audio for: {stripped[:80]!r}")

    if tts_fallbacks_enabled():
        try:
            if rate_limit:
                await acquire_gemini_tts_slot()
            session = await _shared_http_session()
            timeout = aiohttp.ClientTimeout(total=conn_options.timeout)
            async for pcm_chunk in _iter_interactions_sse(
                stripped,
                api_key=api_key,
                model=model,
                voice=voice,
                session=session,
                timeout=timeout,
            ):
                yield _yield_frame(pcm_chunk)
        except Exception as exc:
            if is_gemini_rate_limit_error(exc):
                raise GeminiTTSRateLimitError(f"Gemini TTS rate limited: {exc}") from exc
            if is_gemini_api_blocked_error(exc):
                raise APIConnectionError(f"Gemini TTS API key blocked: {exc}") from exc
            raise APIConnectionError(f"Gemini TTS failed: {exc}") from exc

        if not got_audio:
            raise APIConnectionError(f"Gemini TTS produced no audio for: {stripped[:80]!r}")

        logger.info(
            "[GEMINI-TTS] SSE stream done in %.0fms",
            (time.perf_counter() - t0) * 1000,
        )


@dataclass
class _GeminiTTSOptions:
    model: str
    voice: str
    api_key: str
    api_keys: list[str]


def _sentence_tokenizer():
    try:
        from indic_sentence_tokenizer import IndicSentenceTokenizer

        return IndicSentenceTokenizer(retain_format=True)
    except ImportError:
        from livekit.agents import tokenize

        return tokenize.basic.SentenceTokenizer(retain_format=True)


class TTS(tts.TTS):
    """LiveKit TTS plugin for Gemini Flash TTS (native chunked streaming)."""

    def __init__(
        self,
        *,
        voice_name: str = DEFAULT_GEMINI_VOICE,
        model: str = DEFAULT_GEMINI_TTS_MODEL,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        keys: list[str] = []
        for candidate in (api_keys or []):
            if is_usable_gemini_api_key(candidate) and candidate not in keys:
                keys.append(str(candidate).strip())
        if is_usable_gemini_api_key(api_key) and str(api_key).strip() not in keys:
            keys.insert(0, str(api_key).strip())
        if not keys:
            keys = [resolve_gemini_api_key(api_key)]
        self._opts = _GeminiTTSOptions(
            model=model,
            voice=normalize_gemini_voice(voice_name),
            api_key=keys[0],
            api_keys=keys,
        )
        self._sentence_tokenizer = _sentence_tokenizer()

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "gemini"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "GeminiSynthesizeStream":
        return GeminiSynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        await close_shared_http_session()


class ChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            frame_size_ms=FRAME_MS,
            stream=False,
        )
        opts = self._tts._opts
        async for frame in iter_gemini_pcm_frames_with_keys(
            self._input_text,
            api_keys=opts.api_keys,
            model=opts.model,
            voice=opts.voice,
            conn_options=self._conn_options,
        ):
            output_emitter.push(frame.data.tobytes())
        output_emitter.flush()


class GeminiSynthesizeStream(tts.SynthesizeStream):
    """Chunked streaming: one Gemini generation per sentence, PCM streamed per chunk."""

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts: TTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        sent_stream = self._tts._sentence_tokenizer.stream()
        request_id = utils.shortuuid()
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts.sample_rate,
            num_channels=self._tts.num_channels,
            mime_type="audio/pcm",
            frame_size_ms=FRAME_MS,
            stream=True,
        )
        segment_id = utils.shortuuid()
        output_emitter.start_segment(segment_id=segment_id)
        opts = self._tts._opts

        async def _forward_input() -> None:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    sent_stream.flush()
                    continue
                sent_stream.push_text(data)
            sent_stream.end_input()

        async def _synthesize_segments() -> None:
            async for ev in sent_stream:
                text = (ev.token or "").strip()
                if not text:
                    continue
                async for frame in iter_gemini_pcm_frames_with_keys(
                    text,
                    api_keys=opts.api_keys,
                    model=opts.model,
                    voice=opts.voice,
                    conn_options=self._conn_options,
                ):
                    output_emitter.push(frame.data.tobytes())
                output_emitter.flush()

        tasks = [
            asyncio.create_task(_forward_input()),
            asyncio.create_task(_synthesize_segments()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            await sent_stream.aclose()
            await utils.aio.cancel_and_wait(*tasks)