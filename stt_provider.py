"""
STT provider — Soniox realtime WebSocket only (LiveKit plugin).

Follows Soniox + LiveKit integration guide:
  - wss://stt-rt.soniox.com/transcribe-websocket
  - STT-based turn detection (AgentSession turn_handling.turn_detection="stt")
  - Reusable aiohttp.ClientSession (http_session) to avoid connection overhead
  - max_endpoint_delay_ms 200–3000 (default 200 — silence timeout / time-to-final)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from language_router import to_soniox_language_hints
from stt_soniox import normalize_soniox_realtime_model

logger = logging.getLogger("inbound-voice-agent")

SONIOX_RT_WSS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
DEFAULT_SONIOX_MODEL = "stt-rt-v5"
DEFAULT_STT_PROVIDER = "soniox"
DEFAULT_SILENCE_TIMEOUT_MS = 200
DEFAULT_MAX_ENDPOINT_DELAY_MS = DEFAULT_SILENCE_TIMEOUT_MS
DEFAULT_ENDPOINT_SENSITIVITY = 0.5


@dataclass
class StreamingSTTResult:
    stream: Any | None
    provider: str | None
    using_manual_turns: bool


def _resolve_api_key(call_cfg: Any, get_secret: Callable) -> str | None:
    key = getattr(call_cfg, "soniox_api_key", None)
    if key and str(key).strip():
        return str(key).strip()
    env_key = (os.environ.get("SONIOX_API_KEY") or "").strip()
    if env_key:
        return env_key
    secret = get_secret("SONIOX_API_KEY")
    return str(secret).strip() if secret else None


def _silence_timeout_ms(live_config: dict) -> int:
    """
    Silence timeout → Soniox max_endpoint_delay_ms (200–3000 ms).

    Priority:
      1. stt_silence_timeout_ms / stt_max_endpoint_delay_ms
      2. stt_max_endpointing_delay (seconds → ms)
      3. DEFAULT_SILENCE_TIMEOUT_MS (200)
    """
    for key in ("stt_silence_timeout_ms", "stt_max_endpoint_delay_ms"):
        raw = live_config.get(key)
        if raw is not None:
            try:
                return max(200, min(int(raw), 3000))
            except (TypeError, ValueError):
                pass

    delay_s = live_config.get("stt_max_endpointing_delay")
    if delay_s is not None:
        try:
            return max(200, min(int(float(delay_s) * 1000), 3000))
        except (TypeError, ValueError):
            pass

    return DEFAULT_SILENCE_TIMEOUT_MS


def _normalize_model(live_config: dict) -> str:
    return normalize_soniox_realtime_model(live_config.get("stt_model") or DEFAULT_SONIOX_MODEL)


def _language_hints(live_config: dict) -> list[str]:
    hints = live_config.get("stt_language_soniox")
    if hints:
        return list(hints)
    preset = (live_config.get("lang_preset") or "").strip().lower()
    if preset in ("hme", "hi-en-mr"):
        return ["hi", "mr", "en"]
    stt_language = live_config.get("stt_language", "unknown")
    return to_soniox_language_hints(stt_language)


def _vk_realty_context(live_config: dict | None = None):
    """Preset-aware domain vocabulary — full Hyderabad inventory + jargon."""
    from stt_context import build_soniox_context

    preset = (live_config or {}).get("lang_preset")
    ctx = build_soniox_context(preset)
    if ctx is None:
        logger.warning("[STT] livekit-plugins-soniox not installed — STT context disabled")
    return ctx


def build_soniox_stt(
    live_config: dict,
    call_cfg: Any,
    get_secret: Callable,
    *,
    http_session: Any | None = None,
):
    """
    Configured livekit.plugins.soniox.STT for production calls.

    Interim (non-final) + final tokens stream by default — use
    session.on("user_input_transcribed") for partial vs is_final.
    """
    from livekit.plugins.soniox import STT, STTOptions

    api_key = _resolve_api_key(call_cfg, get_secret)
    if not api_key and not (os.environ.get("SONIOX_API_KEY") or "").strip():
        raise ValueError(
            "SONIOX_API_KEY missing — set in .env or pass via call_cfg.soniox_api_key"
        )

    model = _normalize_model(live_config)
    language_hints = _language_hints(live_config)
    language_hints_strict = len(language_hints) == 1
    max_endpoint_delay_ms = _silence_timeout_ms(live_config)
    raw_sensitivity = live_config.get("stt_endpoint_sensitivity")
    if raw_sensitivity is not None and "v5" in model.lower():
        try:
            endpoint_sensitivity = max(-1.0, min(float(raw_sensitivity), 1.0))
        except (TypeError, ValueError):
            endpoint_sensitivity = DEFAULT_ENDPOINT_SENSITIVITY
    elif "v5" in model.lower():
        endpoint_sensitivity = DEFAULT_ENDPOINT_SENSITIVITY
    else:
        endpoint_sensitivity = None

    enable_lang_id = live_config.get("stt_enable_language_identification", True)
    if isinstance(enable_lang_id, str):
        enable_lang_id = enable_lang_id.strip().lower() in ("1", "true", "yes", "on")

    params = STTOptions(
        model=model,
        language_hints=language_hints,
        language_hints_strict=language_hints_strict,
        context=_vk_realty_context(live_config),
        sample_rate=16000,
        num_channels=1,
        enable_language_identification=bool(enable_lang_id),
        max_endpoint_delay_ms=max_endpoint_delay_ms,
        endpoint_sensitivity=endpoint_sensitivity,
    )

    # api_key=None → plugin reads SONIOX_API_KEY env (Soniox guide pattern).
    stt_api_key = api_key if api_key else None

    logger.info(
        "[STT] Soniox %s — url=%s hints=%s max_endpoint_delay_ms=%s "
        "sensitivity=%s http_session=%s interim=on",
        model,
        SONIOX_RT_WSS_URL,
        language_hints,
        max_endpoint_delay_ms,
        endpoint_sensitivity,
        "reused" if http_session is not None else "default",
    )

    return STT(
        api_key=stt_api_key,
        base_url=SONIOX_RT_WSS_URL,
        params=params,
        http_session=http_session,
    )