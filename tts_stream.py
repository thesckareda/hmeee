"""
Streaming TTS via Sarvam Bulbul v3 REST API.

Receives LLM tokens one at a time, buffers, splits on sentence boundaries,
and fires synthesis per chunk so first audio plays before LLM finishes.
"""
import asyncio
import base64
import logging
import re
import struct
import time

from language_router import DEFAULT_SARVAM_VOICE, normalize_sarvam_voice, to_sarvam_tts_lang

import httpx

logger = logging.getLogger("inbound-voice-agent")


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    """Extract raw PCM data from WAV bytes by parsing RIFF chunks.

    Args:
        wav_bytes: Complete WAV file bytes (24000 Hz 16-bit mono from Sarvam API).

    Returns:
        Raw PCM audio bytes (the data chunk contents).
    """
    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF":
        return wav_bytes

    pos = 12  # skip RIFF header
    while pos + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[pos : pos + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, pos + 4)[0]
        if chunk_id == b"data":
            data_start = pos + 8
            return wav_bytes[data_start : data_start + chunk_size]
        pos += 8 + chunk_size

    return wav_bytes


import regex

from indic_sentence_tokenizer import (
    MAX_CHUNK_GRAPHEMES,
    MIN_CHUNK_GRAPHEMES,
    grapheme_count,
)

# Strong sentence boundaries only — commas must NOT trigger early release.
SENTENCE_BOUNDARY = regex.compile(r"(?<=[.!?।])", regex.UNICODE)
_SENTENCE_END = (".", "!", "?", "।")

VOICE_MAP = {
    "hi": "shubh",
    "hi-en": "shubh",
    "ta": "kavitha",
    "te": "kavya",
    "bn": "priya",
    "mr": "shubh",
    "gu": "priya",
    "kn": "priya",
    "ml": "priya",
    "en": "ashutosh",
    "en-in": "ashutosh",
}

MIN_CHUNK_WORDS = MIN_CHUNK_GRAPHEMES  # legacy alias — grapheme count for Indic
MAX_CHUNK_CHARS = MAX_CHUNK_GRAPHEMES


def _ends_strong_sentence(text: str) -> bool:
    return (text or "").rstrip().endswith(_SENTENCE_END)


def is_speakable(text: str, *, end_of_stream: bool = False) -> bool:
    """Gate every Sarvam synth call — block micro-fragments and filler stubs."""
    from tts_text_transform import filter_tts_fragment, is_incomplete_speakable_clause

    stripped = (text or "").strip()
    if not stripped:
        return False
    if not filter_tts_fragment(stripped):
        return False
    if is_incomplete_speakable_clause(stripped):
        return False
    if grapheme_count(stripped) < MIN_CHUNK_GRAPHEMES:
        return False
    if _ends_strong_sentence(stripped):
        return True
    if end_of_stream:
        return grapheme_count(stripped) >= 40
    return False


