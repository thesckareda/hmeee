"""
One-time worker startup tasks: greeting cache, intent classifier, metrics.

Prewarm follows the active TTS provider (gemini / sarvam / cartesia) and lang
preset (hme / tinglish). Re-runs when preset or voice changes so greeting,
filler, and session-line audio matches live calls.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger("inbound-voice-agent")

_prewarm_done = False
_last_prewarm_preset: str | None = None
_last_prewarm_signature: str | None = None
_prewarm_lock = asyncio.Lock()


def _prewarm_signature(
    preset: str,
    *,
    voice_per_lang: dict[str, str],
    tts_voice: str,
    tts_provider: str,
    tts_model: str,
) -> str:
    import json

    payload = {
        "preset": preset,
        "tts_provider": tts_provider,
        "tts_voice": tts_voice,
        "model": tts_model,
        "voices": {k: voice_per_lang[k] for k in sorted(voice_per_lang)},
    }
    return json.dumps(payload, sort_keys=True)


def _prewarm_api_key(env_var: str, cfg: dict | None = None, cfg_key: str | None = None) -> str:
    """Resolve API keys without requiring the full secrets bundle."""
    cfg = cfg or {}
    env_val = (os.environ.get(env_var) or "").strip()
    if env_val:
        return env_val
    if cfg_key:
        cfg_val = str(cfg.get(cfg_key) or "").strip()
        if cfg_val:
            return cfg_val
    try:
        from secrets_manager import get_secret

        secret_val = get_secret(env_var)
        if secret_val and str(secret_val).strip():
            return str(secret_val).strip()
    except Exception:
        pass
    return ""


def invalidate_worker_prewarm() -> None:
    """Force the next prewarm to rebuild caches (e.g. after preset/voice change)."""
    global _prewarm_done, _last_prewarm_preset, _last_prewarm_signature

    from greeting_cache import clear_prewarm_caches

    _prewarm_done = False
    _last_prewarm_preset = None
    _last_prewarm_signature = None
    clear_prewarm_caches()


async def run_worker_prewarm() -> None:
    """Idempotent worker prewarm — re-runs when lang preset or TTS voice changes."""
    global _prewarm_done, _last_prewarm_preset, _last_prewarm_signature

    async with _prewarm_lock:
        from config_store import read_full_config, read_raw_config
        from gemini_tts import DEFAULT_GEMINI_TTS_MODEL, DEFAULT_GEMINI_VOICE, normalize_gemini_voice
        from language_router import get_default_turn_language, resolve_lang_preset
        from tts_provider import resolve_tts_provider

        try:
            raw_cfg = read_raw_config()
            cfg = {**read_full_config(), **raw_cfg}
        except Exception:
            raw_cfg = {}
            cfg = {}

        lang_preset_raw = str(cfg.get("lang_preset", "tinglish")).strip().lower()
        preset = resolve_lang_preset(lang_preset_raw, cfg)
        tts_provider = resolve_tts_provider(cfg)

        tts_voice = str(cfg.get("tts_voice", DEFAULT_GEMINI_VOICE)).strip()
        cartesia_model = str(cfg.get("cartesia_tts_model") or "sonic-3.5").strip()
        gemini_model = str(cfg.get("gemini_tts_model") or DEFAULT_GEMINI_TTS_MODEL).strip()
        tts_model = cartesia_model if tts_provider == "cartesia" else gemini_model

        voice_per_lang: dict[str, str] = {}
        api_key = ""

        if tts_provider == "gemini":
            tts_voice = normalize_gemini_voice(cfg.get("tts_voice", DEFAULT_GEMINI_VOICE))
            api_key = _prewarm_api_key("GOOGLE_API_KEY", raw_cfg, "google_api_key")
            if not api_key:
                api_key = _prewarm_api_key("GEMINI_API_KEY", raw_cfg, "gemini_api_key")
        elif tts_provider == "sarvam":
            from language_router import normalize_sarvam_voice

            tts_voice = normalize_sarvam_voice(cfg.get("tts_voice", "ashutosh"))
            api_key = _prewarm_api_key("SARVAM_API_KEY", raw_cfg, "sarvam_api_key")
        else:
            from cartesia_tts import cartesia_prebake_voice_map

            voice_per_lang = cartesia_prebake_voice_map(preset)
            api_key = _prewarm_api_key("CARTESIA_API_KEY", raw_cfg, "cartesia_api_key")

        signature = _prewarm_signature(
            preset,
            voice_per_lang=voice_per_lang,
            tts_voice=tts_voice,
            tts_provider=tts_provider,
            tts_model=tts_model,
        )

        if _prewarm_done and _last_prewarm_signature == signature:
            return

        if _last_prewarm_signature is not None and _last_prewarm_signature != signature:
            from greeting_cache import clear_prewarm_caches

            clear_prewarm_caches()
            logger.info(
                "[PREWARM] Voice/preset signature changed — rebuilding cache (preset=%s provider=%s)",
                preset,
                tts_provider,
            )

        from knowledge_pack import preload_knowledge_packs
        from re_voice_memory.metrics import maybe_start_metrics_server

        preload_knowledge_packs()
        maybe_start_metrics_server()

        # IQ plan 0.4 — registry loads once at prewarm so Wave 6 never pays cold-start
        try:
            from iq_registry import all_primitives

            all_primitives()
        except Exception:
            pass

        first_line = str(cfg.get("first_line") or "").strip()
        default_lang = get_default_turn_language(preset, cfg)

        extra_greetings = None
        if first_line:
            extra_greetings = {default_lang: first_line}

        from greeting_cache import (
            languages_for_preset,
            prebake_all_greetings,
            session_lines_from_config,
        )

        active_langs = languages_for_preset(preset)
        session_lines = session_lines_from_config(raw_cfg or cfg)

        try:
            await prebake_all_greetings(
                api_key,
                voice_override=tts_voice,
                provider=tts_provider,
                model=tts_model,
                extra_greetings=extra_greetings,
                extra_session_lines=session_lines,
                lang_preset=preset,
                languages=active_langs,
                voice_per_lang=voice_per_lang or None,
                gemini_voice=tts_voice,
                gemini_model=gemini_model,
                disk_only=not api_key,
            )
            logger.info(
                "[PREWARM] Greeting/filler/session cache ready (%s, preset=%s, langs=%s, session=%d, disk_only=%s)",
                tts_provider,
                preset,
                "+".join(active_langs),
                len(session_lines),
                not api_key,
            )
        except Exception as e:
            logger.warning("[PREWARM] Greeting prebake failed (%s): %s", tts_provider, e)

        _prewarm_done = True
        _last_prewarm_preset = preset
        _last_prewarm_signature = signature
        logger.info(
            "[PREWARM] Worker prewarm complete (preset=%s, provider=%s, voice=%s)",
            preset,
            tts_provider,
            tts_voice,
        )


def load_vad():
    """Load Silero VAD — interruption detection only when turn_detection=stt."""
    try:
        from livekit.plugins import silero

        return silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.2,
            activation_threshold=0.5,
        )
    except Exception as e:
        logger.warning("[PREWARM] Silero VAD load failed: %s", e)
        return None


async def ensure_soniox_http_session(userdata: dict):
    """Lazy reusable aiohttp session for Soniox STT (must run inside async entrypoint)."""
    existing = userdata.get("soniox_http_session")
    if existing is not None and not getattr(existing, "closed", True):
        return existing
    try:
        import aiohttp

        session = aiohttp.ClientSession()
        userdata["soniox_http_session"] = session
        logger.info("[STT] Soniox aiohttp session created (reused per worker process)")
        return session
    except Exception as e:
        logger.warning("[STT] Soniox http_session create failed: %s", e)
        return None