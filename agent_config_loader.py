"""Load Voice AI agent configuration from Supabase for the LiveKit worker."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from agent_models import FullAgentConfig

logger = logging.getLogger("agent_config_loader")

DEFAULT_AGENT_ID = "a0000000-0000-4000-8000-000000000001"

DEFAULT_PROMPT_VERSION = "stable"
DEFAULT_CANARY_PROMPT_VERSION = "canary-v1"


def get_engine_canary_percent() -> int:
    """Percent of traffic routed to canary prompt/engine (0–100)."""
    raw = os.environ.get("ENGINE_CANARY_PERCENT", "0").strip()
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return 0


def get_prompt_version_env() -> str:
    """Stable prompt version tag for logs and observability."""
    return (os.environ.get("PROMPT_VERSION", DEFAULT_PROMPT_VERSION).strip() or DEFAULT_PROMPT_VERSION)


def get_canary_prompt_version_env() -> str:
    """Prompt version tag used when a call is in the canary bucket."""
    return (
        os.environ.get("PROMPT_VERSION_CANARY", DEFAULT_CANARY_PROMPT_VERSION).strip()
        or DEFAULT_CANARY_PROMPT_VERSION
    )


def is_canary_seed(seed: str, percent: int | None = None) -> bool:
    """Deterministic canary assignment from room id / phone hash."""
    pct = get_engine_canary_percent() if percent is None else max(0, min(100, int(percent)))
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    digest = hashlib.sha256((seed or "default").encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < pct


def apply_canary_to_live_config(live_config: dict, seed: str) -> dict:
    """Tag live_config with canary bucket + resolved prompt version."""
    merged = dict(live_config)
    pct = get_engine_canary_percent()
    canary = is_canary_seed(seed, pct)
    merged["is_canary"] = canary
    merged["engine_canary_percent"] = pct
    merged["prompt_version_stable"] = get_prompt_version_env()
    merged["prompt_version_canary"] = get_canary_prompt_version_env()
    merged["prompt_version"] = (
        merged["prompt_version_canary"] if canary else merged["prompt_version_stable"]
    )
    return merged


def get_canary_health_info() -> dict[str, Any]:
    """Snapshot for /health — canary deployment flags."""
    pct = get_engine_canary_percent()
    return {
        "canary_enabled": pct > 0,
        "engine_canary_percent": pct,
        "prompt_version": get_prompt_version_env(),
        "prompt_version_canary": get_canary_prompt_version_env(),
    }


INTERRUPTION_PRESETS: dict[str, tuple[float, int]] = {
    "low": (0.5, 3),
    "medium": (0.35, 2),
    "high": (0.2, 1),
}


def _normalize_stt_model(provider: str, model: str | None) -> str:
    """Map DB stt_model to runtime model id."""
    raw = (model or "").strip().lower()
    if provider == "soniox":
        if not raw or raw in ("soniox", "soniox-v2", "default"):
            return "stt-rt-v5"
        return model or "stt-rt-v5"
    if provider == "sarvam":
        if not raw or raw in ("saarika", "saaras", "default"):
            return "saaras:v3"
        return model or "saaras:v3"
    if provider == "deepgram":
        return model or "nova-2-general"
    return model or ""


def _as_dict(value: Any) -> dict:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def apply_db_agent_to_live_config(
    live_config: dict,
    full_config: FullAgentConfig,
) -> dict:
    """Merge database agent config into the legacy live_config dict used by agent.py."""
    agent = full_config.agent
    merged = dict(live_config)

    merged["agent_instructions"] = full_config.merged_system_prompt()
    merged["first_line"] = agent.welcome_message or merged.get("first_line", "")
    merged["llm_provider"] = "gemini"
    merged["llm_temperature"] = float(agent.temperature)
    merged["gemini_model"] = "gemini-2.5-flash"
    merged["llm_model"] = "gemini-2.5-flash"
    merged["tts_voice"] = agent.tts_voice_id or merged.get("tts_voice", "kavya")
    merged["tts_provider"] = agent.tts_provider or merged.get("tts_provider", "gemini")
    stt_provider = agent.stt_provider or merged.get("stt_provider", "soniox")
    if (stt_provider or "").lower() != "soniox":
        logger.info("[CONFIG] Overriding stt_provider '%s' → soniox", stt_provider)
        stt_provider = "soniox"
    merged["stt_provider"] = stt_provider
    merged["stt_model"] = _normalize_stt_model(stt_provider, agent.stt_model)
    merged["agent_id"] = str(agent.id)
    merged["agent_name"] = agent.name
    merged["agent_purpose"] = agent.purpose or ""
    merged["max_call_duration_seconds"] = agent.max_call_duration_seconds

    from language_router import (
        HME_PRESET,
        TINGLISH_PRESET,
        get_default_turn_language,
        resolve_lang_preset,
    )

    langs = [str(l).lower().replace("_", "-") for l in (agent.languages or ["te", "en"])]
    preset = resolve_lang_preset(None, {"languages": langs})
    merged["lang_preset"] = preset
    merged["language"] = get_default_turn_language(preset, {"languages": langs})
    if preset == "english":
        merged["tts_language"] = merged.get("tts_language", "en-IN")
    elif preset == HME_PRESET:
        merged["tts_language"] = merged.get("tts_language", "hi-IN")
    elif preset == TINGLISH_PRESET:
        merged["tts_language"] = merged.get("tts_language", "te-IN")
    else:
        merged["tts_language"] = merged.get("tts_language", "en-IN")

    cc = full_config.call_configuration
    if cc:
        end_rules = _as_dict(cc.end_call_rules)
        response = _as_dict(cc.response_behavior)
        silence = _as_dict(cc.silence_handling)
        transfer = _as_dict(cc.transfer_routing)
        advanced = _as_dict(cc.advanced_settings)

        max_dur = end_rules.get("max_duration_seconds") or agent.max_call_duration_seconds
        try:
            merged["max_call_duration_seconds"] = int(max_dur)
        except (TypeError, ValueError):
            pass

        sensitivity = str(response.get("interruption_sensitivity") or "medium").lower()
        min_dur, min_words = INTERRUPTION_PRESETS.get(sensitivity, INTERRUPTION_PRESETS["medium"])
        merged["min_interruption_duration"] = min_dur
        merged["min_interruption_words"] = min_words
        merged["allow_interruptions"] = True

        silence_timeout = silence.get("timeout_seconds", 10)
        merged["stt_min_endpointing_delay"] = min(
            float(merged.get("stt_min_endpointing_delay", 0.35)),
            max(0.05, float(silence_timeout) / 30),
        )
        merged["user_idle"] = {
            "threshold_sec": int(silence_timeout),
            "max_prompts": int(silence.get("max_prompts") or 2),
            "prompt_message": str(silence.get("prompt_message") or "Are you still there?"),
            "end_after_max": bool(silence.get("end_call_after_max_prompts", False)),
            "last_message": str(
                end_rules.get("voicemail_message")
                or "I'll leave you for now. Have a nice day!"
            ),
        }
        merged["call_ending"] = {
            "max_duration_sec": int(max_dur) if max_dur else merged.get("max_call_duration_seconds", 600),
            "enabled": True,
            "end_phrases": list(end_rules.get("end_phrases") or ["goodbye", "bye", "thank you"]),
            "message": str(
                end_rules.get("voicemail_message")
                or "Thank you for calling. Have a great day! Goodbye."
            ),
        }
        merged["noise_reduction"] = bool(advanced.get("noise_cancellation", True))

        if isinstance(response.get("max_response_sentences"), int):
            merged["tts_hard_word_cap"] = max(12, response["max_response_sentences"] * 8)

        merged["filler_phrases_enabled"] = bool(response.get("filler_phrases_enabled", True))
        merged["backchannel_enabled"] = bool(response.get("backchannel_enabled", False))

        merged["_call_config"] = {
            "silence_handling": silence,
            "end_call_rules": end_rules,
            "transfer_routing": transfer,
            "response_behavior": response,
            "ambient_sound": _as_dict(cc.ambient_sound),
            "advanced_settings": advanced,
        }

    meta = agent.metadata or {}
    integrations = meta.get("integrations") or {}
    merged["calendar_enabled"] = bool(integrations.get("calendar_enabled"))
    merged["crm_enabled"] = bool(integrations.get("crm_enabled"))
    merged["knowledge_search_enabled"] = bool(meta.get("search_enabled"))

    return merged


async def resolve_dynamic_agent_config(
    metadata: dict | None,
    base_live_config: dict,
) -> tuple[dict, str | None, FullAgentConfig | None]:
    """
    Resolve runtime config from job metadata + DB agent.

    Returns:
        (live_config, agent_id, full_config)
    """
    if os.environ.get("USE_FILE_CONFIG_ONLY", "true").strip().lower() in ("1", "true", "yes", "on"):
        logger.info("[AGENT-CONFIG] DB/preset agents disabled — using config.json (VK Realty Arjun)")
        return base_live_config, None, None

    meta = metadata or {}
    agent_id = (
        meta.get("agent_id")
        or os.environ.get("DEFAULT_AGENT_ID")
        or (DEFAULT_AGENT_ID if meta.get("is_demo") else None)
    )

    if not agent_id:
        logger.info("[AGENT-CONFIG] No agent_id in metadata — using file/default config")
        return base_live_config, None, None

    try:
        import agents_db

        full = await agents_db.get_agent_config(agent_id)
        if not full:
            logger.warning("[AGENT-CONFIG] agent_id=%s not found — using default config", agent_id)
            return base_live_config, None, None

        if not full.agent.is_active:
            logger.warning(
                "[AGENT-CONFIG] agent '%s' is inactive — using default config",
                full.agent.name,
            )
            return base_live_config, None, None

        merged = apply_db_agent_to_live_config(base_live_config, full)
        logger.info(
            "[AGENT-CONFIG] Loaded dynamic agent '%s' (%s) from DB",
            full.agent.name,
            agent_id,
        )
        return merged, str(agent_id), full
    except Exception as exc:
        logger.error("[AGENT-CONFIG] Failed to load agent %s: %s — using default", agent_id, exc)
        return base_live_config, None, None


def parse_job_metadata(raw: str | None) -> dict:
    """Parse LiveKit job metadata JSON safely."""
    if not raw:
        return {}
    try:
        import json

        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}