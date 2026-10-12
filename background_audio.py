"""Subtle call ambience via LiveKit BackgroundAudioPlayer."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

_PROJECT_DIR = Path(__file__).resolve().parent

DEFAULT_AMBIENT_VOLUME = 0.12
MIN_AMBIENT_VOLUME = 0.08
MAX_AMBIENT_VOLUME = 0.18

AMBIENT_SOUND_CATALOG: list[dict[str, str]] = [
    {
        "id": "cicd_1",
        "label": "CICD office (custom)",
        "hint": "VK Realty custom office loop — CICD-1.ogg",
    },
    {
        "id": "office_ambient",
        "label": "Office ambience",
        "hint": "Subtle office murmur — LiveKit built-in",
    },
    {
        "id": "city_ambient",
        "label": "City street",
        "hint": "Light urban background at low volume",
    },
    {
        "id": "crowded_room",
        "label": "Crowded room",
        "hint": "Soft call-center / busy room texture",
    },
    {
        "id": "forest_ambient",
        "label": "Forest ambience",
        "hint": "Calm outdoor background",
    },
    {
        "id": "keyboard_typing",
        "label": "Keyboard typing",
        "hint": "Light typing sounds — use very low volume",
    },
    {
        "id": "keyboard_typing2",
        "label": "Keyboard typing (alt)",
        "hint": "Alternate typing texture",
    },
]

_CUSTOM_SOUND_FILES: dict[str, Path] = {
    "cicd_1": _PROJECT_DIR / "CICD-1.ogg",
}

_BUILTIN_SOUND_MAP = {
    "office_ambient": "OFFICE_AMBIENCE",
    "office_ambience": "OFFICE_AMBIENCE",
    "city_ambient": "CITY_AMBIENCE",
    "city_ambience": "CITY_AMBIENCE",
    "crowded_room": "CROWDED_ROOM",
    "forest_ambient": "FOREST_AMBIENCE",
    "forest_ambience": "FOREST_AMBIENCE",
    "hold_music": "HOLD_MUSIC",
    "keyboard_typing": "KEYBOARD_TYPING",
    "keyboard_typing2": "KEYBOARD_TYPING2",
}


def _clamp_volume(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_AMBIENT_VOLUME
    return max(MIN_AMBIENT_VOLUME, min(value, MAX_AMBIENT_VOLUME))


def ambient_sound_catalog() -> list[dict[str, str]]:
    return [dict(item) for item in AMBIENT_SOUND_CATALOG]


def resolve_ambient_sound_settings(live_config: dict) -> dict[str, Any]:
    """Read ambient_sound from flat config or merged DB call_configuration."""
    raw = live_config.get("ambient_sound")
    if not isinstance(raw, dict):
        nested = live_config.get("_call_config")
        if isinstance(nested, dict) and isinstance(nested.get("ambient_sound"), dict):
            raw = nested["ambient_sound"]
        else:
            raw = {}
    file_path = str(raw.get("file_path") or raw.get("path") or "").strip()
    return {
        "enabled": bool(raw.get("enabled", False)),
        "sound_type": str(raw.get("sound_type") or "cicd_1").strip(),
        "volume": _clamp_volume(raw.get("volume", DEFAULT_AMBIENT_VOLUME)),
        "file_path": file_path,
    }


def _resolve_custom_sound_path(sound_type: str) -> str | None:
    key = sound_type.lower().replace("-", "_")
    bundled = _CUSTOM_SOUND_FILES.get(key)
    if bundled and bundled.is_file():
        return str(bundled)
    if bundled:
        logger.warning("[AMBIENT] Bundled ambient file missing: %s", bundled)
    return None


def _resolve_audio_source(*, sound_type: str, file_path: str):
    from livekit.agents import BuiltinAudioClip

    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = (_PROJECT_DIR / path).resolve()
        if path.is_file():
            return str(path)
        logger.warning("[AMBIENT] Custom ambient file missing: %s", file_path)

    custom = _resolve_custom_sound_path(sound_type)
    if custom:
        return custom

    key = _BUILTIN_SOUND_MAP.get(sound_type.lower().replace("-", "_"))
    if key and hasattr(BuiltinAudioClip, key):
        return getattr(BuiltinAudioClip, key)
    logger.warning(
        "[AMBIENT] Unknown sound_type '%s' — using OFFICE_AMBIENCE",
        sound_type,
    )
    return BuiltinAudioClip.OFFICE_AMBIENCE


async def start_background_audio(
    *,
    room,
    session,
    live_config: dict,
):
    """Start looping ambient audio when enabled in config."""
    from livekit.agents import AudioConfig, BackgroundAudioPlayer

    settings = resolve_ambient_sound_settings(live_config)
    if not settings["enabled"]:
        logger.info("[AMBIENT] Disabled in config")
        return None

    source = _resolve_audio_source(
        sound_type=settings["sound_type"],
        file_path=settings["file_path"],
    )
    player = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(source, volume=settings["volume"]),
    )
    await player.start(room=room, agent_session=session)
    logger.info(
        "[AMBIENT] Started — type=%s volume=%.2f",
        settings["sound_type"],
        settings["volume"],
    )
    return player


async def stop_background_audio(player) -> None:
    if player is None:
        return
    try:
        await player.aclose()
        logger.info("[AMBIENT] Stopped")
    except Exception as exc:
        logger.warning("[AMBIENT] Stop failed: %s", exc)