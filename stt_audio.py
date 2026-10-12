"""Utilities for capturing LiveKit caller audio and feeding STT (16kHz mono PCM)."""
from __future__ import annotations

import logging
import struct

import numpy as np

logger = logging.getLogger("inbound-voice-agent")

STT_SAMPLE_RATE = 16000
STT_CHANNELS = 1


def pcm_rms(pcm_bytes: bytes) -> float:
    """Root-mean-square level of int16 PCM (0.0 = silence)."""
    if not pcm_bytes:
        return 0.0
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))


def frame_to_pcm16_mono(frame, *, target_rate: int = STT_SAMPLE_RATE) -> bytes:
    """
    Convert a LiveKit AudioFrame to 16-bit mono PCM at target_rate Hz.

    Uses LiveKit Sox resampler when available; falls back to linear interp.
    """
    import livekit.rtc as rtc

    data = frame.data.tobytes() if hasattr(frame.data, "tobytes") else bytes(frame.data)
    samples = np.frombuffer(data, dtype=np.int16).copy()
    channels = int(getattr(frame, "num_channels", 1) or 1)
    if channels > 1:
        n = len(samples) // channels
        if n > 0:
            samples = samples[: n * channels].reshape(n, channels)[:, 0]
        else:
            samples = np.array([], dtype=np.int16)

    from_rate = int(getattr(frame, "sample_rate", target_rate) or target_rate)
    if from_rate == target_rate:
        return samples.tobytes()

    try:
        resampler = rtc.AudioResampler(
            input_rate=from_rate,
            output_rate=target_rate,
            num_channels=1,
            quality=rtc.AudioResamplerQuality.HIGH,
        )
        in_frame = rtc.AudioFrame(
            data=samples.tobytes(),
            sample_rate=from_rate,
            num_channels=1,
            samples_per_channel=len(samples),
        )
        out_frames = resampler.push(in_frame)
        out_frames.extend(resampler.flush())
        if out_frames:
            return b"".join(
                f.data.tobytes() if hasattr(f.data, "tobytes") else bytes(f.data)
                for f in out_frames
            )
    except Exception as exc:
        logger.debug("[STT-AUDIO] Sox resample failed (%s), using linear fallback", exc)

    if len(samples) == 0:
        return b""
    ratio = target_rate / from_rate
    target_len = max(1, int(len(samples) * ratio))
    indices = np.linspace(0, len(samples) - 1, target_len)
    resampled = np.interp(indices, np.arange(len(samples)), samples.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()