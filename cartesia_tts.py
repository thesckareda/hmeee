"""Cartesia Sonic TTS helpers — voice aliases, language mapping, plugin factory."""
from __future__ import annotations

import os
import re

DEFAULT_CARTESIA_TTS_MODEL = "sonic-3.5"
# Nivas — Telugu + English (tinglish preset)
DEFAULT_CARTESIA_VOICE_ID = "4b732050-eaed-49f0-ae76-a6281a20e303"
NIVAS_VOICE_ALIAS = "nivas"
RAMYA_VOICE_ID = "cf061d8b-a752-4865-81a2-57570a6e0565"
RAMYA_VOICE_ALIAS = "ramya"
VIKRAM_VOICE_ID = "38bded0a-3ab4-42d1-8e47-2e0b6b10ced9"
VIKRAM_VOICE_ALIAS = "vikram"
# Nivas — Hindi + English + Marathi (hme preset)
NIVI_HINDI_VOICE_ID = "3f2297f1-cac9-4942-b0bd-5d9bb3c35946"
NIVI_HINDI_VOICE_ALIAS = "nivi-hindi"

_CARTESIA_VOICES: list[dict[str, str]] = [
    {
        "alias": NIVAS_VOICE_ALIAS,
        "id": DEFAULT_CARTESIA_VOICE_ID,
        "name": NIVAS_VOICE_ALIAS,
        "gender": "male",
    },
    {
        "alias": NIVI_HINDI_VOICE_ALIAS,
        "id": NIVI_HINDI_VOICE_ID,
        "name": "Nivas Hindi/EN/MR",
        "gender": "male",
    },
    {
        "alias": RAMYA_VOICE_ALIAS,
        "id": RAMYA_VOICE_ID,
        "name": RAMYA_VOICE_ALIAS,
        "gender": "female",
    },
    {
        "alias": VIKRAM_VOICE_ALIAS,
        "id": VIKRAM_VOICE_ID,
        "name": VIKRAM_VOICE_ALIAS,
        "gender": "male",
    },
]

_CARTESIA_LANG_MAP = {
    "te-IN": "te",
    "hi-IN": "hi",
    "en-IN": "en",
    "ta-IN": "ta",
    "kn-IN": "kn",
    "ml-IN": "ml",
    "mr-IN": "mr",
    "gu-IN": "gu",
    "bn-IN": "bn",
    "en-US": "en",
    "en-us": "en",
    "en-in": "en",
}

_VOICE_ALIASES = {
    voice["alias"]: voice["id"] for voice in _CARTESIA_VOICES
}
_VOICE_ALIASES["cartesia-default"] = DEFAULT_CARTESIA_VOICE_ID

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def to_cartesia_lang(tts_language: str) -> str:
    raw = (tts_language or "te-IN").strip()
    mapped = _CARTESIA_LANG_MAP.get(raw)
    if mapped:
        return mapped
    if "-" in raw:
        return raw.split("-", 1)[0].lower()
    return raw.lower() or "en"


def resolve_cartesia_voice(voice: str) -> str:
    raw = (voice or "").strip()
    if not raw:
        return DEFAULT_CARTESIA_VOICE_ID
    alias = _VOICE_ALIASES.get(raw.lower())
    if alias:
        return alias
    if _UUID_RE.match(raw):
        return raw
    return raw


def resolve_premium_voice_clone(
    voice: str,
    *,
    live_config: dict | None = None,
    campaign_id: str | None = None,
) -> str:
    """
    Premium voice-clone hook for Cartesia (or ElevenLabs) deployments.

    Set ``cartesia_premium_voice_id`` or ``premium_voice_clone_id`` in agent/campaign
    config to override the default Sonic voice with a customer-specific clone UUID.
    Campaign-level ``voice_clone_id`` takes precedence when ``campaign_id`` is set.

    ElevenLabs premium path: set ``premium_tts_provider`` to ``elevenlabs`` and
    ``premium_voice_clone_id`` — wire through ``tts_provider.py`` when enabled.
    """
    cfg = live_config or {}
    clone_id = ""

    if campaign_id:
        campaign_clones = cfg.get("campaign_voice_clones") or {}
        if isinstance(campaign_clones, dict):
            clone_id = str(campaign_clones.get(campaign_id) or "").strip()

    if not clone_id:
        clone_id = str(
            cfg.get("cartesia_premium_voice_id")
            or cfg.get("premium_voice_clone_id")
            or ""
        ).strip()

    if clone_id and _UUID_RE.match(clone_id):
        return clone_id
    return resolve_cartesia_voice(voice)


