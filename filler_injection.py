"""
Filler guard for LLM TTFT delay.

Plays a pre-baked filler audio frame when the LLM takes longer than
FILLER_THRESHOLD_MS to produce its first token, bridging the silence gap.
"""
import asyncio
import logging

import livekit.rtc as rtc

from greeting_cache import get_filler_pcm

logger = logging.getLogger("inbound-voice-agent")

FILLER_THRESHOLD_MS = 140
FILLER_TTFT_GATE_MS = 600


def _filler_threshold_ms(live_config: dict | None) -> int:
    """Gate fillers until TTFT exceeds gate — avoids firing on fast LLM."""
    cfg = live_config or {}
    if not cfg.get("filler_phrases_enabled", False):
        return 10_000
    try:
        return max(FILLER_THRESHOLD_MS, int(cfg.get("filler_ttft_gate_ms", FILLER_TTFT_GATE_MS)))
    except (TypeError, ValueError):
        return FILLER_TTFT_GATE_MS


async def _filler_guard(
    first_token_received: asyncio.Event,
    audio_source,
    language_code: str,
    sample_rate: int,
    filler_played_ref: list[bool],
    live_config: dict | None = None,
):
    await asyncio.sleep(_filler_threshold_ms(live_config) / 1000)
    if first_token_received.is_set():
        return
    pcm = get_filler_pcm(
        language_code,
        lang_preset=(live_config or {}).get("lang_preset"),
        live_config=live_config,
    )
    if pcm is None:
        return
    try:
        samples_per_channel = len(pcm) // 2  # 16-bit PCM → 2 bytes per sample
        frame = rtc.AudioFrame(
            data=pcm,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples_per_channel,
        )
        result = audio_source.capture_frame(frame)
        if asyncio.iscoroutine(result):
            await result
        filler_played_ref[0] = True
    except Exception:
        logger.exception("Failed to inject filler audio")


async def generate_with_filler_guard(
    llm_stream,
    audio_source,
    language_code: str,
    sample_rate: int = 24000,
    *,
    live_config: dict | None = None,
):
    first_token_received = asyncio.Event()
    filler_played_ref = [False]

    guard_task = asyncio.create_task(
        _filler_guard(
            first_token_received,
            audio_source,
            language_code,
            sample_rate,
            filler_played_ref,
            live_config,
        )
    )

    try:
        first = True
        async for token in llm_stream:
            if first:
                first_token_received.set()
                guard_task.cancel()
                try:
                    await guard_task
                except asyncio.CancelledError:
                    pass
                if filler_played_ref[0]:
                    await asyncio.sleep(0.05)
                first = False
            yield token
    finally:
        if not guard_task.done():
            guard_task.cancel()
            try:
                await guard_task
            except asyncio.CancelledError:
                pass
