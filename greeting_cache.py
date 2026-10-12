"""
Pre-baked greeting and filler audio cache.

Presets: tinglish (Telugu + English), hme (Hindi + English + Marathi).
Prewarm uses Cartesia only. Disk cache accepts prior Cartesia entries.

Time-of-day greeting variants (morning / afternoon / evening) rotate naturally.
Per-lead name pronunciation hints and PCM are cached by phone number.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Literal

from datetime_utils import now_ist

logger = logging.getLogger("inbound-voice-agent")

DISK_CACHE_DIR = Path(__file__).resolve().parent / "data" / "greeting_pcm_cache"
NAME_PRONUNCIATION_DIR = DISK_CACHE_DIR / "name_pronunciation"

TimeBucket = Literal["morning", "afternoon", "evening", "night"]

GREETING_VARIANTS: dict[str, list[str]] = {
    "te": [
        "Namaskaram andi! Nenu Arjun, VK Realty nunchi matladuthunnanu. Mee property search lo ela sahayam cheyagalanu?",
        "Hello andi! Nenu Arjun, VK Realty nunchi. Mee property search ki ela help cheyagalanu?",
    ],
    "en-in": [
        "Hello! This is Arjun from VK Realty. How can I help you with your property search today?",
        "Hi there! Arjun here from VK Realty — what can I help you find today?",
    ],
}

HI_EN_MR_GREETING_VARIANTS: dict[str, list[str]] = {
    "hi": [
        "नमस्ते! मैं अर्जुन बोल रहा हूँ, VK Realty से। आपकी property search में मैं कैसे मदद कर सकता हूँ?",
        "नमस्कार! Arjun bol raha hoon VK Realty se — aapki property search mein kaise help karun?",
    ],
    "en-in": [
        "Hello! This is Arjun from VK Realty. How can I help you with your property search today?",
        "Good to connect! I'm Arjun from VK Realty — how can I help with your property search?",
    ],
    "mr": [
        "नमस्कार! मी अर्जुन, VK Realty कडून बोलतोय. तुमच्या property शोधात मी कशी मदत करू शकतो?",
        "नमस्कार! Arjun here, VK Realty kadun — tumchya property shodhat kashi madat karu?",
    ],
}

# Extra greetings keyed by preset → lang → time bucket (merged with base variants).
TIME_GREETING_VARIANTS: dict[str, dict[str, dict[str, list[str]]]] = {
    "tinglish": {
        "te": {
            "morning": [
                "Shubodayam andi! Nenu Arjun, VK Realty nunchi. Mee property search lo ela sahayam cheyagalanu?",
            ],
            "afternoon": [
                "Namaskaram andi! Nenu Arjun, VK Realty nunchi matladuthunnanu. Emi property chustunnaru?",
            ],
            "evening": [
                "Namaskaram andi! Nenu Arjun, VK Realty nunchi. Evening ki kuda help cheyagalanu — emi chustunnaru?",
            ],
        },
        "en-in": {
            "morning": [
                "Good morning! This is Arjun from VK Realty. How can I help with your property search today?",
            ],
            "afternoon": [
                "Good afternoon! Arjun here from VK Realty — what property are you exploring?",
            ],
            "evening": [
                "Good evening! This is Arjun from VK Realty. How can I help you this evening?",
            ],
        },
    },
    "hme": {
        "hi": {
            "morning": [
                "सुप्रभात! मैं अर्जुन, VK Realty से। आपकी property search में कैसे मदद करूँ?",
            ],
            "afternoon": [
                "नमस्ते! दोपहर में Arjun, VK Realty se — property search mein kaise help karun?",
            ],
            "evening": [
                "शुभ संध्या! मैं अर्जुन, VK Realty से। आज शाम property search में कैसे मदद करूँ?",
            ],
        },
        "en-in": {
            "morning": [
                "Good morning! Arjun from VK Realty — how can I help with your property search?",
            ],
            "afternoon": [
                "Good afternoon! This is Arjun from VK Realty. What are you looking for today?",
            ],
            "evening": [
                "Good evening! Arjun here from VK Realty — how can I assist you tonight?",
            ],
        },
        "mr": {
            "morning": [
                "सुप्रभात! मी अर्जुन, VK Realty कडून — property शोधात कशी मदत करू?",
            ],
            "afternoon": [
                "नमस्कार! Arjun, VK Realty kadun — duparichya veli property shodhat madat karu?",
            ],
            "evening": [
                "शुभ संध्या! मी अर्जुन, VK Realty कडून — संध्याकाळी property शोधात कशी मदत करू?",
            ],
        },
    },
}

_name_phonetic_cache: dict[str, str] = {}
_name_pcm_cache: dict[str, bytes] = {}

FILLER_VARIANTS: dict[str, list[str]] = {
    "te": [
        "Sare andi",
        "Avunu andi",
    ],
    "en-in": [
        "Sure",
        "One moment",
    ],
}

HI_EN_MR_FILLER_VARIANTS: dict[str, list[str]] = {
    "hi": [
        "जी बिल्कुल",
        "एक पल",
    ],
    "en-in": [
        "Sure",
        "One moment",
    ],
    "mr": [
        "नक्की",
        "एक क्षण",
    ],
}

PRESET_GREETING_SETS: dict[str, list[str]] = {
    "tinglish": ["te", "en-in"],
    "hme": ["hi", "en-in", "mr"],
    "english": ["en-in"],
}

_greeting_cache: dict[str, dict[str, bytes]] = {}
_filler_cache: dict[str, list[bytes]] = {}
_session_cache: dict[str, bytes] = {}


def clear_prewarm_caches(*, clear_disk: bool = True) -> None:
    """Drop in-memory prewarm caches (e.g. when lang preset or voice changes)."""
    _greeting_cache.clear()
    _filler_cache.clear()
    _session_cache.clear()
    if clear_disk:
        clear_disk_pcm_cache()


def clear_disk_pcm_cache() -> int:
    """Remove on-disk greeting/filler/session PCM blobs (stale voice ids)."""
    removed = 0
    try:
        if not DISK_CACHE_DIR.is_dir():
            return 0
        for path in DISK_CACHE_DIR.glob("*.pcm"):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("[PREBAKE] Disk cache delete failed %s: %s", path, exc)
    except OSError as exc:
        logger.debug("[PREBAKE] Disk cache clear failed: %s", exc)
    if removed:
        logger.info("[PREBAKE] Cleared %d stale disk PCM cache file(s)", removed)
    return removed


def _disk_cache_key(*, provider: str, voice: str, model: str, text: str) -> str:
    raw = f"{provider}|{voice}|{model}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _load_disk_pcm(cache_key: str) -> bytes | None:
    path = DISK_CACHE_DIR / f"{cache_key}.pcm"
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path.read_bytes()
    except OSError as exc:
        logger.debug("[PREBAKE] Disk cache read failed %s: %s", path, exc)
    return None


def _load_disk_pcm_any(
    text: str,
    *,
    gemini_voice: str,
    gemini_model: str,
    sarvam_voice: str = "kavya",
    cartesia_voice: str = "",
    cartesia_model: str = "",
    prefer_provider: str | None = None,
) -> bytes | None:
    """Load cached PCM from prior Gemini, Sarvam, or Cartesia prebake."""
    preferred = (prefer_provider or "").strip().lower()
    cartesia_keys = []
    if cartesia_voice:
        cartesia_keys.append(
            _disk_cache_key(provider="cartesia", voice=cartesia_voice, model=cartesia_model, text=text)
        )
    gemini_keys = [
        _disk_cache_key(provider="gemini", voice=gemini_voice, model=gemini_model, text=text),
        _disk_cache_key(provider="gemini", voice="Rasalgethi", model=gemini_model, text=text),
    ]
    sarvam_keys = [
        _disk_cache_key(provider="sarvam", voice=sarvam_voice, model="", text=text),
        _disk_cache_key(provider="sarvam", voice="kavya", model="", text=text),
    ]
    if preferred == "cartesia":
        # Never fall back to Gemini/Sarvam — wrong voice when Cartesia voice id changes.
        candidates = cartesia_keys
    elif preferred == "sarvam":
        candidates = sarvam_keys + cartesia_keys + gemini_keys
    else:
        candidates = gemini_keys + cartesia_keys + sarvam_keys
    seen: set[str] = set()
    for key in candidates:
        if key in seen:
            continue
        seen.add(key)
        pcm = _load_disk_pcm(key)
        if pcm:
            return pcm
    return None


def _save_disk_pcm(cache_key: str, pcm_bytes: bytes) -> None:
    if not pcm_bytes:
        return
    try:
        DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = DISK_CACHE_DIR / f"{cache_key}.pcm"
        path.write_bytes(pcm_bytes)
    except OSError as exc:
        logger.debug("[PREBAKE] Disk cache write failed: %s", exc)


def _store_pcm(*, lang: str, text: str, kind: str, pcm_bytes: bytes) -> None:
    if not pcm_bytes:
        return
    if kind == "greeting":
        if lang not in _greeting_cache:
            _greeting_cache[lang] = {}
        _greeting_cache[lang][text] = pcm_bytes
    elif kind == "session":
        _session_cache[text] = pcm_bytes
    else:
        if lang not in _filler_cache:
            _filler_cache[lang] = []
        _filler_cache[lang].append(pcm_bytes)


async def _prebake_one(
    api_key: str,
    lang: str,
    text: str,
    kind: str,
    *,
    provider: str,
    voice: str,
    model: str,
    disk_only: bool,
    gemini_voice: str,
    gemini_model: str,
    voice_per_lang: dict[str, str] | None = None,
) -> None:
    active_voice = (voice_per_lang or {}).get(lang) or voice
    cached = _load_disk_pcm_any(
        text,
        gemini_voice=gemini_voice,
        gemini_model=gemini_model,
        sarvam_voice=active_voice if provider == "sarvam" else "kavya",
        cartesia_voice=active_voice if provider == "cartesia" else "",
        cartesia_model=model if provider == "cartesia" else "",
        prefer_provider=provider,
    )
    if cached:
        _store_pcm(lang=lang, text=text, kind=kind, pcm_bytes=cached)
        logger.debug("[PREBAKE] Disk hit %s (%s)", kind, text[:40])
        return

    if disk_only or not api_key:
        return

    try:
        if provider == "cartesia":
            from cartesia_tts import synthesize_cartesia_pcm, to_cartesia_lang

            pcm_bytes = await synthesize_cartesia_pcm(
                text,
                api_key=api_key,
                voice_id=active_voice,
                language=to_cartesia_lang(lang if lang != "en-in" else "en-IN"),
                model=model,
            )
            cache_key = _disk_cache_key(provider="cartesia", voice=active_voice, model=model, text=text)
        elif provider == "sarvam":
            from language_router import to_sarvam_tts_lang
            from tts_stream import SarvamTTSStream, wav_to_pcm

            sarvam_lang = to_sarvam_tts_lang(lang)
            tts = SarvamTTSStream(
                api_key=api_key, language_code=sarvam_lang, voice_override=active_voice
            )
            wav_bytes = await tts._synthesise_raw(text)
            if wav_bytes is None:
                logger.warning("[PREBAKE] Sarvam returned None lang=%s text=%s", lang, text)
                return
            pcm_bytes = wav_to_pcm(wav_bytes)
            cache_key = _disk_cache_key(provider="sarvam", voice=active_voice, model="", text=text)
        elif provider == "gemini":
            from gemini_tts import iter_gemini_pcm_frames_with_keys

            pcm_parts: list[bytes] = []
            async for frame in iter_gemini_pcm_frames_with_keys(
                text,
                api_keys=[api_key] if api_key else [],
                model=gemini_model,
                voice=active_voice,
            ):
                pcm_parts.append(frame.data.tobytes())
            if not pcm_parts:
                logger.warning("[PREBAKE] Gemini returned no audio lang=%s text=%s", lang, text[:40])
                return
            pcm_bytes = b"".join(pcm_parts)
            cache_key = _disk_cache_key(
                provider="gemini",
                voice=active_voice,
                model=gemini_model,
                text=text,
            )
        else:
            logger.warning("[PREBAKE] Unsupported provider %s — skipping", provider)
            return
    except Exception as exc:
        logger.warning("[PREBAKE] TTS failed lang=%s text=%r: %s", lang, text[:60], exc)
        return

    _save_disk_pcm(cache_key, pcm_bytes)
    _store_pcm(lang=lang, text=text, kind=kind, pcm_bytes=pcm_bytes)


def resolve_time_bucket(hour: int | None = None) -> TimeBucket:
    """Map IST hour to morning / afternoon / evening / night."""
    if hour is None:
        hour = now_ist().hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _preset_time_key(lang_preset: str) -> str:
    from language_router import HME_PRESET, TINGLISH_PRESET, resolve_lang_preset

    preset = resolve_lang_preset(lang_preset)
    if preset == HME_PRESET:
        return "hme"
    if preset == TINGLISH_PRESET:
        return "tinglish"
    return "tinglish"


def greeting_texts_for_lang(
    lang: str,
    *,
    lang_preset: str = "telugu",
    time_bucket: TimeBucket | None = None,
) -> list[str]:
    """All greeting lines for a language — base variants plus time-of-day extras."""
    from language_router import HME_PRESET, resolve_lang_preset

    preset = resolve_lang_preset(lang_preset)
    base_map = HI_EN_MR_GREETING_VARIANTS if preset == HME_PRESET else GREETING_VARIANTS
    seen: set[str] = set()
    texts: list[str] = []

    def _add(line: str) -> None:
        cleaned = (line or "").strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            texts.append(cleaned)

    for line in base_map.get(lang, []):
        _add(line)

    bucket = time_bucket or resolve_time_bucket()
    preset_key = _preset_time_key(lang_preset)
    time_map = TIME_GREETING_VARIANTS.get(preset_key, {}).get(lang, {})
    for line in time_map.get(bucket, []):
        _add(line)
    # Night uses evening greetings when no dedicated night lines exist.
    if bucket == "night":
        for line in time_map.get("evening", []):
            _add(line)

    return texts


def greeting_variants_for_preset(lang_preset: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    from language_router import HME_PRESET, resolve_lang_preset

    preset = resolve_lang_preset(lang_preset)
    if preset == HME_PRESET:
        base_greetings, fillers = HI_EN_MR_GREETING_VARIANTS, HI_EN_MR_FILLER_VARIANTS
    else:
        base_greetings, fillers = GREETING_VARIANTS, FILLER_VARIANTS

    merged_greetings: dict[str, list[str]] = {}
    for lang in languages_for_preset(lang_preset):
        merged_greetings[lang] = greeting_texts_for_lang(lang, lang_preset=lang_preset)
    # Preserve any langs only in base map.
    for lang, lines in base_greetings.items():
        if lang not in merged_greetings:
            merged_greetings[lang] = list(lines)

    return merged_greetings, fillers


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit()) or "unknown"


def _name_cache_key(phone: str, name: str = "") -> str:
    raw = f"{_normalize_phone(phone)}|{(name or '').strip().lower()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _name_meta_path(phone: str) -> Path:
    return NAME_PRONUNCIATION_DIR / f"{_normalize_phone(phone)}.json"


def set_name_phonetic(phone: str, name: str, phonetic: str) -> None:
    """Cache a phonetic pronunciation hint for a lead's name."""
    key = _normalize_phone(phone)
    hint = (phonetic or "").strip()
    if not key or not hint:
        return
    _name_phonetic_cache[key] = hint
    try:
        NAME_PRONUNCIATION_DIR.mkdir(parents=True, exist_ok=True)
        meta = {}
        path = _name_meta_path(phone)
        if path.is_file():
            meta = json.loads(path.read_text(encoding="utf-8"))
        meta["phonetic"] = hint
        meta["name"] = (name or "").strip()
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.debug("[NAME-CACHE] phonetic write failed: %s", exc)


