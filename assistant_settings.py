"""
Structured assistant settings — persisted in config.json (main agent) or Supabase (DB agents).

Flat runtime keys in config.json remain the source of truth for agent.py; this module
provides the rich dashboard schema and bidirectional mapping.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from config_store import CONFIG_FILE, read_raw_config, write_full_config

BASE_DIR = Path(__file__).resolve().parent
RELOAD_SIGNAL = BASE_DIR / "data" / "config_reload.signal"

LANGUAGE_OPTIONS = [
    {"code": "en-us", "label": "English (American)"},
    {"code": "en-in", "label": "English (India)"},
    {"code": "hi", "label": "Hindi"},
    {"code": "te", "label": "Telugu"},
    {"code": "ta", "label": "Tamil"},
    {"code": "mr", "label": "Marathi"},
    {"code": "bn", "label": "Bengali"},
    {"code": "kn", "label": "Kannada"},
    {"code": "ml", "label": "Malayalam"},
    {"code": "gu", "label": "Gujarati"},
    {"code": "pa", "label": "Punjabi"},
]

def _voice_catalog() -> dict[str, list[dict[str, str]]]:
    from cartesia_tts import cartesia_voice_catalog_entries
    from gemini_tts import gemini_voice_catalog_entries

    gemini_voices = gemini_voice_catalog_entries()
    cartesia_voices = cartesia_voice_catalog_entries()
    return {
    "recommended": [
        *cartesia_voices,
        {"id": "Rasalgethi", "name": "Rasalgethi", "gender": "neutral", "quality": "Informative", "provider": "gemini"},
        {"id": "Sulafat", "name": "Sulafat", "gender": "neutral", "quality": "Warm", "provider": "gemini"},
        {"id": "Achird", "name": "Achird", "gender": "neutral", "quality": "Friendly", "provider": "gemini"},
        {"id": "kavya", "name": "Kavya", "gender": "female", "quality": "premium", "provider": "sarvam"},
        {"id": "ashutosh", "name": "Ashutosh", "gender": "male", "quality": "premium", "provider": "sarvam"},
    ],
    "cloned": cartesia_voices,
    "google": gemini_voices,
    "elevenlabs": [
        {"id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel", "gender": "female", "quality": "turbo", "provider": "elevenlabs"},
    ],
    "cartesia": cartesia_voices,
    "sarvam": [
        {"id": "kavya", "name": "Kavya", "gender": "female", "quality": "bulbul-v3", "provider": "sarvam"},
        {"id": "ashutosh", "name": "Ashutosh", "gender": "male", "quality": "bulbul-v3", "provider": "sarvam"},
        {"id": "priya", "name": "Priya", "gender": "female", "quality": "bulbul-v3", "provider": "sarvam"},
    ],
    }


VOICE_CATALOG: dict[str, list[dict[str, str]]] = _voice_catalog()


def lookup_voice_catalog_entry(voice_id: str) -> dict[str, str] | None:
    """Find a voice by id across all catalog tabs."""
    vid = str(voice_id or "").strip()
    if not vid:
        return None
    for tab, voices in VOICE_CATALOG.items():
        for voice in voices:
            if str(voice.get("id") or "") == vid:
                return {**voice, "tab": tab}
    return None


def resolve_voice_display_name(voice_id: str, display_name: str = "") -> str:
    """Return a human-readable voice label, falling back to catalog lookup."""
    vid = str(voice_id or "").strip()
    name = str(display_name or "").strip()
    if name and name != vid:
        return name
    entry = lookup_voice_catalog_entry(vid)
    if entry:
        return str(entry.get("name") or vid)
    return vid


def resolve_voice_tab(voice_id: str, tab: str = "") -> str:
    """Infer catalog tab for a voice id when not explicitly stored."""
    chosen = str(tab or "").strip()
    if chosen:
        return chosen
    entry = lookup_voice_catalog_entry(str(voice_id or "").strip())
    if entry:
        return str(entry.get("tab") or "recommended")
    return "recommended"


class FlowSectionSettings(BaseModel):
    id: str
    title: str
    content: str = ""
    enabled: bool = True


class WelcomeMessageSettings(BaseModel):
    message: str = ""
    dynamic: bool = True
    interruptable: bool = True
    max_chars: int = Field(default=600, ge=1, le=2000)


def resolve_welcome_message(
    template: str,
    *,
    dynamic: bool = True,
    caller_name: str = "",
) -> str:
    """Expand welcome template; substitute [user_name] when dynamic is enabled."""
    msg = str(template or "").strip()
    if not dynamic or "[user_name]" not in msg:
        return msg

    name = str(caller_name or "").strip()
    placeholders = {"", "unknown", "caller", "unknown caller"}
    if not name or name.lower() in placeholders:
        cleaned = msg.replace("[user_name]", "").replace("  ", " ").strip()
        return cleaned or msg.replace("[user_name]", "there")
    return msg.replace("[user_name]", name)


class VoiceSettings(BaseModel):
    tab: str = "recommended"
    provider: str = "gemini"
    voice_id: str = "Rasalgethi"
    display_name: str = ""

    @model_validator(mode="after")
    def _resolve_display_from_catalog(self) -> VoiceSettings:
        if not self.display_name or self.display_name == self.voice_id:
            entry = lookup_voice_catalog_entry(self.voice_id)
            if entry:
                self.display_name = str(entry.get("name") or self.voice_id)
                if self.tab == "recommended" and entry.get("tab"):
                    self.tab = str(entry["tab"])
        return self


class LLMSettings(BaseModel):
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    temperature: float = Field(default=0.70, ge=0.0, le=2.0)
    streaming: bool = True
    max_completion_tokens: int = Field(default=200, ge=40, le=4096)


class STTSettings(BaseModel):
    provider: str = "soniox"
    model: str = "stt-rt-v5"
    language: str = "te-IN"
    silence_timeout_ms: int = Field(default=200, ge=200, le=3000)
    noise_reduction: bool = True
    min_endpointing_delay: float = Field(default=0.2, ge=0.2, le=3.0)
    max_endpointing_delay: float = Field(default=0.2, ge=0.2, le=5.0)
    # Soniox WebSocket API — endpoint detection (silence timeout = time-to-final)
    max_endpoint_delay_ms: int = Field(default=200, ge=200, le=3000)
    endpoint_sensitivity: float = Field(default=0.35, ge=-1.0, le=1.0)
    enable_language_identification: bool = True
    enable_endpoint_detection: bool = True


class ListeningModeParams(BaseModel):
    speech_detection_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    user_speech_start_duration_ms: int = Field(default=200, ge=50, le=2000)
    minimum_user_speech_duration_ms: int = Field(default=300, ge=50, le=3000)


class BotSpeakingParams(BaseModel):
    minimum_words_to_interrupt: int = Field(default=1, ge=0, le=10)
    speech_detection_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    user_speech_start_duration_ms: int = Field(default=200, ge=50, le=2000)
    minimum_user_speech_duration_ms: int = Field(default=300, ge=50, le=3000)


class AdvancedSettings(BaseModel):
    speaking_interruption_allowed: bool = True
    speculative_reply_enabled: bool = False
    speculative_reply_min_speech_ms: int = Field(default=2000, ge=1500, le=8000)
    parallel_turn_injectors_enabled: bool = False
    listening: ListeningModeParams = Field(default_factory=ListeningModeParams)
    bot_speaking: BotSpeakingParams = Field(default_factory=BotSpeakingParams)


class AmbientSoundSettings(BaseModel):
    enabled: bool = True
    sound_type: str = "cicd_1"
    volume: float = Field(default=0.12, ge=0.08, le=0.18)
    file_path: str = ""


class ConversationalFlowSettings(BaseModel):
    identity_purpose: FlowSectionSettings = Field(
        default_factory=lambda: FlowSectionSettings(
            id="identity",
            title="Identity & Purpose",
            content="",
            enabled=True,
        )
    )
    facts: FlowSectionSettings = Field(
        default_factory=lambda: FlowSectionSettings(id="facts", title="Facts", content="", enabled=True)
    )
    actions_limits: FlowSectionSettings = Field(
        default_factory=lambda: FlowSectionSettings(
            id="actions",
            title="Actions & Limits",
            content="",
            enabled=True,
        )
    )


class AssistantSettings(BaseModel):
    assistant_id: str = "main"
    name: str = "UrbanNest Realty Lead Qualifier"
    languages: list[str] = Field(default_factory=lambda: ["te"])
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    stt: STTSettings = Field(default_factory=STTSettings)
    advanced: AdvancedSettings = Field(default_factory=AdvancedSettings)
    ambient_sound: AmbientSoundSettings = Field(default_factory=AmbientSoundSettings)
    welcome: WelcomeMessageSettings = Field(default_factory=WelcomeMessageSettings)
    flow: ConversationalFlowSettings = Field(default_factory=ConversationalFlowSettings)
    system_prompt: str = ""
    max_turns: int = Field(default=40, ge=5, le=200)
    max_call_duration_seconds: int = Field(default=1800, ge=60, le=7200)

    @field_validator("languages")
    @classmethod
    def _langs_nonempty(cls, v: list[str]) -> list[str]:
        if not v:
            return ["te"]
        return v


def _ambient_from_raw(raw: dict[str, Any]) -> AmbientSoundSettings:
    block = raw.get("ambient_sound")
    if not isinstance(block, dict):
        nested = raw.get("_call_config")
        if isinstance(nested, dict) and isinstance(nested.get("ambient_sound"), dict):
            block = nested["ambient_sound"]
        else:
            block = {}
    try:
        return AmbientSoundSettings.model_validate(block)
    except Exception:
        return AmbientSoundSettings()


def _primary_language(langs: list[str]) -> str:
    if not langs:
        return "te"
    code = langs[0].lower().replace("_", "-")
    if code in ("en-us", "en"):
        return "en-in"
    if code == "english":
        return "en-in"
    if code.startswith("te"):
        return "te"
    return code.split("-")[0] if "-" in code else code


def settings_from_flat_config(raw: dict[str, Any], *, assistant_id: str = "main") -> AssistantSettings:
    """Build structured settings from config.json flat keys + nested assistant_settings."""
    nested = raw.get("assistant_settings") if isinstance(raw.get("assistant_settings"), dict) else {}
    if nested:
        try:
            data = dict(nested)
            data.setdefault("assistant_id", assistant_id)
            return AssistantSettings.model_validate(data)
        except Exception:
            pass

    langs = raw.get("languages")
    if not isinstance(langs, list) or not langs:
        lang = str(raw.get("language") or "te")
        langs = [lang]

    flow_sections = raw.get("conversational_flow_sections")
    flow = ConversationalFlowSettings()
    if isinstance(flow_sections, list):
        by_id = {s.get("id"): s for s in flow_sections if isinstance(s, dict)}
        for key, default in (
            ("identity", flow.identity_purpose),
            ("facts", flow.facts),
            ("actions", flow.actions_limits),
        ):
            sec = by_id.get(key) or by_id.get(default.id)
            if sec:
                setattr(flow, key if key != "actions" else "actions_limits", FlowSectionSettings.model_validate(sec))

    adv_raw = raw.get("advanced_settings") if isinstance(raw.get("advanced_settings"), dict) else {}
    user_idle = raw.get("user_idle") if isinstance(raw.get("user_idle"), dict) else {}

    return AssistantSettings(
        assistant_id=assistant_id,
        name=str(raw.get("agent_name") or "Main Agent"),
        languages=[str(l) for l in langs],
        voice=VoiceSettings(
            tab=resolve_voice_tab(
                str(raw.get("tts_voice") or "Rasalgethi"),
                str(raw.get("tts_voice_tab") or ""),
            ),
            provider=str(raw.get("tts_provider") or "gemini"),
            voice_id=str(raw.get("tts_voice") or "Rasalgethi"),
            display_name=resolve_voice_display_name(
                str(raw.get("tts_voice") or "Rasalgethi"),
                str(raw.get("tts_voice_display") or ""),
            ),
        ),
        llm=LLMSettings(
            provider=str(raw.get("llm_provider") or "gemini"),
            model=str(raw.get("gemini_model") or raw.get("llm_model") or "gemini-2.5-flash"),
            temperature=float(raw.get("llm_temperature") or 0.70),
            streaming=bool(raw.get("llm_streaming", True)),
            max_completion_tokens=int(raw.get("llm_max_completion_tokens") or 200),
        ),
        stt=STTSettings(
            provider=str(raw.get("stt_provider") or "soniox"),
            model=str(raw.get("stt_model") or "stt-rt-v5"),
            language=str(raw.get("stt_language") or "te-IN"),
            silence_timeout_ms=int(
                raw.get("stt_silence_timeout_ms") or raw.get("stt_max_endpoint_delay_ms") or 200
            ),
            noise_reduction=bool(raw.get("noise_reduction", True)),
            min_endpointing_delay=float(raw.get("stt_min_endpointing_delay") or 0.2),
            max_endpointing_delay=float(raw.get("stt_max_endpointing_delay") or 0.2),
            max_endpoint_delay_ms=int(
                raw.get("stt_silence_timeout_ms") or raw.get("stt_max_endpoint_delay_ms") or 200
            ),
            endpoint_sensitivity=float(raw.get("stt_endpoint_sensitivity") or 0.35),
            enable_language_identification=bool(
                raw.get("stt_enable_language_identification", True)
            ),
            enable_endpoint_detection=bool(raw.get("stt_enable_endpoint_detection", True)),
        ),
        ambient_sound=_ambient_from_raw(raw),
        advanced=AdvancedSettings(
            speaking_interruption_allowed=bool(raw.get("allow_interruptions", True)),
            speculative_reply_enabled=bool(raw.get("speculative_reply_enabled", False)),
            speculative_reply_min_speech_ms=int(raw.get("speculative_reply_min_speech_ms") or 2000),
            parallel_turn_injectors_enabled=bool(raw.get("parallel_turn_injectors_enabled", False)),
            listening=ListeningModeParams(
                speech_detection_confidence=float(adv_raw.get("listening_speech_confidence") or 0.5),
                user_speech_start_duration_ms=int(adv_raw.get("listening_start_ms") or 200),
                minimum_user_speech_duration_ms=int(adv_raw.get("listening_min_ms") or 300),
            ),
            bot_speaking=BotSpeakingParams(
                minimum_words_to_interrupt=int(raw.get("min_interruption_words") or 1),
                speech_detection_confidence=float(adv_raw.get("bot_speech_confidence") or 0.5),
                user_speech_start_duration_ms=int(adv_raw.get("bot_start_ms") or 200),
                minimum_user_speech_duration_ms=int(adv_raw.get("bot_min_ms") or 300),
            ),
        ),
        welcome=WelcomeMessageSettings(
            message=str(raw.get("first_line") or ""),
            dynamic=bool(raw.get("welcome_dynamic", True)),
            interruptable=bool(raw.get("welcome_interruptable", True)),
        ),
        flow=flow,
        system_prompt=str(raw.get("agent_instructions") or ""),
        max_turns=int(raw.get("max_turns") or 40),
        max_call_duration_seconds=int(raw.get("max_call_duration_seconds") or 1800),
    )


def _lang_codes_normalized(langs: list[str]) -> set[str]:
    out: set[str] = set()
    for raw in langs:
        code = str(raw or "").strip().lower().replace("_", "-")
        if code in ("en", "english", "en-us"):
            out.add("en-in")
        elif code in ("hindi", "hi-in"):
            out.add("hi")
        elif code in ("marathi", "mr-in"):
            out.add("mr")
        elif code.startswith("te"):
            out.add("te")
        elif code:
            out.add(code)
    return out


def flat_updates_from_settings(settings: AssistantSettings) -> dict[str, Any]:
    """Map structured settings → flat config.json keys consumed by agent.py."""
    primary = _primary_language(settings.languages)
    lang_codes = _lang_codes_normalized(settings.languages)
    if lang_codes >= {"hi", "en-in", "mr"}:
        lang_preset = "hme"
        primary = "hi"
    elif primary == "te" or "te" in lang_codes:
        lang_preset = "tinglish"
    elif primary.startswith("en"):
        lang_preset = "english"
    else:
        lang_preset = primary

    tts_lang = settings.stt.language
    if primary == "te":
        tts_lang = tts_lang or "te-IN"

    flow_sections = [
        settings.flow.identity_purpose.model_dump(),
        settings.flow.facts.model_dump(),
        settings.flow.actions_limits.model_dump(),
    ]
    merged_prompt = settings.system_prompt.strip()
    for sec in flow_sections:
        if sec.get("enabled") and sec.get("content"):
            merged_prompt += f"\n\n## {sec['title']}\n{sec['content']}"

    from language_router import DEFAULT_USER_IDLE_THRESHOLD_SEC, idle_messages_for_preset
    from gemini_llm import resolve_gemini_conversation_model

    idle_defaults = idle_messages_for_preset(lang_preset)
    gemini_model = resolve_gemini_conversation_model({"lang_preset": lang_preset})

    return {
        "agent_name": settings.name,
        "agent_instructions": merged_prompt or settings.system_prompt,
        "first_line": settings.welcome.message,
        "welcome_dynamic": settings.welcome.dynamic,
        "welcome_interruptable": settings.welcome.interruptable,
        "languages": settings.languages,
        "language": primary,
        "lang_preset": lang_preset,
        "tts_provider": settings.voice.provider,
        "tts_voice": settings.voice.voice_id,
        "tts_voice_tab": settings.voice.tab,
        "tts_voice_display": settings.voice.display_name,
        "tts_language": tts_lang,
        "gemini_tts_model": "gemini-3.1-flash-tts-preview",
        "cartesia_tts_model": "sonic-3.5",
        "stt_provider": settings.stt.provider,
        "stt_model": settings.stt.model,
        "stt_language": settings.stt.language,
        "stt_min_endpointing_delay": settings.stt.min_endpointing_delay,
        "stt_max_endpointing_delay": settings.stt.max_endpointing_delay,
        "stt_silence_timeout_ms": settings.stt.silence_timeout_ms,
        "stt_max_endpoint_delay_ms": settings.stt.max_endpoint_delay_ms,
        "stt_endpoint_sensitivity": settings.stt.endpoint_sensitivity,
        "stt_enable_language_identification": settings.stt.enable_language_identification,
        "stt_enable_endpoint_detection": settings.stt.enable_endpoint_detection,
        "llm_provider": settings.llm.provider,
        "llm_model": gemini_model,
        "gemini_model": gemini_model,
        "llm_temperature": settings.llm.temperature,
        "llm_streaming": settings.llm.streaming,
        "llm_max_completion_tokens": settings.llm.max_completion_tokens,
        "max_turns": settings.max_turns,
        "max_call_duration_seconds": settings.max_call_duration_seconds,
        "allow_interruptions": settings.advanced.speaking_interruption_allowed,
        "speculative_reply_enabled": settings.advanced.speculative_reply_enabled,
        "speculative_reply_min_speech_ms": settings.advanced.speculative_reply_min_speech_ms,
        "parallel_turn_injectors_enabled": settings.advanced.parallel_turn_injectors_enabled,
        "system_prompt_token_budget": 1000,
        "min_interruption_words": settings.advanced.bot_speaking.minimum_words_to_interrupt,
        "min_interruption_duration": max(
            0.1, settings.advanced.bot_speaking.minimum_user_speech_duration_ms / 1000.0
        ),
        "noise_reduction": settings.stt.noise_reduction,
        "ambient_sound": settings.ambient_sound.model_dump(),
        "user_idle": {
            "threshold_sec": DEFAULT_USER_IDLE_THRESHOLD_SEC,
            "max_prompts": 1,
            "prompt_message": idle_defaults["prompt_message"],
            "end_after_max": True,
            "last_message": idle_defaults["last_message"],
        },
        "advanced_settings": {
            "noise_cancellation": settings.stt.noise_reduction,
            "listening_speech_confidence": settings.advanced.listening.speech_detection_confidence,
            "listening_start_ms": settings.advanced.listening.user_speech_start_duration_ms,
            "listening_min_ms": settings.advanced.listening.minimum_user_speech_duration_ms,
            "bot_speech_confidence": settings.advanced.bot_speaking.speech_detection_confidence,
            "bot_start_ms": settings.advanced.bot_speaking.user_speech_start_duration_ms,
            "bot_min_ms": settings.advanced.bot_speaking.minimum_user_speech_duration_ms,
        },
        "conversational_flow_sections": flow_sections,
        "assistant_settings": settings.model_dump(),
    }


def load_main_assistant_settings() -> AssistantSettings:
    return settings_from_flat_config(read_raw_config(), assistant_id="main")


def save_main_assistant_settings(settings: AssistantSettings) -> dict[str, Any]:
    updates = flat_updates_from_settings(settings)
    saved = write_full_config(updates)
    touch_reload_signal()
    return saved


def touch_reload_signal() -> None:
    RELOAD_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
    RELOAD_SIGNAL.write_text(json.dumps({"ts": __import__("time").time()}), encoding="utf-8")


def merge_settings_into_runtime(config: dict[str, Any]) -> dict[str, Any]:
    """Ensure runtime dict reflects assistant_settings block if present."""
    if not isinstance(config.get("assistant_settings"), dict):
        return config
    preserved_idle = dict(config.get("user_idle") or {}) if isinstance(config.get("user_idle"), dict) else {}
    preserved_ending = dict(config.get("call_ending") or {}) if isinstance(config.get("call_ending"), dict) else {}
    flat = flat_updates_from_settings(
        AssistantSettings.model_validate({**config["assistant_settings"], "assistant_id": "main"})
    )
    merged = {**config}
    for k, v in flat.items():
        if k == "assistant_settings":
            merged[k] = v
        elif isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    if preserved_idle:
        merged_idle = dict(merged.get("user_idle") or {})
        for key in ("prompt_message", "last_message", "threshold_sec", "max_prompts", "end_after_max"):
            val = preserved_idle.get(key)
            if val not in (None, ""):
                merged_idle[key] = val
        merged["user_idle"] = merged_idle
    if preserved_ending.get("message"):
        merged_ending = dict(merged.get("call_ending") or {})
        merged_ending["message"] = preserved_ending["message"]
        merged["call_ending"] = merged_ending
    return merged


def catalog_payload() -> dict[str, Any]:
    from background_audio import ambient_sound_catalog

    return {
        "languages": LANGUAGE_OPTIONS,
        "voices": _voice_catalog(),
        "llm_providers": [
            {"id": "gemini", "label": "Google (Gemini)", "models": ["gemini-2.5-flash"]},
            {"id": "openai", "label": "OpenAI", "models": ["gpt-4o", "gpt-4o-mini"]},
            {"id": "groq", "label": "Groq", "models": ["llama-3.3-70b-versatile"]},
        ],
        "stt_providers": [
            {"id": "soniox", "label": "Soniox", "models": ["stt-rt-v5"]},
            {"id": "sarvam", "label": "Sarvam", "models": ["saaras:v3"]},
            {"id": "deepgram", "label": "Deepgram", "models": ["nova-2-general"]},
        ],
        "ambient_sounds": ambient_sound_catalog(),
        "ambient_volume": {"min": 0.08, "max": 0.18, "default": 0.12, "step": 0.01},
        "tts_providers": [
            {"id": "cartesia", "label": "Cartesia Sonic", "models": ["sonic-3.5"]},
            {"id": "gemini", "label": "Gemini Flash TTS", "models": ["gemini-3.1-flash-tts-preview"]},
            {"id": "sarvam", "label": "Sarvam Bulbul", "models": ["bulbul:v3"]},
            {"id": "elevenlabs", "label": "ElevenLabs", "models": ["eleven_turbo_v2_5"]},
        ],
    }