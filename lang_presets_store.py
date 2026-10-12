"""Editable language presets — persisted in data/lang_presets.json."""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

BASE_DIR = Path(__file__).resolve().parent
PRESETS_FILE = BASE_DIR / "data" / "lang_presets.json"

# Spoken Telugish — mirrors HME Hinglish pattern
TINGLISH_DIRECTIVE = (
    "LANGUAGE: Natural Telugish ONLY — spoken Telugu + English. "
    "Never pure literary Telugu, Hindi, Tamil, or Marathi.\n"
    "- Default: natural SPOKEN Telugu (తెలుగు script) with English mixed in — "
    "like a real consultant on a live phone call.\n"
    "- Use ENGLISH for: numbers (1.2 Cr, 3 BHK, 1500 sqft), budgets, dates/times, "
    "place names (Kokapet, Gachibowli, Thane, Financial District), property terms "
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

HME_DIRECTIVE = (
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

_TINGLISH_AGENT_INSTRUCTIONS = (
    "You are Arjun, a Senior Real Estate Consultant at VK Realty, specializing in premium and mid-segment "
    "residential properties in Hyderabad — Kokapet, Financial District, Neopolis, Narsingi, and Gachibowli.\n\n"
    "You are warm, confident, and human-like. Speak natural SPOKEN Telugish (Telugu + English) — match the caller. "
    "Never pure literary Telugu, Hindi, Tamil, or Marathi.\n"
    "- Telugish: Telugu connectors (andi, garu, avunu, sare) + English for numbers (1.2 Cr, 3 BHK), budgets, "
    "site visit, EMI, parking, place names (Kokapet, Gachibowli), and property terms.\n"
    "- English-only caller → clear Indian English with the same English property terms.\n"
    "- Sound like a real phone consultant, not a textbook or news bulletin.\n"
    "Keep responses short, natural, and conversational (1–2 sentences).\n\n"
    "Your responsibilities:\n"
    "- Answer property-related questions using ONLY the provided property context below.\n"
    "- Understand customer needs, qualify serious buyers, guide toward suitable options, collect details naturally, "
    "and schedule site visits.\n\n"
    "STRICT RULES:\n"
    "- Never make up information beyond the given property context.\n"
    "- Never guess pricing, availability, or offers.\n"
    "- If something is not available, say exactly:\n"
    " 'I don't want to provide incorrect information. One of our property specialists will assist you with that.'\n"
    "- Ask only ONE question at a time.\n"
    "- Do not sound robotic or scripted.\n\n"
    "CONVERSATION STYLE:\n"
    "- Use fillers naturally: 'Sare andi.', 'Got it.', 'Avunu.', 'One moment.'\n"
    "- Example: User asks for 3BHK in Kokapet → "
    "'Kokapet lo 3 BHK options unnaayi andi, budget range entha?'\n"
    "- Your goal: natural conversation, build trust, qualify efficiently, move toward site visit."
)

_HME_AGENT_INSTRUCTIONS = (
    "You are Arjun, a Senior Real Estate Consultant at VK Realty, specializing in premium and mid-segment "
    "residential properties in Mumbai and Chandigarh.\n\n"
    "You are warm, confident, and human-like. Speak natural SPOKEN Hinglish, Marathi+English, or Indian English — "
    "match the caller's language. Never pure formal Hindi or pure formal Marathi.\n"
    "- Hinglish: Hindi connectors in Devanagari (जी, हाँ, बिल्कुल) + English for numbers (1.2 Cr, 3 BHK), "
    "budgets, site visit, EMI, parking, place names (Thane, Zirakpur), and property terms.\n"
    "- Marathi+English: Marathi connectors + same English terms for numbers and property jargon.\n"
    "- Sound like a real phone consultant, not a textbook or news bulletin.\n"
    "Keep responses short, natural, and conversational (1–2 sentences)."
)

DEFAULT_PRESETS: list[dict[str, Any]] = [
    {
        "id": "hme",
        "label": "Hinglish + Marathi + English",
        "description": "Spoken Hinglish and Marathi+English — English for numbers and property terms.",
        "enabled": True,
        "agent_instructions": _HME_AGENT_INSTRUCTIONS,
        "languages": ["hi", "en-in", "mr"],
        "default_language": "hi",
        "tts_voice": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
        "tts_voice_display": "Nivas Hindi/EN/MR",
        "tts_language": "hi-IN",
        "first_line": (
            "नमस्ते! मैं अर्जुन बोल रहा हूँ, VK Realty से। "
            "आपकी property search में मैं कैसे मदद कर सकता हूँ?"
        ),
        "language_directive": HME_DIRECTIVE,
        "interrupt_ack": "जी or haan",
        "voice_per_lang": {
            "hi": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
            "mr": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
            "en-in": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
        },
        "user_idle": {
            "threshold_sec": 30,
            "max_prompts": 1,
            "prompt_message": "Hello, sun pa rahe hain?",
            "last_message": "Koi baat nahi, jab time ho to dubara call kar lena. Thank you!",
            "end_after_max": False,
        },
    },
]


def _default_store() -> dict[str, Any]:
    return {"active_preset_id": "hme", "presets": deepcopy(DEFAULT_PRESETS)}


def load_store() -> dict[str, Any]:
    if not PRESETS_FILE.is_file():
        store = _default_store()
        save_store(store)
        return store
    try:
        raw = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw.get("presets"), list):
            return _default_store()
        return raw
    except Exception as exc:
        logger.warning("[PRESETS] Failed to read %s: %s", PRESETS_FILE, exc)
        return _default_store()


def save_store(store: dict[str, Any]) -> dict[str, Any]:
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return store


def list_presets() -> dict[str, Any]:
    store = load_store()
    cfg_lang = ""
    try:
        from config_store import read_raw_config

        cfg_lang = str(read_raw_config().get("lang_preset") or "")
    except Exception:
        pass
    active = store.get("active_preset_id") or cfg_lang or "hme"
    return {
        "active_preset_id": active,
        "runtime_lang_preset": cfg_lang,
        "presets": store.get("presets") or [],
    }


def get_preset(preset_id: str) -> dict[str, Any] | None:
    pid = (preset_id or "").strip().lower()
    for preset in load_store().get("presets") or []:
        if str(preset.get("id", "")).lower() == pid:
            return preset
    return None


def update_preset(preset_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    pid = preset_id.strip().lower()
    updated = None
    for i, preset in enumerate(store.get("presets") or []):
        if str(preset.get("id", "")).lower() != pid:
            continue
        merged = {**preset, **{k: v for k, v in patch.items() if k != "id"}}
        store["presets"][i] = merged
        updated = merged
        break
    if updated is None:
        raise KeyError(f"Preset not found: {preset_id}")
    save_store(store)
    return updated


def set_preset_enabled(preset_id: str, enabled: bool) -> dict[str, Any]:
    return update_preset(preset_id, {"enabled": bool(enabled)})


def get_language_directive(preset_id: str | None) -> str | None:
    preset = get_preset(preset_id or "")
    if not preset or not preset.get("enabled"):
        return None
    directive = str(preset.get("language_directive") or "").strip()
    return directive or None


def get_idle_messages(preset_id: str | None) -> dict[str, str] | None:
    preset = get_preset(preset_id or "")
    if not preset or not preset.get("enabled"):
        return None
    idle = preset.get("user_idle")
    if not isinstance(idle, dict):
        return None
    prompt = str(idle.get("prompt_message") or "").strip()
    last = str(idle.get("last_message") or "").strip()
    if not prompt:
        return None
    return {"prompt_message": prompt, "last_message": last}


def get_interrupt_ack(preset_id: str | None) -> str | None:
    preset = get_preset(preset_id or "")
    if not preset:
        return None
    ack = str(preset.get("interrupt_ack") or "").strip()
    return ack or None


def get_voice_per_lang(preset_id: str | None) -> dict[str, str] | None:
    preset = get_preset(preset_id or "")
    if not preset:
        return None
    vmap = preset.get("voice_per_lang")
    if isinstance(vmap, dict) and vmap:
        return {str(k): str(v) for k, v in vmap.items()}
    voice = str(preset.get("tts_voice") or "").strip()
    if not voice:
        return None
    langs = preset.get("languages") or []
    return {str(lang): voice for lang in langs}


def activate_preset(preset_id: str) -> dict[str, Any]:
    """Apply preset to config.json and signal worker reload."""
    preset = get_preset(preset_id)
    if not preset:
        raise KeyError(f"Preset not found: {preset_id}")
    if not preset.get("enabled", True):
        raise ValueError(f"Preset '{preset_id}' is disabled")

    from config_store import read_raw_config, write_full_config

    pid = str(preset["id"])
    idle = preset.get("user_idle") if isinstance(preset.get("user_idle"), dict) else {}
    current = read_raw_config()
    agent_instructions = str(preset.get("agent_instructions") or current.get("agent_instructions") or "").strip()

    updates: dict[str, Any] = {
        "lang_preset": pid,
        "languages": list(preset.get("languages") or []),
        "language": str(preset.get("default_language") or "te"),
        "first_line": str(preset.get("first_line") or ""),
        "tts_voice": str(preset.get("tts_voice") or ""),
        "tts_voice_display": str(preset.get("tts_voice_display") or ""),
        "tts_language": str(preset.get("tts_language") or "te-IN"),
        "user_idle": {
            "threshold_sec": int(idle.get("threshold_sec") or 30),
            "max_prompts": int(idle.get("max_prompts") or 1),
            "prompt_message": str(idle.get("prompt_message") or "Are you still there?"),
            "last_message": str(idle.get("last_message") or "Goodbye!"),
            "end_after_max": bool(idle.get("end_after_max", False)),
        },
    }
    if agent_instructions:
        updates["agent_instructions"] = agent_instructions
    if isinstance(current.get("assistant_settings"), dict):
        voice = current["assistant_settings"].get("voice")
        if isinstance(voice, dict):
            voice["voice_id"] = updates["tts_voice"]
            voice["display_name"] = updates["tts_voice_display"]
        welcome = current["assistant_settings"].get("welcome")
        if isinstance(welcome, dict):
            welcome["message"] = updates["first_line"]
        current["assistant_settings"]["languages"] = updates["languages"]
        if updates.get("agent_instructions"):
            current["assistant_settings"]["system_prompt"] = updates["agent_instructions"]
        updates["assistant_settings"] = current["assistant_settings"]

    write_full_config({**current, **updates})

    store = load_store()
    store["active_preset_id"] = pid
    save_store(store)

    try:
        from greeting_cache import clear_prewarm_caches
        from worker_prewarm import invalidate_worker_prewarm

        clear_prewarm_caches()
        invalidate_worker_prewarm()
    except Exception as exc:
        logger.warning("[PRESETS] Prewarm invalidate failed: %s", exc)

    try:
        from assistant_settings import touch_reload_signal

        touch_reload_signal()
    except Exception as exc:
        logger.warning("[PRESETS] Reload signal failed: %s", exc)

    logger.info("[PRESETS] Activated preset '%s'", pid)
    return {"preset": preset, "config_updates": updates}