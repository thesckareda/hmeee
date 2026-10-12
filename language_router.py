"""
Language detection and routing for STT/TTS/LLM.

Saarika auto-detect on first audio; per-client config override takes priority.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("inbound-voice-agent")

# Sarvam Bulbul v3 speakers (v2 names like ananya/malar are NOT valid).
SARVAM_V3_SPEAKERS = frozenset({
    "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh",
    "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran",
    "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan",
    "sumit", "roopa", "kabir", "aayan", "shubh", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
})

DEFAULT_SARVAM_VOICE = "ashutosh"

# Legacy v2 / UI voice names → Bulbul v3
_LEGACY_VOICE_MAP: dict[str, str] = {
    "ananya": "ashutosh",
    "amartya": "shubh",
    "arvind": "shubh",
    "malar": "kavya",
}

LANGUAGE_CONFIG: dict[str, dict[str, Any]] = {
    "hi": {"stt_lang": "hi-IN", "tts_voice": "shubh", "llm_locale": "hi", "rate": 0.90},
    "hi-en": {"stt_lang": "hi-en", "tts_voice": "shubh", "llm_locale": "hi-en", "rate": 0.92},
    "ta": {"stt_lang": "ta-IN", "tts_voice": "kavitha", "llm_locale": "ta", "rate": 0.88},
    "te": {"stt_lang": "te-IN", "tts_voice": "kavya", "llm_locale": "te", "rate": 0.88},
    "bn": {"stt_lang": "bn-IN", "tts_voice": "priya", "llm_locale": "bn", "rate": 0.90},
    "mr": {"stt_lang": "mr-IN", "tts_voice": "shubh", "llm_locale": "mr", "rate": 0.90},
    "gu": {"stt_lang": "gu-IN", "tts_voice": "priya", "llm_locale": "gu", "rate": 0.90},
    "kn": {"stt_lang": "kn-IN", "tts_voice": "priya", "llm_locale": "kn", "rate": 0.88},
    "ml": {"stt_lang": "ml-IN", "tts_voice": "priya", "llm_locale": "ml", "rate": 0.88},
    "pa": {"stt_lang": "pa-IN", "tts_voice": "priya", "llm_locale": "pa", "rate": 0.90},
    "en": {"stt_lang": "en-IN", "tts_voice": "ashutosh", "llm_locale": "en", "rate": 0.95},
    "en-in": {"stt_lang": "en-IN", "tts_voice": "ashutosh", "llm_locale": "en-in", "rate": 0.95},
}

DEFAULT_LANGUAGE = "te"
ALLOWED_TURN_LANGUAGES = frozenset({"te", "en-in"})
TINGLISH_PRESET = "tinglish"
HME_PRESET = "hme"
HI_EN_MR_PRESET = HME_PRESET  # backward-compatible alias
HI_EN_MR_LANGUAGES = frozenset({"hi", "en-in", "mr"})

try:
    from lang_presets_store import TINGLISH_DIRECTIVE as TELUGU_ENGLISH_DIRECTIVE
except ImportError:
    TELUGU_ENGLISH_DIRECTIVE = (
        "LANGUAGE: Natural Telugish ONLY — spoken Telugu + English. "
        "Never pure literary Telugu, Hindi, Tamil, or Marathi.\n"
        "- Default: natural SPOKEN Telugu (తెలుగు script) with English mixed in — "
        "like a real consultant on a live phone call.\n"
        "- Use ENGLISH for: numbers (1.2 Cr, 3 BHK, 1500 sqft), budgets, dates/times, "
        "place names (Kokapet, Gachibowli, Financial District), property terms "
        "(site visit, EMI, parking, floor, possession, loan, budget, premium, investment), "
        "and VK Realty.\n"
        "- Use Telugu for: greetings, connectors (andi, garu, avunu, sare), questions, empathy — "
        "NOT for numbers, BHK, Crore, or property jargon.\n"
        "- Example: 'Kokapet lo 3 BHK flat price 1.2 Crores nunchi start avutundi andi. "
        "Site visit schedule cheddama?'\n"
        "- English-only caller → clear Indian English with the same English property terms.\n"
        "- Never sound like a newsreader or formal textbook Telugu.\n"
        "- Keep replies 1-2 short spoken sentences."
    )

try:
    from lang_presets_store import HME_DIRECTIVE as HI_EN_MR_DIRECTIVE
except ImportError:
    HI_EN_MR_DIRECTIVE = (
        "LANGUAGE: Natural Hinglish, Marathi+English, or Indian English ONLY. "
        "Never pure formal Hindi or pure formal Marathi. Never Telugu, Tamil, or other languages.\n"
        "- Hindi caller → SPOKEN Hinglish: Hindi connectors in Devanagari mixed with English (Latin) for "
        "numbers, property jargon, budgets, and place names. Sound like a real consultant on a phone call.\n"
        "- Use ENGLISH for: numbers (1.2 Cr, 3 BHK, 1500 sqft), budgets, dates/times, place names "
        "(Thane, Kanjurmarg, Mulund, Zirakpur), property terms (site visit, EMI, parking, floor, possession, "
        "loan, budget, premium, investment, self-use), and VK Realty.\n"
        "- Use Hindi (Devanagari) for: greetings, connectors (जी, हाँ, बिल्कुल), empathy, and everyday "
        "spoken phrases — NOT for numbers, BHK, Crore, or property terms.\n"
        "- Marathi caller → spoken Marathi+English: Marathi connectors in Devanagari + same English terms.\n"
        "- English caller → clear Indian English with English numbers and property terms.\n"
        "- Never sound like a textbook or news bulletin.\n"
        "- Keep replies 1-2 short spoken sentences."
    )

_PRESET_ALIASES: dict[str, str] = {
    "english": "english",
    TINGLISH_PRESET: TINGLISH_PRESET,
    "telugu": TINGLISH_PRESET,
    "telugish": TINGLISH_PRESET,
    "multilingual": TINGLISH_PRESET,
    HME_PRESET: HME_PRESET,
    "hi_en_mr": HME_PRESET,
    "hindi_english_marathi": HME_PRESET,
    "hinglish": HME_PRESET,
    "hindi": HME_PRESET,
    "marathi": HME_PRESET,
    "tamil": TINGLISH_PRESET,
    "bengali": TINGLISH_PRESET,
    "gujarati": TINGLISH_PRESET,
    "kannada": TINGLISH_PRESET,
    "malayalam": TINGLISH_PRESET,
}

_PRESET_TURN: dict[str, dict[str, Any]] = {
    TINGLISH_PRESET: {"allowed": frozenset({"te", "en-in"}), "default": "te"},
    "english": {"allowed": frozenset({"en-in"}), "default": "en-in"},
    HME_PRESET: {"allowed": HI_EN_MR_LANGUAGES, "default": "hi"},
}

_MARATHI_HINT_RE = re.compile(
    r"(आहे|नक्की|मी\s|तुम्ही|काय|मदत|शोध|करू|घ्या|बोलतोय|VK Realty कडून)"
)


def resolve_lang_preset(lang_preset: str | None = None, live_config: dict | None = None) -> str:
    """Normalize preset id; infer hme/tinglish from languages list when set."""
    live_config = live_config or {}
    langs = live_config.get("languages")
    if isinstance(langs, list) and langs:
        normalized = {_normalize_lang_token(str(x)) for x in langs if str(x).strip()}
        if HI_EN_MR_LANGUAGES.issubset(normalized):
            return HME_PRESET
        if "te" in normalized:
            return TINGLISH_PRESET
    raw = str(lang_preset or live_config.get("lang_preset") or TINGLISH_PRESET).lower().strip()
    return _PRESET_ALIASES.get(raw, TINGLISH_PRESET)


def _normalize_lang_token(code: str) -> str:
    lowered = (code or "").strip().lower().replace("_", "-")
    if lowered in ("en", "english", "en-us"):
        return "en-in"
    if lowered in ("hindi", "hi-in"):
        return "hi"
    if lowered in ("marathi", "mr-in"):
        return "mr"
    return lowered


def get_allowed_turn_languages(lang_preset: str | None = None, live_config: dict | None = None) -> frozenset[str]:
    key = resolve_lang_preset(lang_preset, live_config)
    return _PRESET_TURN.get(key, _PRESET_TURN[TINGLISH_PRESET])["allowed"]


def get_default_turn_language(lang_preset: str | None = None, live_config: dict | None = None) -> str:
    key = resolve_lang_preset(lang_preset, live_config)
    return _PRESET_TURN.get(key, _PRESET_TURN[TINGLISH_PRESET])["default"]


def default_language_for_config(live_config: dict | None = None) -> str:
    """Preset-aware default — hi for hme, te for tinglish, en-in for english."""
    cfg = live_config or {}
    return get_default_turn_language(cfg.get("lang_preset"), cfg)


DEFAULT_USER_IDLE_THRESHOLD_SEC = 30

IDLE_MESSAGES_BY_PRESET: dict[str, dict[str, str]] = {
    HME_PRESET: {
        "prompt_message": "Hello, sun pa rahe hain?",
        "last_message": "Koi baat nahi, jab time ho to dubara call kar lena. Thank you!",
    },
    TINGLISH_PRESET: {
        "prompt_message": "Hello andi, line lo unnaara?",
        "last_message": "Parledu andi, time unte malli call cheyandi. Thank you!",
    },
    "english": {
        "prompt_message": "Are you still there?",
        "last_message": "Please call back anytime. Goodbye!",
    },
}


def idle_messages_for_preset(
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> dict[str, str]:
    preset = resolve_lang_preset(lang_preset, live_config)
    try:
        from lang_presets_store import get_idle_messages

        stored = get_idle_messages(preset)
        if stored:
            return stored
    except Exception:
        pass
    return IDLE_MESSAGES_BY_PRESET.get(preset, IDLE_MESSAGES_BY_PRESET[TINGLISH_PRESET])


def interrupt_ack_phrases(
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    preset = resolve_lang_preset(lang_preset, live_config)
    try:
        from lang_presets_store import get_interrupt_ack

        stored = get_interrupt_ack(preset)
        if stored:
            return stored
    except Exception:
        pass
    if preset == HME_PRESET:
        return "जी or haan"
    if preset == "english":
        return "okay or sure"
    return "avunu andi or sare"


def get_language_directive(
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    preset = resolve_lang_preset(lang_preset, live_config)
    try:
        from lang_presets_store import get_language_directive as _store_directive

        stored = _store_directive(preset)
        if stored:
            return stored
    except Exception:
        pass
    if preset == HME_PRESET:
        return HI_EN_MR_DIRECTIVE
    if preset == "english":
        return (
            "LANGUAGE: Clear Indian English only. Use English for all numbers and property terms. "
            "Do not use Hindi, Telugu, or Marathi unless the caller switches."
        )
    return TELUGU_ENGLISH_DIRECTIVE


def normalize_sarvam_voice(voice: str | None, *, fallback: str = DEFAULT_SARVAM_VOICE) -> str:
    """Map legacy/invalid speaker ids to a Bulbul v3 voice."""
    raw = (voice or "").strip().lower()
    if not raw:
        return fallback
    if raw in SARVAM_V3_SPEAKERS:
        return raw
    mapped = _LEGACY_VOICE_MAP.get(raw)
    if mapped:
        return mapped
    logger.warning("[TTS] Unknown Sarvam voice '%s' — using %s", voice, fallback)
    return fallback

# Map internal codes → Sarvam TTS API codes (hi-en → hi-IN, te → te-IN, etc.)
SARVAM_TTS_LANG_MAP: dict[str, str] = {
    "hi": "hi-IN",
    "hi-en": "hi-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "bn": "bn-IN",
    "mr": "mr-IN",
    "gu": "gu-IN",
    "kn": "kn-IN",
    "ml": "ml-IN",
    "pa": "pa-IN",
    "en": "en-IN",
    "en-in": "en-IN",
}

LANGUAGE_OVERLAYS: dict[str, str] = {
    "hi": (
        "Reply in natural spoken Hinglish — Hindi connectors in Devanagari with English for numbers, "
        "BHK, Crore, sqft, EMI, site visit, place names, and property terms. Never pure formal Hindi."
    ),
    "hi-en": (
        "Reply in natural Hinglish — Hindi sentence structure with English technical terms. "
        "Example: 'Is property ka price 1.8 Cr hai, aur parking bhi included hai.'"
    ),
    "ta": "Reply in Tamil. Use English only for property terms (BHK, sq ft, EMI).",
    "te": TELUGU_ENGLISH_DIRECTIVE,
    "en-in": (
        "Reply in clear Indian English only. Use English for all numbers and property terms. "
        "Do not use Hindi, Marathi, or Telugu unless the caller switches to that language."
    ),
    "bn": "Reply in Bengali. Use English only for property terms.",
    "mr": (
        "Reply in natural spoken Marathi+English — Marathi connectors in Devanagari with English for "
        "numbers, BHK, Crore, place names, and property terms. Never pure formal Marathi."
    ),
    "gu": "Reply in Gujarati. Use English only for property terms.",
    "kn": "Reply in Kannada. Use English only for property terms.",
    "ml": "Reply in Malayalam. Use English only for property terms.",
    "pa": "Reply in Punjabi. Use English only for property terms.",
    "en": (
        "Reply in clear Indian English only. Use English for numbers and property terms. "
        "Never use Hindi."
    ),
}


def get_language_config(language_code: str, *, lang_preset: str | None = None, live_config: dict | None = None) -> dict[str, Any]:
    """Get STT/TTS/LLM config for a language. Falls back to preset default."""
    code = clamp_turn_language(language_code, lang_preset=lang_preset, live_config=live_config)
    default = get_default_turn_language(lang_preset, live_config)
    return LANGUAGE_CONFIG.get(code, LANGUAGE_CONFIG.get(default, LANGUAGE_CONFIG[DEFAULT_LANGUAGE]))


def detect_language_from_config(live_config: dict) -> str:
    """
    Per-client language config takes priority over auto-detect.
    Returns language code or 'auto' to trigger Saarika auto-detect.
    """
    configured = str(live_config.get("language", "")).lower().strip()
    if configured in ("multilingual", "telugish", "auto"):
        return DEFAULT_LANGUAGE
    if configured and configured in LANGUAGE_CONFIG:
        return clamp_turn_language(configured)
    preset = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    if preset == HI_EN_MR_PRESET:
        return get_default_turn_language(preset, live_config)
    preset_map = {
        "english": "en-in",
        TINGLISH_PRESET: "te",
    }
    if preset in preset_map:
        return clamp_turn_language(preset_map[preset], lang_preset=preset, live_config=live_config)
    return get_default_turn_language(preset, live_config)


def get_language_overlay(language_code: str, *, lang_preset: str | None = None, live_config: dict | None = None) -> str:
    preset = resolve_lang_preset(lang_preset, live_config)
    if preset == HI_EN_MR_PRESET:
        return get_language_directive(preset, live_config)
    if preset == TINGLISH_PRESET:
        code = clamp_turn_language(language_code, lang_preset=preset, live_config=live_config)
        if code == "en-in":
            return LANGUAGE_OVERLAYS["en-in"]
        return get_language_directive(preset, live_config)
    if preset == "english":
        return get_language_directive(preset, live_config)
    code = clamp_turn_language(language_code, lang_preset=preset, live_config=live_config)
    return LANGUAGE_OVERLAYS.get(code, TELUGU_ENGLISH_DIRECTIVE)


def clamp_turn_language(
    language_code: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Clamp runtime language to the active preset's allowed set."""
    preset = resolve_lang_preset(lang_preset, live_config)
    allowed = get_allowed_turn_languages(preset, live_config)
    default = get_default_turn_language(preset, live_config)
    code = _normalize_lang_token(language_code or "")
    if code in allowed:
        return code
    if code in ("en", "english") and "en-in" in allowed:
        return "en-in"
    if code in ("te", "te-in", "telugu", "telugish", "multilingual") and "te" in allowed:
        return "te"
    if code in ("hi", "hi-en", "hinglish", "hindi") and "hi" in allowed:
        return "hi"
    if code in ("mr", "marathi") and "mr" in allowed:
        return "mr"
    if code in LANGUAGE_CONFIG:
        mapped = normalize_detected_language(code)
        if mapped in allowed:
            return mapped
    return default