def cartesia_voice_for_turn_language(lang: str, *, live_config: dict | None = None) -> str:
    """Pick Cartesia voice id for a turn language (hme preset uses Nivi for hi/mr)."""
    from language_router import HME_PRESET, resolve_lang_preset

    live_config = live_config or {}
    preset = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    voice_map = cartesia_prebake_voice_map(preset)
    return voice_map.get(lang) or resolve_cartesia_voice(live_config.get("tts_voice") or NIVAS_VOICE_ALIAS)


def cartesia_prebake_voice_map(lang_preset: str | None = None) -> dict[str, str]:
    """Language → Cartesia voice id for prewarm (preset-aware)."""
    from language_router import HME_PRESET, TINGLISH_PRESET, resolve_lang_preset

    preset = resolve_lang_preset(lang_preset)
    try:
        from lang_presets_store import get_voice_per_lang

        stored = get_voice_per_lang(preset)
        if stored:
            return stored
    except Exception:
        pass
    if preset == HME_PRESET:
        return {
            "hi": NIVI_HINDI_VOICE_ID,
            "mr": NIVI_HINDI_VOICE_ID,
            "en-in": NIVI_HINDI_VOICE_ID,
        }
    if preset == TINGLISH_PRESET:
        return {
            "te": DEFAULT_CARTESIA_VOICE_ID,
            "en-in": DEFAULT_CARTESIA_VOICE_ID,
        }
    return {"en-in": DEFAULT_CARTESIA_VOICE_ID}


async def synthesize_cartesia_pcm(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    language: str,
    model: str = DEFAULT_CARTESIA_TTS_MODEL,
) -> bytes:
    """Synthesize raw PCM (s16le 24kHz) via Cartesia bytes API."""
    import httpx

    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": api_key,
                "Cartesia-Version": "2024-11-13",
                "Content-Type": "application/json",
            },
            json={
                "model_id": model,
                "transcript": text,
                "voice": {"mode": "id", "id": resolve_cartesia_voice(voice_id)},
                "language": to_cartesia_lang(language),
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 24000,
                },
            },
        )
        resp.raise_for_status()
        return resp.content


def cartesia_voice_catalog_entries() -> list[dict[str, str]]:
    return [
        {
            "id": voice["id"],
            "name": voice["name"],
            "gender": voice["gender"],
            "quality": "cloned · sonic-3.5",
            "provider": "cartesia",
        }
        for voice in _CARTESIA_VOICES
    ]


def build_cartesia_tts(
    *,
    live_config: dict,
    tts_voice: str,
    tts_language: str,
    get_secret,
):
    from livekit.plugins import cartesia

    api_key = (
        live_config.get("cartesia_api_key")
        or get_secret("CARTESIA_API_KEY")
        or os.environ.get("CARTESIA_API_KEY")
    )
    if not api_key:
        raise ValueError("CARTESIA_API_KEY is required for Cartesia TTS")

    model = (
        live_config.get("cartesia_tts_model")
        or os.environ.get("CARTESIA_TTS_MODEL")
        or DEFAULT_CARTESIA_TTS_MODEL
    )
    voice = resolve_premium_voice_clone(
        tts_voice,
        live_config=live_config,
        campaign_id=live_config.get("campaign_id"),
    )
    language = to_cartesia_lang(
        tts_language or live_config.get("tts_language", "te-IN")
    )
    return cartesia.TTS(
        api_key=api_key,
        model=model,
        voice=voice,
        language=language,
        sample_rate=24000,
        word_timestamps=False,
    )