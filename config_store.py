"""Read/write config.json for the live voice agent (VK Realty main agent)."""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

MAIN_AGENT_LABEL = "VK Realty — Arjun (Main Agent)"

# Keys managed by the main-agent dashboard panel.
MAIN_AGENT_FIELDS = (
    "agent_name",
    "agent_instructions",
    "first_line",
    "welcome_dynamic",
    "welcome_interruptable",
    "language",
    "lang_preset",
    "tts_provider",
    "stt_provider",
    "llm_provider",
    "llm_model",
    "gemini_model",
    "groq_model",
    "llm_max_completion_tokens",
    "llm_startup_probe",
    "max_turns",
    "tts_voice",
    "tts_language",
    "stt_language",
    "stt_min_endpointing_delay",
    "stt_max_endpointing_delay",
    "stt_silence_timeout_ms",
    "stt_max_endpoint_delay_ms",
    "stt_endpoint_sensitivity",
    "stt_enable_language_identification",
    "stt_enable_endpoint_detection",
    "tts_hard_word_cap",
    "timezone",
    "tone_preset",
    "campaign_id",
)


def _env_override(key: str, env_key: str, default=None):
    if os.path.exists(CONFIG_FILE):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if data.get(key) not in (None, ""):
                return data.get(key)
        except Exception:
            pass
    env_val = os.getenv(env_key, "")
    if env_val not in ("", None):
        return env_val
    return default