_SCRIPT_DETECTORS: list[tuple[str, re.Pattern[str]]] = [
    ("te", re.compile(r"[\u0C00-\u0C7F]")),
    ("ta", re.compile(r"[\u0B80-\u0BFF]")),
    ("hi", re.compile(r"[\u0900-\u097F]")),
    ("bn", re.compile(r"[\u0980-\u09FF]")),
    ("gu", re.compile(r"[\u0A80-\u0AFF]")),
    ("kn", re.compile(r"[\u0C80-\u0CFF]")),
    ("ml", re.compile(r"[\u0D00-\u0D7F]")),
]


def detect_language_from_text(
    text: str,
    *,
    fallback: str = DEFAULT_LANGUAGE,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Script/heuristic turn language detection within the active preset."""
    if not (text or "").strip():
        return clamp_turn_language(fallback, lang_preset=lang_preset, live_config=live_config)
    preset = resolve_lang_preset(lang_preset, live_config)
    allowed = get_allowed_turn_languages(preset, live_config)
    if "te" in allowed and _SCRIPT_DETECTORS[0][1].search(text):
        return "te"
    if re.search(r"[\u0900-\u097F]", text):
        if "mr" in allowed and _MARATHI_HINT_RE.search(text):
            return "mr"
        if "hi" in allowed:
            return "hi"
        if "te" in allowed:
            return "te"
    if re.search(r"[A-Za-z]", text) and "en-in" in allowed:
        return "en-in"
    return clamp_turn_language(fallback, lang_preset=preset, live_config=live_config)


def resolve_turn_language(
    transcript: str,
    *,
    stt_language: str | None = None,
    fallback: str = DEFAULT_LANGUAGE,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Resolve per-turn language within the active preset."""
    preset = resolve_lang_preset(lang_preset, live_config)
    allowed = get_allowed_turn_languages(preset, live_config)
    text = (transcript or "").strip()
    if "te" in allowed and _SCRIPT_DETECTORS[0][1].search(text):
        return "te"
    if re.search(r"[\u0900-\u097F]", text):
        if "mr" in allowed and _MARATHI_HINT_RE.search(text):
            return "mr"
        if "hi" in allowed:
            return "hi"
    if re.search(r"[A-Za-z]", text) and "en-in" in allowed:
        return "en-in"
    if stt_language:
        hint = normalize_detected_language(stt_language)
        clamped = clamp_turn_language(hint, lang_preset=preset, live_config=live_config)
        if clamped in allowed:
            return clamped
    return clamp_turn_language(fallback, lang_preset=preset, live_config=live_config)


def to_sarvam_tts_lang(internal_code: str, *, lang_preset: str | None = None, live_config: dict | None = None) -> str:
    """Map internal language code to Sarvam TTS API format."""
    code = clamp_turn_language(internal_code, lang_preset=lang_preset, live_config=live_config)
    return SARVAM_TTS_LANG_MAP.get(code, "te-IN")


SONIOX_LANG_HINTS_MAP: dict[str, list[str]] = {
    "hi": ["hi"],
    "hi-en": ["te", "en", "hi"],
    "hi-IN": ["hi"],
    "en": ["en"],
    "en-in": ["en"],
    "en-IN": ["en"],
    "en-US": ["en"],
    "ta": ["ta", "en"],
    "ta-IN": ["ta", "en"],
    "te": ["te", "en"],
    "te-IN": ["te", "en"],
    "bn": ["bn", "en"],
    "bn-IN": ["bn", "en"],
    "mr": ["mr", "en"],
    "mr-IN": ["mr", "en"],
    "gu": ["gu", "en"],
    "gu-IN": ["gu", "en"],
    "kn": ["kn", "en"],
    "kn-IN": ["kn", "en"],
    "ml": ["ml", "en"],
    "ml-IN": ["ml", "en"],
    "pa": ["pa", "en"],
    "pa-IN": ["pa", "en"],
    "unknown": ["hi", "mr", "en"],
    "auto": ["hi", "mr", "en"],
    "multilingual": ["hi", "mr", "en"],
    "hme": ["hi", "mr", "en"],
    HI_EN_MR_PRESET: ["hi", "mr", "en"],
}


def to_soniox_language_hints(internal_code: str) -> list[str]:
    """Map internal/router language code to Soniox ISO 639-1 language hints."""
    code = (internal_code or "").strip()
    if not code:
        return SONIOX_LANG_HINTS_MAP["unknown"]
    lowered = code.lower()
    if lowered in SONIOX_LANG_HINTS_MAP:
        return SONIOX_LANG_HINTS_MAP[lowered]
    if code in SONIOX_LANG_HINTS_MAP:
        return SONIOX_LANG_HINTS_MAP[code]
    if "-IN" in code:
        base = code.split("-")[0].lower()
        return [base, "en"] if base != "en" else ["en"]
    if "-" in code:
        parts = [p.lower() for p in code.split("-") if p]
        return parts[:2] if parts else SONIOX_LANG_HINTS_MAP["unknown"]
    return [lowered]


def to_sarvam_stt_lang(
    internal_code: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Map internal/router language code to Sarvam STT WebSocket language_code."""
    code = (internal_code or "").strip()
    preset = resolve_lang_preset(lang_preset, live_config)
    if not code or code.lower() in ("unknown", "auto", "multilingual"):
        if preset == HI_EN_MR_PRESET:
            return "hi-IN"
        return "te-IN"
    clamped = clamp_turn_language(code, lang_preset=preset, live_config=live_config)
    return str(LANGUAGE_CONFIG[clamped]["stt_lang"])


def to_sarvam_batch_stt_lang(
    internal_code: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Map internal/router language code to LiveKit sarvam.STT (saaras:v3) codes."""
    code = (internal_code or "").strip()
    preset = resolve_lang_preset(lang_preset, live_config)
    if not code or code.lower() in ("unknown", "auto", "multilingual"):
        if preset == HI_EN_MR_PRESET:
            return "hi-IN"
        return "te-IN"
    return to_sarvam_tts_lang(code, lang_preset=preset, live_config=live_config)


def normalize_detected_language(code: str | None) -> str:
    """Map Sarvam/API language codes to internal router codes."""
    if not code:
        return DEFAULT_LANGUAGE
    lowered = code.lower().strip()
    if lowered in LANGUAGE_CONFIG:
        return lowered
    mapping = {
        "hi-in": "hi",
        "ta-in": "ta",
        "te-in": "te",
        "bn-in": "bn",
        "mr-in": "mr",
        "gu-in": "gu",
        "kn-in": "kn",
        "ml-in": "ml",
        "pa-in": "pa",
        "en-in": "en-in",
        "en-us": "en",
        "unknown": DEFAULT_LANGUAGE,
        "auto": DEFAULT_LANGUAGE,
    }
    return mapping.get(lowered, DEFAULT_LANGUAGE)


async def detect_language_from_audio(first_audio_chunk: bytes, api_key: str) -> str:
    """
    Send first 500ms of audio to Saarika with language_code='auto'.
    Returns detected language code or DEFAULT_LANGUAGE on failure.
    """
    if not first_audio_chunk or not api_key:
        return DEFAULT_LANGUAGE
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.post(
                "https://api.sarvam.ai/speech-to-text",
                headers={"api-subscription-key": api_key},
                json={
                    "audio": base64.b64encode(first_audio_chunk).decode(),
                    "language_code": "auto",
                    "model": "saaras:v3",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return normalize_detected_language(data.get("language_code"))
    except Exception as e:
        logger.warning("[LANG] Audio language detect failed: %s", e)
        return DEFAULT_LANGUAGE


def apply_language_to_config(live_config: dict, language_code: str) -> dict:
    """Apply detected/configured language to live_config STT/TTS fields."""
    live_config = dict(live_config)
    lang_preset = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    language_code = clamp_turn_language(language_code, lang_preset=lang_preset, live_config=live_config)
    cfg = get_language_config(language_code, lang_preset=lang_preset, live_config=live_config)
    live_config["detected_language"] = language_code
    live_config["lang_preset"] = lang_preset

    # Respect user overrides from config/DB if present and not empty
    if not live_config.get("stt_language") or live_config.get("stt_language") == "unknown":
        live_config["stt_language"] = cfg["stt_lang"]
    if not live_config.get("tts_language") or live_config.get("tts_language") == "unknown":
        live_config["tts_language"] = cfg["stt_lang"]
    tts_provider = str(live_config.get("tts_provider", "gemini")).strip().lower()
    raw_voice = live_config.get("tts_voice") or cfg["tts_voice"]
    if tts_provider == "gemini":
        from gemini_tts import DEFAULT_GEMINI_VOICE, normalize_gemini_voice

        live_config["tts_voice"] = normalize_gemini_voice(raw_voice or DEFAULT_GEMINI_VOICE)
    elif tts_provider == "cartesia":
        from cartesia_tts import cartesia_voice_for_turn_language, resolve_cartesia_voice

        if lang_preset == HI_EN_MR_PRESET:
            live_config["tts_voice"] = cartesia_voice_for_turn_language(language_code, live_config=live_config)
        else:
            live_config["tts_voice"] = resolve_cartesia_voice(raw_voice or "nivas")
    else:
        live_config["tts_voice"] = normalize_sarvam_voice(raw_voice)
    if not live_config.get("tts_speech_rate"):
        live_config["tts_speech_rate"] = cfg["rate"]

    live_config["stt_language_sarvam"] = to_sarvam_stt_lang(
        live_config["stt_language"], lang_preset=lang_preset, live_config=live_config
    )
    live_config["stt_language_sarvam_batch"] = to_sarvam_batch_stt_lang(
        live_config["stt_language"], lang_preset=lang_preset, live_config=live_config
    )
    soniox_key = lang_preset if lang_preset == HI_EN_MR_PRESET else live_config["stt_language"]
    live_config["stt_language_soniox"] = to_soniox_language_hints(soniox_key)
    live_config["tts_language_sarvam"] = to_sarvam_tts_lang(
        live_config["tts_language"], lang_preset=lang_preset, live_config=live_config
    )

    overlay = get_language_overlay(language_code, lang_preset=lang_preset, live_config=live_config)
    base = live_config.get("agent_instructions", "")
    if overlay and overlay not in base:
        live_config["agent_instructions"] = f"{base}\n\n[LANGUAGE OVERLAY]\n{overlay}"
    return live_config