class SarvamTTSStream:
    MIN_CHUNK_WORDS = MIN_CHUNK_WORDS
    MAX_CHUNK_CHARS = MAX_CHUNK_CHARS
    VOICE_MAP = VOICE_MAP
    SENTENCE_BOUNDARY = SENTENCE_BOUNDARY

    def __init__(
        self,
        api_key: str,
        language_code: str = "hi-en",
        speech_rate: float = 0.92,
        enable_ssml: bool = True,
        voice_override: str = "",
    ):
        self._api_key = api_key
        self._language_code = language_code
        self._speech_rate = speech_rate
        self._enable_ssml = enable_ssml
        self._voice_override = voice_override
        self._buffer = ""
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tts_tasks: set = set()
        self._flushed = False
        self._client: httpx.AsyncClient | None = None
        self._first_chunk_recorded = False
        self._chunk_start_times: dict[int, float] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=3.0)
        return self._client

    def _apply_ssml(self, text: str) -> str:
        if not self._enable_ssml:
            return text
        text = re.sub(r"(₹\s*\d[\d,]*)", r'<break time="200ms"/>\1', text)
        if text.rstrip().endswith("?"):
            text = f'<prosody pitch="+5%">{text}</prosody>'
        return text

    def _drain_ready_chunks(self) -> list[str]:
        """Emit only strong sentence-complete, speakable chunks; keep the rest."""
        if not self._buffer:
            return []

        ready: list[str] = []
        consumed = 0
        for match in regex.finditer(r"[^.!?।]*[.!?।]", self._buffer, regex.UNICODE):
            chunk = match.group(0).strip()
            if not chunk or not is_speakable(chunk):
                continue
            ready.append(chunk)
            consumed = match.end()

        if consumed:
            self._buffer = self._buffer[consumed:].lstrip()
        return ready

    def _schedule_synthesis(self, text: str, *, end_of_stream: bool = False) -> None:
        if not is_speakable(text, end_of_stream=end_of_stream):
            logger.warning("[TTS-STREAM] Held back non-speakable chunk: %r", (text or "")[:80])
            return
        processed = self._apply_ssml(text)
        task = asyncio.create_task(self._synthesize(processed), name="tts_chunk")
        self._tts_tasks.add(task)
        task.add_done_callback(self._tts_tasks.discard)

    def push_token(self, token: str) -> None:
        self._buffer += token
        for sentence in self._drain_ready_chunks():
            self._schedule_synthesis(sentence)

    async def flush(self) -> None:
        remainder = self._buffer.strip()
        if remainder and is_speakable(remainder, end_of_stream=True):
            self._schedule_synthesis(remainder, end_of_stream=True)
            self._buffer = ""
        elif remainder:
            logger.warning(
                "[TTS-STREAM] Flush held remainder (not speakable): %r",
                remainder[:80],
            )
        self._flushed = True

    async def _synthesize(self, text: str) -> None:
        if not is_speakable(text, end_of_stream=True):
            logger.warning("[TTS-STREAM] Skipped synth for non-speakable: %r", (text or "")[:80])
            return
        chunk_id = id(text)
        self._chunk_start_times[chunk_id] = time.perf_counter()
        try:
            client = await self._get_client()
            speaker = normalize_sarvam_voice(
                self._voice_override or self.VOICE_MAP.get(self._language_code, DEFAULT_SARVAM_VOICE)
            )
            sarvam_lang = to_sarvam_tts_lang(self._language_code)
            body = {
                "inputs": [text],
                "target_language_code": sarvam_lang,
                "speaker": speaker,
                "speech_sample_rate": 24000,
                "enable_preprocessing": True,
                "model": "bulbul:v3",
            }
            if self._speech_rate:
                body["speech_rate"] = self._speech_rate

            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": self._api_key},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            for audio_b64 in data.get("audios", []):
                audio_bytes = base64.b64decode(audio_b64)
                if not self._first_chunk_recorded and chunk_id in self._chunk_start_times:
                    elapsed_ms = (time.perf_counter() - self._chunk_start_times[chunk_id]) * 1000
                    try:
                        from re_voice_memory.metrics import record_tts_first_chunk
                        record_tts_first_chunk(elapsed_ms)
                    except Exception:
                        pass
                    self._first_chunk_recorded = True
                await self._queue.put(audio_bytes)
        except Exception as e:
            logger.error(f"[TTS-STREAM] Synthesis failed: {e}")
        finally:
            self._chunk_start_times.pop(chunk_id, None)

    async def _synthesise_raw(self, text: str) -> bytes | None:
        try:
            client = await self._get_client()
            speaker = normalize_sarvam_voice(
                self._voice_override or self.VOICE_MAP.get(self._language_code, DEFAULT_SARVAM_VOICE)
            )
            sarvam_lang = to_sarvam_tts_lang(self._language_code)
            body = {
                "inputs": [text],
                "target_language_code": sarvam_lang,
                "speaker": speaker,
                "speech_sample_rate": 24000,
                "enable_preprocessing": True,
                "model": "bulbul:v3",
            }
            if self._speech_rate:
                body["speech_rate"] = self._speech_rate

            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": self._api_key},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            audios = data.get("audios", [])
            if not audios:
                return None
            return base64.b64decode(audios[0])
        except Exception as e:
            logger.error(f"[TTS-STREAM] Raw synthesis failed: {e}")
            return None

    async def audio_chunks(self):
        while True:
            try:
                audio_bytes = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1
                )
                yield audio_bytes
            except asyncio.TimeoutError:
                if not self._tts_tasks and self._flushed:
                    break

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


async def synthesize_sarvam_audio_frames(
    text: str,
    *,
    api_key: str,
    language_code: str,
    voice: str = "",
    speech_rate: float = 0.92,
    sample_rate: int = 24000,
    frame_ms: int = 100,
):
    """
    Sarvam REST → WAV → PCM → LiveKit AudioFrames.

    Bypasses the LiveKit Sarvam plugin which mislabels WAV as audio/mp3 and
    triggers avcodec InvalidDataError.
    """
    import livekit.rtc as rtc

    if not is_speakable(text, end_of_stream=True):
        logger.warning("[TTS-STREAM] Skipped PCM synth for non-speakable: %r", (text or "")[:80])
        return

    stream = SarvamTTSStream(
        api_key=api_key,
        language_code=language_code,
        voice_override=voice,
        speech_rate=speech_rate,
    )
    try:
        wav = await stream._synthesise_raw(text)
    finally:
        await stream.close()

    if not wav:
        return

    pcm = wav_to_pcm(wav)
    chunk_bytes = max(2, int(sample_rate * frame_ms / 1000) * 2)
    for offset in range(0, len(pcm), chunk_bytes):
        chunk = pcm[offset : offset + chunk_bytes]
        if len(chunk) < 2:
            continue
        samples = len(chunk) // 2
        yield rtc.AudioFrame(
            data=chunk,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples,
        )