def get_name_phonetic(phone: str, name: str = "") -> str | None:
    """Return cached phonetic hint for a lead, if any."""
    key = _normalize_phone(phone)
    if key in _name_phonetic_cache:
        return _name_phonetic_cache[key]
    try:
        path = _name_meta_path(phone)
        if path.is_file():
            meta = json.loads(path.read_text(encoding="utf-8"))
            hint = (meta.get("phonetic") or "").strip()
            if hint:
                _name_phonetic_cache[key] = hint
                return hint
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("[NAME-CACHE] phonetic read failed: %s", exc)
    return None


def cache_name_pcm(phone: str, name: str, pcm_bytes: bytes) -> None:
    """Persist TTS PCM for a personalized name greeting."""
    if not pcm_bytes:
        return
    cache_key = _name_cache_key(phone, name)
    _name_pcm_cache[cache_key] = pcm_bytes
    _save_disk_pcm(f"name_{cache_key}", pcm_bytes)


def get_name_greeting_pcm(phone: str, name: str = "") -> bytes | None:
    """Return pre-baked PCM for a lead's name pronunciation greeting."""
    cache_key = _name_cache_key(phone, name)
    if cache_key in _name_pcm_cache:
        return _name_pcm_cache[cache_key]
    pcm = _load_disk_pcm(f"name_{cache_key}")
    if pcm:
        _name_pcm_cache[cache_key] = pcm
    return pcm


