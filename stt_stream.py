"""
Streaming STT via Sarvam Saaras v3 WebSocket API.

Sends 100ms PCM chunks as caller speaks.
Emits partial and final transcripts via async generators.
Never waits for full utterance — endpointing at 150ms silence.
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger("inbound-voice-agent")


class BufferAccumulator:
    """Accumulates resampled PCM bytes and yields fixed-size chunks."""

    def __init__(self, target_bytes: int):
        self._buffer = bytearray()
        self._target_bytes = target_bytes

    def feed(self, pcm_bytes: bytes) -> None:
        self._buffer.extend(pcm_bytes)

    def has_chunk(self) -> bool:
        return len(self._buffer) >= self._target_bytes

    def pop_chunk(self) -> bytes:
        chunk = bytes(self._buffer[:self._target_bytes])
        self._buffer = self._buffer[self._target_bytes:]
        return chunk


class SarvamSTTStream:
    """
    Streaming STT via Sarvam Saaras v3 WebSocket API.
    Sends 100ms PCM chunks as caller speaks.
    Emits partial and final transcripts via async generator.
    Never waits for full utterance — endpointing at 150ms silence.
    """

    SARVAM_WSS_URL = "wss://api.sarvam.ai/speech-to-text/stream"
    CHUNK_MS = 100
    SAMPLE_RATE = 16000
    CHANNELS = 1
    BYTES_PER_SAMPLE = 2
    CHUNK_BYTES = int(SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_MS / 1000)

    def __init__(
        self,
        api_key: str,
        language_code: str = "hi-en",
        model: str = "saaras:v3",
        endpointing_ms: int = 150,
        confidence_threshold: float = 0.85,
    ):
        self.api_key = api_key
        self.language_code = language_code
        self.model = model
        self.endpointing_ms = endpointing_ms
        self.confidence_threshold = confidence_threshold
        self._ws = None
        self._partial_queue: asyncio.Queue = asyncio.Queue()
        self._final_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._first_partial_recorded = False
        self._audio_send_times: list[float] = []

    async def connect(self) -> None:
        """Open WebSocket connection and start receive loop."""
        import websockets
        self._ws = await websockets.connect(
            self.SARVAM_WSS_URL,
            additional_headers={
                "api-subscription-key": self.api_key,
                "language_code": self.language_code,
                "model": self.model,
                "endpointing_ms": str(self.endpointing_ms),
            },
            ping_interval=20,
            ping_timeout=10,
        )
        self._running = True
        asyncio.create_task(self._receive_loop(), name="stt_receive")

    async def _receive_loop(self) -> None:
        """Background task: receive transcripts from Sarvam WebSocket."""
        try:
            async for message in self._ws:
                data = json.loads(message)
                transcript = data.get("transcript", "")
                is_final = data.get("is_final", False)
                confidence = data.get("confidence", 1.0)
                if is_final and transcript:
                    await self._final_queue.put({
                        "text": transcript,
                        "confidence": confidence,
                        "language_detected": data.get("language_code", self.language_code),
                    })
                elif transcript and confidence >= self.confidence_threshold:
                    if not self._first_partial_recorded and self._audio_send_times:
                        elapsed_ms = (time.perf_counter() - self._audio_send_times[0]) * 1000
                        try:
                            from re_voice_memory.metrics import record_stt_partial_latency
                            record_stt_partial_latency(elapsed_ms)
                        except Exception:
                            pass
                        self._first_partial_recorded = True
                    await self._partial_queue.put({
                        "text": transcript,
                        "confidence": confidence,
                        "is_partial": True,
                    })
        except Exception as e:
            logger.error(f"[STT-STREAM] Receive loop error: {e}")
        finally:
            self._running = False

    async def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Send 100ms PCM audio chunk to Sarvam WebSocket."""
        if self._ws and self._running:
            try:
                if not self._audio_send_times:
                    self._audio_send_times.append(time.perf_counter())
                await self._ws.send(pcm_bytes)
            except Exception as e:
                logger.warning(f"[STT-STREAM] Send error: {e}")

    async def partials(self):
        """Async generator: yield partial transcripts as they arrive."""
        while self._running or not self._partial_queue.empty():
            try:
                yield await asyncio.wait_for(self._partial_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

    async def finals(self):
        """Async generator: yield final transcripts."""
        while self._running or not self._final_queue.empty():
            try:
                yield await asyncio.wait_for(self._final_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

    async def close(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