def read_raw_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_full_config() -> dict:
    """Full config.json contents with sensible defaults."""
    raw = read_raw_config()
    return {
        "agent_name": raw.get("agent_name") or _env_override("agent_name", "AGENT_NAME", "inbound-voice-demo"),
        "agent_instructions": raw.get("agent_instructions") or _env_override("agent_instructions", "AGENT_INSTRUCTIONS", ""),
        "first_line": raw.get("first_line") or _env_override("first_line", "FIRST_LINE", ""),
        "language": raw.get("language") or _env_override("language", "LANGUAGE", "te"),
        "lang_preset": raw.get("lang_preset") or _env_override("lang_preset", "LANG_PRESET", "hme"),
        "tts_provider": raw.get("tts_provider") or _env_override("tts_provider", "TTS_PROVIDER", "gemini"),
        "gemini_tts_model": raw.get("gemini_tts_model") or _env_override("gemini_tts_model", "GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"),
        "cartesia_tts_model": raw.get("cartesia_tts_model") or _env_override("cartesia_tts_model", "CARTESIA_TTS_MODEL", "sonic-3.5"),
        "stt_provider": raw.get("stt_provider") or _env_override("stt_provider", "STT_PROVIDER", "soniox"),
        "llm_provider": raw.get("llm_provider") or _env_override("llm_provider", "LLM_PROVIDER", "gemini"),
        "llm_model": raw.get("llm_model") or _env_override("llm_model", "LLM_MODEL", "gpt-4o-mini"),
        "gemini_model": raw.get("gemini_model") or _env_override("gemini_model", "GEMINI_MODEL", "gemini-2.5-flash"),
        "groq_model": raw.get("groq_model") or _env_override("groq_model", "GROQ_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        "llm_max_completion_tokens": int(raw.get("llm_max_completion_tokens") or _env_override("llm_max_completion_tokens", "LLM_MAX_COMPLETION_TOKENS", 100)),
        "llm_startup_probe": bool(raw.get("llm_startup_probe", False)),
        "max_turns": int(raw.get("max_turns") or _env_override("max_turns", "MAX_TURNS", 40)),
        "tts_voice": raw.get("tts_voice") or _env_override("tts_voice", "TTS_VOICE", "Rasalgethi"),
        "tts_language": raw.get("tts_language") or _env_override("tts_language", "TTS_LANGUAGE", "te-IN"),
        "stt_language": raw.get("stt_language") or _env_override("stt_language", "STT_LANGUAGE", "te-IN"),
        "stt_min_endpointing_delay": float(raw.get("stt_min_endpointing_delay") or 0.2),
        "stt_max_endpointing_delay": float(raw.get("stt_max_endpointing_delay") or 0.2),
        "stt_silence_timeout_ms": int(
            raw.get("stt_silence_timeout_ms") or raw.get("stt_max_endpoint_delay_ms") or 200
        ),
        "stt_max_endpoint_delay_ms": int(
            raw.get("stt_silence_timeout_ms") or raw.get("stt_max_endpoint_delay_ms") or 200
        ),
        "stt_endpoint_sensitivity": float(raw.get("stt_endpoint_sensitivity") or 0.35),
        "stt_enable_language_identification": bool(
            raw.get("stt_enable_language_identification", True)
        ),
        "stt_enable_endpoint_detection": bool(raw.get("stt_enable_endpoint_detection", True)),
        "tts_hard_word_cap": int(raw.get("tts_hard_word_cap") or 50),
        "timezone": raw.get("timezone") or _env_override("timezone", "TIMEZONE", "Asia/Kolkata"),
        "tone_preset": raw.get("tone_preset") or "professional",
        "campaign_id": raw.get("campaign_id") or _env_override("campaign_id", "CAMPAIGN_ID", ""),
    }


LEGACY_CONFIG_KEYS = frozenset({"cal_event_type_id"})


def write_full_config(updates: dict) -> dict:
    """Merge updates into config.json and return the saved config."""
    current = read_raw_config()
    for key in LEGACY_CONFIG_KEYS:
        current.pop(key, None)
    for key, value in updates.items():
        if value is None:
            continue
        current[key] = value
    CONFIG_FILE.write_text(json.dumps(current, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    return read_full_config()


def get_voice_config_payload() -> dict:
    """Cartesia / TTS voice settings from config.json (agent runtime)."""
    raw = read_raw_config()
    assistant = raw.get("assistant_settings") if isinstance(raw.get("assistant_settings"), dict) else {}
    voice = assistant.get("voice") if isinstance(assistant.get("voice"), dict) else {}
    return {
        "tts_provider": str(raw.get("tts_provider") or os.getenv("TTS_PROVIDER") or "cartesia"),
        "cartesia_tts_model": str(
            raw.get("cartesia_tts_model") or os.getenv("CARTESIA_TTS_MODEL") or "sonic-3.5"
        ),
        "tts_voice": str(raw.get("tts_voice") or voice.get("voice_id") or ""),
        "tts_voice_display": str(raw.get("tts_voice_display") or voice.get("display_name") or ""),
        "tts_language": str(raw.get("tts_language") or "te-IN"),
    }


def apply_voice_config(updates: dict) -> dict[str, str]:
    """Persist voice/TTS fields to config.json and sync assistant_settings."""
    applied: dict[str, str] = {}
    raw = read_raw_config()
    patch: dict = {}

    for key in ("tts_provider", "cartesia_tts_model", "tts_voice", "tts_voice_display", "tts_language"):
        val = updates.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if not text:
            continue
        patch[key] = text
        applied[key] = text

    if not patch:
        return applied

    assistant = raw.get("assistant_settings") if isinstance(raw.get("assistant_settings"), dict) else {}
    voice = assistant.get("voice") if isinstance(assistant.get("voice"), dict) else {}
    if patch.get("tts_provider"):
        voice["provider"] = patch["tts_provider"]
    if patch.get("tts_voice"):
        voice["voice_id"] = patch["tts_voice"]
    if patch.get("tts_voice_display"):
        voice["display_name"] = patch["tts_voice_display"]
    assistant["voice"] = voice
    patch["assistant_settings"] = assistant

    write_full_config({**raw, **patch})

    env_patch: dict[str, str] = {}
    if patch.get("tts_provider"):
        env_patch["TTS_PROVIDER"] = patch["tts_provider"]
    if patch.get("cartesia_tts_model"):
        env_patch["CARTESIA_TTS_MODEL"] = patch["cartesia_tts_model"]
    if env_patch:
        try:
            from env_manager import update_env_keys

            update_env_keys(env_patch)
            applied.update(env_patch)
        except Exception:
            pass

    try:
        from lang_presets_store import load_store, save_store

        store = load_store()
        active = str(store.get("active_preset_id") or raw.get("lang_preset") or "tinglish")
        presets = store.get("presets") or []
        for preset in presets:
            if str(preset.get("id")) != active:
                continue
            if patch.get("tts_voice"):
                preset["tts_voice"] = patch["tts_voice"]
            if patch.get("tts_voice_display"):
                preset["tts_voice_display"] = patch["tts_voice_display"]
            if patch.get("tts_language"):
                preset["tts_language"] = patch["tts_language"]
            if patch.get("tts_voice") and isinstance(preset.get("voice_per_lang"), dict):
                for lang in preset["voice_per_lang"]:
                    preset["voice_per_lang"][lang] = patch["tts_voice"]
            break
        save_store(store)
    except Exception:
        pass

    return applied


def main_agent_payload() -> dict:
    cfg = read_full_config()
    default_agent_id = (os.getenv("DEFAULT_AGENT_ID") or "").strip()
    source = "config.json"
    if default_agent_id:
        source = "database"
    return {
        "label": MAIN_AGENT_LABEL,
        "source": source,
        "default_agent_id": default_agent_id or None,
        "config_file": str(CONFIG_FILE.name),
        "config": cfg,
    }