def personalize_greeting_with_name(
    template: str,
    *,
    phone: str,
    caller_name: str,
) -> str:
    """Substitute caller name using phonetic hint when cached."""
    name = (caller_name or "").strip()
    if not name or "[user_name]" not in template:
        return template
    spoken = get_name_phonetic(phone, name) or name
    return template.replace("[user_name]", spoken)


def languages_for_preset(lang_preset: str) -> list[str]:
    from language_router import TINGLISH_PRESET, resolve_lang_preset

    preset = resolve_lang_preset(lang_preset)
    return PRESET_GREETING_SETS.get(preset, PRESET_GREETING_SETS[TINGLISH_PRESET])


def session_lines_from_config(cfg: dict | None) -> list[tuple[str, str]]:
    """Collect idle/goodbye lines from config for Cartesia prewarm."""
    from language_router import get_default_turn_language, resolve_lang_preset

    cfg = cfg or {}
    lang = get_default_turn_language(resolve_lang_preset(cfg.get("lang_preset"), cfg), cfg)
    seen: set[str] = set()
    lines: list[tuple[str, str]] = []

    def _add(text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        lines.append((lang, cleaned))

    user_idle = cfg.get("user_idle") if isinstance(cfg.get("user_idle"), dict) else {}
    for key in ("prompt_message", "last_message"):
        _add(str(user_idle.get(key) or ""))

    call_ending = cfg.get("call_ending") if isinstance(cfg.get("call_ending"), dict) else {}
    _add(str(call_ending.get("message") or ""))

    return lines


def _collect_prebake_items(
    *,
    extra_greetings: dict[str, str] | None = None,
    extra_session_lines: list[tuple[str, str]] | None = None,
    lang_preset: str = "telugu",
    languages: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    seen: set[tuple[str, str, str]] = set()
    items: list[tuple[str, str, str]] = []

    def _add(lang: str, text: str, kind: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        key = (lang, text, kind)
        if key in seen:
            return
        seen.add(key)
        items.append(key)

    active_langs = set(languages or languages_for_preset(lang_preset))
    greetings, fillers = greeting_variants_for_preset(lang_preset)

    if extra_greetings:
        for lang, text in extra_greetings.items():
            if lang in active_langs:
                _add(lang, text, "greeting")

    for lang, texts in greetings.items():
        if lang not in active_langs:
            continue
        for text in texts:
            _add(lang, text, "greeting")
    for lang, texts in fillers.items():
        if lang not in active_langs:
            continue
        for text in texts:
            _add(lang, text, "filler")
    for lang, text in extra_session_lines or []:
        _add(lang, text, "session")
    return items


async def prebake_all_greetings(
    api_key: str = "",
    *,
    voice_override: str = "",
    provider: str = "sarvam",
    model: str = "",
    extra_greetings: dict[str, str] | None = None,
    extra_session_lines: list[tuple[str, str]] | None = None,
    disk_only: bool = False,
    gemini_voice: str = "Rasalgethi",
    gemini_model: str = "gemini-3.1-flash-tts-preview",
    lang_preset: str = "telugu",
    languages: list[str] | None = None,
    voice_per_lang: dict[str, str] | None = None,
) -> None:
    """Pre-bake greeting/filler audio for the active language preset."""
    from gemini_tts import DEFAULT_GEMINI_TTS_MODEL, DEFAULT_GEMINI_VOICE

    voice = (voice_override or DEFAULT_GEMINI_VOICE).strip()
    tts_provider = (provider or "sarvam").strip().lower()
    tts_model = model or DEFAULT_GEMINI_TTS_MODEL
    active_langs = languages or languages_for_preset(lang_preset)
    items = _collect_prebake_items(
        extra_greetings=extra_greetings,
        extra_session_lines=extra_session_lines,
        lang_preset=lang_preset,
        languages=active_langs,
    )

    disk_hits = 0
    api_calls = 0
    for lang, text, kind in items:
        active_voice = (voice_per_lang or {}).get(lang) or voice
        if _load_disk_pcm_any(
            text,
            gemini_voice=gemini_voice,
            gemini_model=gemini_model,
            sarvam_voice=active_voice if tts_provider == "sarvam" else "kavya",
            cartesia_voice=active_voice if tts_provider == "cartesia" else "",
            cartesia_model=tts_model if tts_provider == "cartesia" else "",
            prefer_provider=tts_provider,
        ):
            disk_hits += 1
        elif not disk_only and api_key:
            api_calls += 1
        await _prebake_one(
            api_key,
            lang,
            text,
            kind,
            provider=tts_provider,
            voice=voice,
            model=tts_model,
            disk_only=disk_only,
            gemini_voice=gemini_voice,
            gemini_model=gemini_model,
            voice_per_lang=voice_per_lang,
        )

    total_greetings = sum(len(v) for v in _greeting_cache.values())
    total_fillers = sum(len(v) for v in _filler_cache.values())
    total_session = len(_session_cache)
    logger.info(
        "Pre-baked %d greetings + %d fillers + %d session lines (%s) via %s "
        "(disk_hits=%d api_calls=%d disk_only=%s)",
        total_greetings,
        total_fillers,
        total_session,
        "+".join(active_langs),
        tts_provider,
        disk_hits,
        api_calls,
        disk_only,
    )


def _resolve_cache_lang(
    language_code: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    from language_router import clamp_turn_language, get_default_turn_language, resolve_lang_preset

    code = (language_code or "").strip().lower()
    preset = resolve_lang_preset(lang_preset, live_config)
    if code in GREETING_VARIANTS or code in HI_EN_MR_GREETING_VARIANTS:
        return clamp_turn_language(code, lang_preset=preset, live_config=live_config)
    if code in ("en", "english"):
        return clamp_turn_language("en-in", lang_preset=preset, live_config=live_config)
    if code in ("hi", "hindi"):
        return clamp_turn_language("hi", lang_preset=preset, live_config=live_config)
    if code in ("mr", "marathi"):
        return clamp_turn_language("mr", lang_preset=preset, live_config=live_config)
    if code in _greeting_cache:
        return clamp_turn_language(code, lang_preset=preset, live_config=live_config)
    return get_default_turn_language(preset, live_config)


def _cache_lang_fallback_order(
    lang: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> list[str]:
    from language_router import resolve_lang_preset

    preset_langs = languages_for_preset(resolve_lang_preset(lang_preset, live_config))
    order: list[str] = []
    if lang in preset_langs:
        order.append(lang)
    for code in preset_langs:
        if code not in order:
            order.append(code)
    return order


def get_greeting_pcm(
    language_code: str,
    config_first_line: str = "",
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
    time_bucket: TimeBucket | None = None,
    lead_phone: str = "",
) -> bytes | None:
    lang = _resolve_cache_lang(language_code, lang_preset=lang_preset, live_config=live_config)
    if config_first_line and lead_phone and "[user_name]" in config_first_line:
        name_pcm = get_name_greeting_pcm(lead_phone, live_config.get("_caller_name", "") if live_config else "")
        if name_pcm:
            return name_pcm

    if config_first_line:
        lang_cache = _greeting_cache.get(lang)
        if lang_cache and config_first_line in lang_cache:
            return lang_cache[config_first_line]
        for code in _cache_lang_fallback_order(lang, lang_preset=lang_preset, live_config=live_config):
            lang_cache = _greeting_cache.get(code)
            if lang_cache and config_first_line in lang_cache:
                return lang_cache[config_first_line]

    bucket = time_bucket or resolve_time_bucket()
    preset_key = _preset_time_key(lang_preset or "telugu")
    for code in _cache_lang_fallback_order(lang, lang_preset=lang_preset, live_config=live_config):
        cache = _greeting_cache.get(code)
        if not cache:
            continue
        time_lines = TIME_GREETING_VARIANTS.get(preset_key, {}).get(code, {}).get(bucket, [])
        if bucket == "night":
            time_lines = time_lines or TIME_GREETING_VARIANTS.get(preset_key, {}).get(code, {}).get("evening", [])
        for line in time_lines:
            if line in cache:
                return cache[line]
        if cache:
            return random.choice(list(cache.values()))
    return None


def get_session_line_pcm(text: str) -> bytes | None:
    return _session_cache.get((text or "").strip())


def get_filler_pcm(
    language_code: str,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> bytes | None:
    lang = _resolve_cache_lang(language_code, lang_preset=lang_preset, live_config=live_config)
    for code in _cache_lang_fallback_order(lang, lang_preset=lang_preset, live_config=live_config):
        pcm_list = _filler_cache.get(code)
        if pcm_list:
            return random.choice(pcm_list)
    return None