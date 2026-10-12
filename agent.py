import os
import json
import logging
import certifi
import numpy as np
import pytz
import re
import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta
from datetime_utils import now_utc, now_ist, to_ist
from dotenv import load_dotenv
from pathlib import Path
from typing import Annotated

# Fix for macOS SSL certificate verification
os.environ["SSL_CERT_FILE"] = certifi.where()

# ── Sentry error tracking (#21) ───────────────────────────────────────────────
import sentry_sdk
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        integrations=[AsyncioIntegration()],
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

# ── Background task safety ────────────────────────────────────────────────────
import traceback
_TASK_REGISTRY: set = set()

def safe_create_task(coro, name='', on_error_callback=None):
    async def _wrapper():
        try:
            return await coro
        except asyncio.CancelledError:
            logger.debug("Task cancelled: %s", name or coro)
            raise
        except Exception:
            logger.error("Unhandled exception in task %s:\n%s", name or coro, traceback.format_exc())
            sentry_sdk.capture_exception()
            if on_error_callback:
                try:
                    on_error_callback()
                except Exception:
                    logger.exception("Error in on_error_callback for task %s", name or coro)
    task = asyncio.create_task(_wrapper(), name=name)
    _TASK_REGISTRY.add(task)
    task.add_done_callback(_TASK_REGISTRY.discard)
    return task

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_AGENT_DIR = Path(__file__).resolve().parent
load_dotenv(_AGENT_DIR / ".env", override=True)
logger = logging.getLogger("inbound-voice-agent")

_FAREWELL_UTTERANCE_RE = re.compile(
    r"^(?:"
    r"bye|good\s*bye|goodbye|see\s*you|talk\s*to\s*you\s*later|"
    r"that'?s\s*all|thats\s*all|okay\s*bye|ok\s*bye|bye\s*bye|"
    r"thank\s*you\s*bye|thanks\s*bye|dhanyavad|"
    r"thank\s*you(?:\s*so\s*much)?"
    r")(?:[.!?।\s]*)$",
    re.IGNORECASE,
)


def user_wants_to_end_call(transcript: str, *, turn_count: int, min_turns: int = 1) -> bool:
    """
    True only for explicit farewells — not casual 'thank you' mid-conversation.
    """
    if turn_count < min_turns:
        return False
    text = (transcript or "").strip().lower().rstrip(".!?। ")
    if not text:
        return False
    if _FAREWELL_UTTERANCE_RE.match(text):
        return True
    words = text.split()
    if len(words) <= 5 and words[-1] in ("bye", "goodbye", "bayi"):
        return True
    telugu_farewells = (
        "sare andi",
        "sare bye",
        "okay bye",
        "ok bye",
        "parledu andi",
        "parledu",
        "thank you bye",
        "thanks bye",
    )
    if any(phrase in text for phrase in telugu_farewells):
        return True
    return False
logging.basicConfig(level=logging.INFO)

try:
    import twilio  # noqa: F401
    _WHATSAPP_AVAILABLE = True
except ImportError:
    _WHATSAPP_AVAILABLE = False
    logger.error(
        "[WHATSAPP] twilio not installed in this worker — "
        "brochure send disabled. Run: pip install twilio"
    )

# ── LiveKit version check (#23) ──────────────────────────────────────────────
TESTED_LIVEKIT_VERSION = '0.x.y'
try:
    import livekit as _lk_pkg
    _installed = getattr(_lk_pkg, '__version__', None)
    if _installed and _installed != TESTED_LIVEKIT_VERSION:
        logger.warning(
            "[VERSION] Tested LiveKit version is %s but installed is %s. "
            "Indic TTS chunk buffer may need adjustment.",
            TESTED_LIVEKIT_VERSION, _installed,
        )
except Exception:
    pass

from livekit import api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    WorkerOptions,
    cli,
    llm,
)
# Recording egress helper
from recording import start_recording, stop_recording

# Voice memory integration (legacy)
# RE Voice OS integration
from re_voice_memory import inject_lead_context
from re_voice_memory.inject import build_role_aware_instructions
from re_voice_memory.lead import get_or_create_lead

from secrets_manager import get_secret
from agent_config_loader import parse_job_metadata, resolve_dynamic_agent_config
from post_call_handler import execute_post_call_actions

CONFIG_FILE = "config.json"
# Env-overridable; Phase 1 default 1400 (was 2800) — shorter prompts → lower LLM TTFT
SYSTEM_PROMPT_TOKEN_BUDGET = int(os.environ.get("SYSTEM_PROMPT_TOKEN_BUDGET", "1000"))

# ── Config loader (#17 partial — per-client path awareness) ───────────────────
def get_live_config(phone_number: str | None = None):
    """Load config — VK Realty file config only when USE_FILE_CONFIG_ONLY is set."""
    config = {}
    use_file_only = os.environ.get("USE_FILE_CONFIG_ONLY", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )
    paths = []
    if not use_file_only:
        if phone_number and phone_number != "unknown":
            for variant in phone_match_variants(phone_number):
                compact = variant.replace("+", "").replace(" ", "")
                paths.append(f"configs/{compact}.json")
                if compact.startswith("91") and len(compact) == 12:
                    paths.append(f"configs/{compact[2:]}.json")
            paths = list(dict.fromkeys(paths))
        paths += ["configs/default.json"]
    paths.append(CONFIG_FILE)

    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    config = json.load(f)
                    logger.info(f"[CONFIG] Loaded: {path}")
                    break
            except Exception as e:
                logger.error(f"[CONFIG] Failed to read {path}: {e}")

    defaults = {
        "agent_name": "",
        "agent_instructions": "",
        "first_line": "",
        "stt_min_endpointing_delay": 0.2,
        "stt_max_endpointing_delay": 0.2,
        "stt_silence_timeout_ms": 200,
        "stt_max_endpoint_delay_ms": 200,
        "stt_endpoint_sensitivity": 0.5,
        "stt_enable_language_identification": True,
        "stt_enable_endpoint_detection": True,
        "stt_model": "stt-rt-v5",
        "llm_model": "gemini-2.5-flash",
        "llm_provider": "gemini",
        "gemini_model": "gemini-2.5-flash",
        "llm_max_completion_tokens": 200,
        "llm_temperature": 0.55,
        "llm_startup_probe": False,
        "tts_voice": "Rasalgethi",
        "tts_language": "te-IN",
        "tts_provider": "gemini",
        "gemini_tts_model": "gemini-3.1-flash-tts-preview",
        "cartesia_tts_model": "sonic-3.5",
        "stt_provider": "soniox",
        "language": "hi",
        "stt_language": "unknown",
        "lang_preset": "hme",
        "max_turns": 30,
        "max_call_duration_seconds": 600,
        "tts_hard_word_cap": 50,
        "cal_event_type_id": "",
        "timezone": "Asia/Kolkata",
        "allow_interruptions": True,
        "welcome_dynamic": True,
        "welcome_interruptable": True,
        "min_interruption_duration": 0.35,
        "min_interruption_words": 1,
        "noise_reduction": True,
        "ambient_sound": {
            "enabled": True,
            "sound_type": "cicd_1",
            "volume": 0.12,
        },
        "user_idle": {
            "threshold_sec": 30,
            "max_prompts": 1,
            "prompt_message": "Hello, sun pa rahe hain?",
            "last_message": "Koi baat nahi, jab time ho to dubara call kar lena. Thank you!",
            "end_after_max": False,
        },
        "call_ending": {
            "max_duration_sec": 1800,
            "enabled": True,
            "end_phrases": ["goodbye", "bye", "thank you"],
            "message": "Thank you for calling. Have a great day! Goodbye.",
        },
    }
    merged = {**defaults, **config}
    # Nested dicts: config.json values win per-key
    for nested in ("user_idle", "call_ending"):
        if isinstance(config.get(nested), dict):
            merged[nested] = {**defaults[nested], **config[nested]}
    try:
        from assistant_settings import merge_settings_into_runtime
        merged = merge_settings_into_runtime(merged)
    except Exception:
        pass
    return merged


def validate_config(config: dict) -> list[str]:
    """Check config for missing/oversized values. Returns list of warning strings."""
    warnings = []
    if not config.get("agent_instructions"):
        warnings.append("agent_instructions is empty — agent will have no business context")
    if not config.get("first_line"):
        warnings.append("first_line is empty — agent has no greeting")
    try:
        from token_counter import count_tokens

        tokens = count_tokens(config.get("agent_instructions", ""), "hme")
        if tokens > 700:
            warnings.append(f"agent_instructions is {tokens} tokens — may exceed system prompt budget")
    except Exception:
        pass
    return warnings


def is_destination_busy_error(error_message: str) -> bool:
    """Return True only for explicit SIP busy signals from the destination."""
    msg = (error_message or "").lower()

    explicit_busy_markers = (
        "sip 486",
        "486 busy",
        "busy here",
        "user busy",
        "callee busy",
        "sip 600",
        "600 busy",
        "busy everywhere",
    )
    if any(marker in msg for marker in explicit_busy_markers):
        return True

    # Avoid misclassifying generic "in use"/"busy" infrastructure errors
    # (e.g., room/participant identity conflicts) as destination busy.
    if "busy" in msg:
        has_sip_context = any(k in msg for k in ("sip", "pstn", "invite", "response code", "status code"))
        has_identity_conflict = any(
            k in msg for k in ("participant_identity", "already exists", "already in use", "identity", "room")
        )
        return has_sip_context and not has_identity_conflict

    return False


def _resolve_outbound_trunk_id() -> str | None:
    """Outbound trunk only — never fall back to inbound SIP_TRUNK_ID."""
    trunk_id = (os.environ.get("OUTBOUND_TRUNK_ID") or get_secret("outbound_trunk_id") or "").strip()
    return trunk_id or None


def _outbound_sip_identity(dial_to: str, room_name: str) -> str:
    room_tag = re.sub(r"[^a-zA-Z0-9_]", "_", room_name)[-32:]
    return f"sip_{dial_to.replace('+', '')}_{room_tag}"


def _transcript_has_user_speech(transcript_text: str) -> bool:
    text = (transcript_text or "").strip()
    if not text or text == "unavailable":
        return False
    for line in text.splitlines():
        if line.strip().upper().startswith("[USER]") and len(line.strip()) > 7:
            return True
    return False


def _campaign_status_from_call(call_status: str) -> str:
    normalized = (call_status or "").strip().lower()
    if normalized in {"completed", "voicemail"}:
        return "answered"
    if normalized in {"no_answer", "busy", "failed", "cancelled"}:
        return "not_lifted" if normalized == "no_answer" else normalized
    return normalized or "failed"


async def _update_campaign_contact_from_call(
    *,
    bulk_call_id: str | None,
    phone: str,
    call_status: str,
    duration: int,
    call_log_id: str | None = None,
    message: str | None = None,
) -> None:
    if not bulk_call_id:
        return
    try:
        import campaign_store

        campaign_store.update_contact_status(
            bulk_call_id,
            phone,
            _campaign_status_from_call(call_status),
            call_log_id=call_log_id,
            duration_seconds=duration,
            message=message,
        )
    except Exception as exc:
        logger.warning("[CAMPAIGN] Contact update failed: %s", exc)


async def _record_outbound_campaign_outcome(
    *,
    bulk_call_id: str | None,
    phone: str,
    call_status: str,
    duration: int,
    summary: str,
    agent_id: str | None,
    room_id: str | None,
) -> None:
    try:
        from db import save_call_log

        save_result = await save_call_log(
            phone=phone,
            duration=duration,
            transcript="",
            summary=summary,
            call_summary=summary,
            call_status=call_status,
            call_direction="outbound",
            channel_type="phone",
            agent_id=agent_id,
            room_id=room_id,
            bulk_call_id=bulk_call_id,
        )
        call_log_id = None
        if save_result.get("success"):
            data = save_result.get("data")
            if data:
                call_log_id = data[0].get("id") if isinstance(data, list) else data.get("id")
        await _update_campaign_contact_from_call(
            bulk_call_id=bulk_call_id,
            phone=phone,
            call_status=call_status,
            duration=duration,
            call_log_id=call_log_id,
            message=summary[:240] if summary else None,
        )
    except Exception as exc:
        logger.warning("[CAMPAIGN] Failed to record outbound outcome: %s", exc)


async def _perform_outbound_sip_dial(
    *,
    ctx: JobContext,
    dial_to: str,
    bulk_call_id: str | None,
    agent_id: str | None,
) -> tuple[bool, str]:
    """Place the PSTN leg for an outbound job. Returns (ok, normalized_phone)."""
    sip_trunk_id = _resolve_outbound_trunk_id()
    if not sip_trunk_id:
        logger.error("[DIAL] Missing OUTBOUND_TRUNK_ID. Set it in .env (run setup_bidirectional_trunk.py).")
        await _record_outbound_campaign_outcome(
            bulk_call_id=bulk_call_id,
            phone=dial_to,
            call_status="failed",
            duration=0,
            summary="Missing OUTBOUND_TRUNK_ID in environment",
            agent_id=agent_id,
            room_id=ctx.room.name,
        )
        return False, dial_to
    if not is_valid_phone_number(dial_to):
        logger.error(
            f"[DIAL] Invalid destination number format: {dial_to}. "
            "For India use +91XXXXXXXXXX, 91XXXXXXXXXX, or XXXXXXXXXX (10 digits)."
        )
        await _record_outbound_campaign_outcome(
            bulk_call_id=bulk_call_id,
            phone=dial_to,
            call_status="failed",
            duration=0,
            summary=f"Invalid destination number: {dial_to}",
            agent_id=agent_id,
            room_id=ctx.room.name,
        )
        return False, dial_to

    sip_identity = _outbound_sip_identity(dial_to, ctx.room.name)
    for attempt in range(1, 3):
        try:
            logger.info(f"[DIAL] Outbound via trunk={sip_trunk_id} to={dial_to} room={ctx.room.name} (attempt {attempt}/2)")
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    sip_trunk_id=sip_trunk_id,
                    sip_call_to=dial_to,
                    room_name=ctx.room.name,
                    participant_identity=sip_identity,
                    participant_name=dial_to,
                    play_dialtone=False,
                    wait_until_answered=False,
                )
            )
            logger.info("[DIAL] SIP INVITE submitted successfully")
            return True, dial_to
        except Exception as e:
            msg = str(e)
            busy_like = is_destination_busy_error(msg)
            if busy_like and attempt < 2:
                logger.warning("[DIAL] Destination returned SIP busy (486/600). Retrying once in 2s...")
                await asyncio.sleep(2)
                continue
            logger.error(f"[DIAL] SIP call creation failed: {e}")
            logger.error("[DIAL] Common causes: active PSTN leg not yet released by carrier, country route disabled, insufficient SIP credits, or trunk auth mismatch")
            fail_status = "busy" if busy_like else "failed"
            await _record_outbound_campaign_outcome(
                bulk_call_id=bulk_call_id,
                phone=dial_to,
                call_status=fail_status,
                duration=0,
                summary=msg[:240],
                agent_id=agent_id,
                room_id=ctx.room.name,
            )
            return False, dial_to

    await _record_outbound_campaign_outcome(
        bulk_call_id=bulk_call_id,
        phone=dial_to,
        call_status="failed",
        duration=0,
        summary="Outbound dial did not complete",
        agent_id=agent_id,
        room_id=ctx.room.name,
    )
    return False, dial_to


# ── IST time context ──────────────────────────────────────────────────────────
def get_ist_time_context() -> str:
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    today_str = now.strftime("%A, %B %d, %Y")
    time_str  = now.strftime("%I:%M %p")
    days_lines = []
    for i in range(7):
        day   = now + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else day.strftime("%A"))
        days_lines.append(f"  {label}: {day.strftime('%A %d %B %Y')} → ISO {day.strftime('%Y-%m-%d')}")
    days_block = "\n".join(days_lines)
    return (
        f"\n\n[SYSTEM CONTEXT]\n"
        f"Current date & time: {today_str} at {time_str} IST\n"
        f"Resolve ALL relative day references using this table:\n{days_block}\n"
        f"Always use ISO dates when calling save_booking_intent. Appointments in IST (+05:30).\n"
        "Appointment booking window: 9:00 AM to 8:00 PM IST only. "
        "Never confirm any slot outside this window.\n"
        "IMPORTANT: For property SITE VISITS (Godrej, My Home, Signature, Trilight, etc.) "
        "always use schedule_viewing — NOT save_booking_intent. schedule_viewing sends WhatsApp "
        "confirmation with maps link and brochure immediately.]"
    )


# ── Language presets ──────────────────────────────────────────────────────────
_TELUGISH_PRESET_INSTRUCTION = (
    "Speak Telugish ONLY — natural spoken Telugu (తెలుగు script) mixed with English. "
    "Never use Hindi, Tamil, Marathi, or other languages. "
    "Use ENGLISH for numbers (1.2 Crore, 3 BHK), place names (Kokapet, Gachibowli), "
    "property terms (site visit, EMI, parking), dates, and brand names (VK Realty). "
    "Use Telugu for greetings, connectors (andi, garu), questions, and empathy. "
    "Example: 'Kokapet lo 3 BHK flat price 1.2 Crores nunchi start avutundi andi. Site visit schedule cheddama?'"
)

LANGUAGE_PRESETS = {
    "tinglish": {
        "label": "Telugu + English (Telugish)",
        "tts_language": "te-IN",
        "tts_voice": "nivas",
        "instruction": _TELUGISH_PRESET_INSTRUCTION,
    },
    "hme": {
        "label": "Hindi + English + Marathi",
        "tts_language": "hi-IN",
        "tts_voice": "nivi-hindi",
        "instruction": (
            "You are Arjun from VK Realty. Speak natural Hinglish, Marathi+English, or Indian English — "
            "match the caller. Mix Hindi/Marathi connectors with English for numbers, BHK, Crore, "
            "site visit, EMI, place names, and property jargon. Never pure formal Hindi/Marathi. "
            "Never Telugu or other languages."
        ),
    },
    "english": {
        "label": "English (India)",
        "tts_language": "en-IN",
        "tts_voice": "nivas",
        "instruction": (
            "Reply in clear Indian English only. Use English for all numbers and property terms. "
            "Never use Hindi or other Indian languages."
        ),
    },
    # Legacy aliases — resolve_lang_preset() maps these to tinglish / hme
    "telugu": {
        "label": "Telugu + English (Telugish)",
        "tts_language": "te-IN",
        "tts_voice": "nivas",
        "instruction": _TELUGISH_PRESET_INSTRUCTION,
    },
    "multilingual": {
        "label": "Telugu + English (Auto)",
        "tts_language": "te-IN",
        "tts_voice": "nivas",
        "instruction": _TELUGISH_PRESET_INSTRUCTION,
    },
    "hi_en_mr": {
        "label": "Hindi + English + Marathi",
        "tts_language": "hi-IN",
        "tts_voice": "nivi-hindi",
        "instruction": (
            "You are Arjun from VK Realty. Speak natural Hinglish, Marathi+English, or Indian English — "
            "match the caller. Mix Hindi/Marathi connectors with English for numbers, BHK, Crore, "
            "site visit, EMI, place names, and property jargon. Never pure formal Hindi/Marathi. "
            "Never Telugu or other languages."
        ),
    },
}

def get_language_instruction(lang_preset: str) -> str:
    from language_router import get_language_directive, resolve_lang_preset

    preset_key = resolve_lang_preset(lang_preset)
    directive = get_language_directive(preset_key)
    return f"\n\n[LANGUAGE DIRECTIVE]\n{directive}"


_CONVERSATIONAL_MASTERY_COMMON = (
    "\n\n[CONVERSATIONAL MASTERY — SOUND LIKE A REAL PERSON]\n"
    "You are on a live phone call — sound human, sharp, and present.\n"
    "CRITICAL: This call is already in progress. You have already greeted the caller. NEVER re-introduce yourself, never say where you are calling from, never repeat your name or company name, never restart the conversation. Pick up exactly where the conversation left off.\n"
    "Rules:\n"
    "- Maximum 1-2 short spoken sentences per turn. Never monologue.\n"
    "- Always reference prior context from this call. Stay in flow.\n"
    "- One question per turn only.\n"
    "- Site visit flow: propose 1-2 times → confirm details aloud → offer WhatsApp confirmation.\n"
    "- Never call schedule_viewing until caller agrees to a specific date and time.\n"
    "- Never invent prices, availability, or legal details.\n"
    "- NEVER say 'Great question!', 'Sure!', 'Absolutely!', 'I understand your concern' — only AI says that.\n"
    "- NEVER start responses with 'I' repeatedly. Vary sentence structure.\n"
    "- Use natural transitions in the caller's language (see language directive below).\n"
    "- Sound slightly informal — like a colleague, not a customer service bot.\n"
    "- Occasionally self-correct: 'It's 1.2 Crore — actually, 1.15 for the 2BHK'\n"
    "- After answering, add ONE useful extra detail, then ask a follow-up question.\n"
    "  Pattern: Answer → Value Add → Question to maintain control.\n"
)

_CONVERSATIONAL_MASTERY_BY_PRESET = {
    "hme": (
        _CONVERSATIONAL_MASTERY_COMMON
        + "- On interruption: yield instantly with a 1-2 word ack (जी / haan / okay) then listen. Do NOT restart the conversation.\n"
        + "- Use natural Hinglish or Marathi+English (mixed spoken style) matching the caller — "
        "English for numbers and property terms. Never pure formal Hindi/Marathi. Never Telugu.\n"
        + "- Natural transitions: 'Waise...', 'Dekho...', 'Actually...', 'So...'\n"
    ),
    "tinglish": (
        _CONVERSATIONAL_MASTERY_COMMON
        + "- On interruption: yield instantly with a 1-2 word ack (avunu andi / sare / okay) then listen. Do NOT restart the conversation.\n"
        + "- Use natural Telugish (spoken Telugu + English) matching the caller — "
        "English for numbers, BHK, Crore, sqft, EMI, site visit, place names, and property jargon. "
        "Telugu for connectors (andi, garu). Never Hindi or pure literary Telugu.\n"
        + "- Natural transitions: 'Ante...', 'Asalu...', 'Sare andi...', 'Actually...', 'So...' — never Hindi fillers like 'Waise' or 'Dekho'.\n"
    ),
    "english": (
        _CONVERSATIONAL_MASTERY_COMMON
        + "- On interruption: yield instantly with a 1-2 word ack (okay / sure) then listen. Do NOT restart the conversation.\n"
        + "- Use clear Indian English only unless the caller switches language.\n"
    ),
}


def get_conversational_mastery(lang_preset: str) -> str:
    from language_router import HME_PRESET, TINGLISH_PRESET, resolve_lang_preset

    preset_key = resolve_lang_preset(lang_preset)
    if preset_key == HME_PRESET:
        return _CONVERSATIONAL_MASTERY_BY_PRESET["hme"]
    if preset_key == TINGLISH_PRESET:
        return _CONVERSATIONAL_MASTERY_BY_PRESET["tinglish"]
    return _CONVERSATIONAL_MASTERY_BY_PRESET.get(preset_key, _CONVERSATIONAL_MASTERY_BY_PRESET["tinglish"])


_TTS_SCRIPT_PATTERNS = {
    "hi-IN": r"[\u0900-\u097F]",  # Devanagari
    "mr-IN": r"[\u0900-\u097F]",  # Devanagari
    "bn-IN": r"[\u0980-\u09FF]",  # Bengali
    "gu-IN": r"[\u0A80-\u0AFF]",  # Gujarati
    "ta-IN": r"[\u0B80-\u0BFF]",  # Tamil
    "te-IN": r"[\u0C00-\u0C7F]",  # Telugu
    "kn-IN": r"[\u0C80-\u0CFF]",  # Kannada
    "ml-IN": r"[\u0D00-\u0D7F]",  # Malayalam
}

_TTS_SAFE_PREFIX = {
    "hi-IN": "नमस्ते",
    "mr-IN": "नमस्कार",
    "bn-IN": "নমস্কার",
    "gu-IN": "નમસ્તે",
    "ta-IN": "வணக்கம்",
    "te-IN": "నమస్కారం",
    "kn-IN": "ನಮಸ್ಕಾರ",
    "ml-IN": "നമസ്കാരം",
}


def _is_english_only_line(text: str) -> bool:
    """True when reply is Latin/ASCII only — no Indic script markers needed."""
    if not (text or "").strip():
        return True
    if re.search(r"[\u0900-\u0D7F]", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))


def _tts_language_matches_content(tts_language: str, text: str) -> bool:
    """Gate Sarvam prefix injection by detected content language."""
    from language_router import detect_language_from_text

    content_lang = detect_language_from_text(text, fallback="en-in")
    lang_map = {
        "hi-IN": "hi",
        "mr-IN": "mr",
        "bn-IN": "bn",
        "gu-IN": "gu",
        "ta-IN": "ta",
        "te-IN": "te",
        "kn-IN": "kn",
        "ml-IN": "ml",
        "en-IN": "en-in",
    }
    expected = lang_map.get(tts_language, "")
    if not expected:
        return False
    if content_lang == "en-in":
        return False
    return content_lang == expected


def enforce_tts_language_marker(text: str, tts_language: str, tts_provider: str = "sarvam") -> str:
    cleaned = (text or "").replace("—", "-").strip()
    if not cleaned:
        cleaned = "Hello."
    if tts_provider != "sarvam":
        return cleaned

    # Already in a native script — never prepend a different language marker.
    for pattern in _TTS_SCRIPT_PATTERNS.values():
        if re.search(pattern, cleaned):
            return cleaned

    # English-only lines should not get a forced नमस्ते/నమస్కారం prefix.
    if _is_english_only_line(cleaned):
        return cleaned

    pattern = _TTS_SCRIPT_PATTERNS.get(tts_language)
    if not pattern:
        return cleaned
    if re.search(pattern, cleaned):
        return cleaned

    if not _tts_language_matches_content(tts_language, cleaned):
        return cleaned

    prefix = _TTS_SAFE_PREFIX.get(tts_language, "")
    return f"{prefix}. {cleaned}" if prefix else cleaned


def normalize_sarvam_tts_language(tts_language: str, tts_provider: str) -> str:
    return tts_language


def _last_assistant_text(agent: "VoiceAgentAssistant") -> str:
    try:
        items = list(getattr(agent, "_chat_ctx", agent.chat_ctx).items)
        for msg in reversed(items):
            if getattr(msg, "role", None) == "assistant":
                text = getattr(msg, "text_content", None) or ""
                if isinstance(text, str) and text.strip():
                    return text.strip()
    except Exception:
        pass
    return ""


def normalize_sarvam_tts_opts(tts_instance) -> None:
    """Ensure Sarvam TTS opts use API-valid BCP-47 codes and Bulbul v3 voices."""
    from language_router import normalize_sarvam_voice, to_sarvam_tts_lang

    opts = getattr(tts_instance, "_opts", None)
    if opts is None:
        return
    raw_lang = str(getattr(opts, "target_language_code", "") or "")
    opts.target_language_code = to_sarvam_tts_lang(raw_lang)
    speaker = getattr(opts, "speaker", None)
    if speaker:
        opts.speaker = normalize_sarvam_voice(str(speaker))


def configure_sarvam_tts_timeouts(tts_instance, *, timeout_sec: float = 60.0):
    """Sarvam WS recv defaults to 10s — too short while waiting for slow LLM tokens."""
    from livekit.agents import APIConnectOptions
    from indic_sentence_tokenizer import IndicSentenceTokenizer

    normalize_sarvam_tts_opts(tts_instance)
    if hasattr(tts_instance, "_opts"):
        tts_instance._opts.word_tokenizer = IndicSentenceTokenizer()
    long_opts = APIConnectOptions(max_retry=2, retry_interval=0.5, timeout=timeout_sec)
    orig_stream = tts_instance.stream
    orig_synthesize = tts_instance.synthesize

    def stream(*, conn_options=None):
        return orig_stream(conn_options=conn_options or long_opts)

    def synthesize(text, *, conn_options=None):
        return orig_synthesize(text, conn_options=conn_options or long_opts)

    tts_instance.stream = stream
    tts_instance.synthesize = synthesize
    return tts_instance


def normalize_appointment_start_time_ist(start_time: str) -> str:
    raw = str(start_time or "").strip()
    matched = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+\-]\d{2}:?\d{2})?$",
        raw,
        re.IGNORECASE,
    )
    if matched:
        y, mo, da, hh, mm, ss, zone = matched.groups()
        ss = ss or "00"
        if zone:
            zone_norm = "Z" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
            dt = datetime.fromisoformat(f"{y}-{mo}-{da}T{hh}:{mm}:{ss}{zone_norm}")
            ist = pytz.timezone("Asia/Kolkata")
            return dt.astimezone(ist).isoformat(timespec="seconds")
        return f"{y}-{mo}-{da}T{hh}:{mm}:{ss}+05:30"
    return raw


def infer_call_insights(
    transcript_text: str,
    booking_status_msg: str,
    booking_detected: bool,
    sentiment: str,
) -> tuple[str, str]:
    tl = (transcript_text or "").lower()
    booking_text = (booking_status_msg or "").lower()

    if booking_detected or "booking confirmed" in booking_text or "appointment" in tl:
        purpose = "Appointment booking"
    elif "cancel" in tl or "resched" in tl:
        purpose = "Appointment update"
    elif any(k in tl for k in ("price", "cost", "fee", "charges", "timing", "hours", "open", "location", "address")):
        purpose = "General inquiry"
    elif any(k in tl for k in ("problem", "issue", "pain", "urgent", "emergency", "help")):
        purpose = "Support request"
    else:
        purpose = "General conversation"

    if transcript_text and transcript_text != "unavailable":
        first_user = ""
        for line in transcript_text.splitlines():
            if line.startswith("[USER] "):
                first_user = line[len("[USER] "):].strip()
                break
        if first_user:
            first_user = first_user[:220]
            call_summary = f"Caller intent: {first_user}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."
        else:
            call_summary = f"Call completed. Purpose: {purpose}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."
    else:
        call_summary = f"Call completed. Purpose: {purpose}. Outcome: {booking_status_msg}. Sentiment: {sentiment}."

    return purpose, call_summary


# ── External imports ──────────────────────────────────────────────────────────
from calendar_tools import (
    get_available_slots,
    create_booking,
    cancel_booking,
    is_within_booking_hours_ist,
    get_calendar_setup_error,
)
import db
from notify import (
    notify_booking_confirmed,
    notify_call_no_booking,
)
from phone_utils import normalize_phone_number, is_valid_phone_number, phone_match_variants, is_rate_limited


# ══════════════════════════════════════════════════════════════════════════════
# TOOL CONTEXT — All AI-callable functions
# ══════════════════════════════════════════════════════════════════════════════

class AgentTools(llm.ToolContext):

    def __init__(self, caller_phone: str, caller_name: str = ""):
        super().__init__(tools=[])
        self.caller_phone        = caller_phone
        self.caller_name         = caller_name
        self.booking_intent: dict | None = None
        self.sip_domain          = os.getenv("VOBIZ_SIP_DOMAIN")
        self.ctx_api             = None
        self.room_name           = None
        self._sip_identity       = None
        self._context_buffer: list[dict] = []  # async context update buffer
        # RE Voice OS — real estate agent fields
        self._re_lead_phone: str = ""
        self._re_campaign_id: str | None = None
        self._offer_intent_captured: bool = False
        self._lead_role: str = "buyer"
        self._deal_stage_before: str = "unknown"
        self._intent_score_before: float | None = None
        self.agent_is_speaking: bool = False
        self._speaking_lock = asyncio.Lock()
        self._prefetched_results: dict[str, str] = {}
        self._active_tool_tasks: dict[str, asyncio.Task] = {}
        self._sent_brochures: set[str] = set()
        self._brochure_in_flight: set[str] = set()
        self._scheduled_site_visits: set[str] = set()
        self._site_visit_records: list[dict] = []
        self.turn_count: int = 0
        self._user_requested_hangup: bool = False
        self._caller_disconnected: bool = False
        self._job_ctx = None
        self._session = None
        self._agent = None

    _SLOW_TOOLS = frozenset({
        "check_availability",
        "check_price_history",
        "schedule_viewing",
        "send_listing",
        "save_booking_intent",
    })

    _THINKING_BY_LANG: dict[str, list[str]] = {
        "en-in": ["One moment, let me check.", "Sure, one second."],
        "te": ["ఒక్క నిమిషం andi, చూస్తాను.", "Sure, one moment."],
        "hi": ["एक सेकंड ji, देखता हूँ।", "One moment, let me check."],
        "mr": ["एक सेकंद, बघतो.", "One moment ji."],
    }

    async def _speak_thinking_phrase(self, tool_name: str) -> None:
        """Brief ack before slow tool I/O so the caller hears no dead air."""
        if tool_name not in self._SLOW_TOOLS:
            return
        agent = getattr(self, "_agent", None)
        if agent is None or self.agent_is_speaking:
            return
        live_config = getattr(agent, "_live_config", {}) or {}
        from language_router import default_language_for_config

        lang = default_language_for_config(live_config)
        phrases = self._THINKING_BY_LANG.get(lang) or self._THINKING_BY_LANG["en-in"]
        phrase = phrases[self.turn_count % len(phrases)]
        try:
            await agent.speak_gated(phrase, add_to_chat_ctx=False)
        except Exception as exc:
            logger.debug("[TOOL-THINKING] skipped for %s: %s", tool_name, exc)

    def buffer_context_update(self, key: str, value: str, listing_id: str | None = None):
        """Non-blocking: stage a context update for post-call persistence."""
        entry: dict = {
            "key": key,
            "value": value,
            "timestamp": now_utc().isoformat(),
        }
        if listing_id:
            entry["listing_id"] = listing_id
        self._context_buffer.append(entry)

    def buffer_objection_handled(
        self,
        objection_type: str,
        raw_phrase: str,
        script_used: str = "generic",
        outcome: str = "resolved",
        listing_id: str | None = None,
    ) -> None:
        """Stage objection + script for post-call append_objection persistence."""
        payload = f"{objection_type}:{raw_phrase}:{script_used}:{outcome}"
        self.buffer_context_update("objection", payload, listing_id)

    def _record_site_visit_conversion(self, listing_id: str = "") -> None:
        """Track site visit booking for conversion_metrics."""
        try:
            from conversion_metrics import record_site_visit_booking

            record_site_visit_booking(
                campaign_id=self._re_campaign_id,
                phone=self.caller_phone,
                listing_id=listing_id,
            )
        except Exception as e:
            logger.debug("[CONVERSION] site visit record failed: %s", e)

    def _mark_deal_booked(self) -> None:
        """Advance deal stage + call agenda when a site visit is confirmed."""
        live = getattr(self, "_live_config", None)
        if not isinstance(live, dict):
            return
        tracker = live.get("_deal_tracker")
        if tracker is not None:
            tracker.mark_booked()
        planner = live.get("_call_planner")
        if planner is not None:
            planner.advance_for_deal_stage("booked")

    def drain_context_buffer(self) -> list[dict]:
        """Called at shutdown to flush buffer to durable memory."""
        buffer = self._context_buffer.copy()
        self._context_buffer.clear()
        return buffer

    async def dispatch_tool(self, tool_name: str, context_text: str) -> str:
        """Prefetch tool lookup in parallel with LLM generation."""
        from datetime_utils import now_ist
        today = now_ist().strftime("%Y-%m-%d")
        try:
            if tool_name == "check_calendar_slots":
                return await self.check_availability(date=today)
            if tool_name == "check_price_history":
                listing_id = self._extract_listing_id(context_text)
                return await self.check_price_history(listing_id=listing_id or "unknown")
            if tool_name == "schedule_viewing":
                listing_id = self._extract_listing_id(context_text) or "pending"
                return await self.schedule_viewing(
                    listing_id=listing_id,
                    requested_datetime=f"{today} 11:00",
                )
            if tool_name == "get_listing_details":
                listing_id = self._extract_listing_id(context_text) or "unknown"
                return await self.check_price_history(listing_id=listing_id)
            if tool_name == "search_listings_by_area":
                return f"Searching listings near: {context_text[:120]}"
            if tool_name == "capture_offer_intent":
                listing_id = self._extract_listing_id(context_text) or "pending"
                return await self.capture_offer_intent(
                    listing_id=listing_id,
                    verbal_amount="verbal",
                )
        except Exception as e:
            logger.warning("[PREFETCH] Tool %s failed: %s", tool_name, e)
            return ""
        return ""

    @staticmethod
    def _extract_listing_id(text: str) -> str | None:
        match = re.search(r"\b([A-Z]{2,}-\d{3,}|\d{4,})\b", text or "")
        return match.group(1) if match else None

    # ── Tool: Transfer to Human ───────────────────────────────────────────
    @llm.function_tool(description="Transfer this call to a human agent. Use if: caller asks for human, is angry, or query is outside scope.")
    async def transfer_call(
        self,
        context: Annotated[str, "Optional context note; can be left empty."] = "",
    ) -> str:
        logger.info("[TOOL] transfer_call triggered")
        destination = os.getenv("DEFAULT_TRANSFER_NUMBER")
        if destination and self.sip_domain and "@" not in destination:
            clean_dest  = destination.replace("tel:", "").replace("sip:", "")
            destination = f"sip:{clean_dest}@{self.sip_domain}"
        if destination and not destination.startswith("sip:"):
            destination = f"sip:{destination}"
        try:
            if self.ctx_api and self.room_name and destination and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to=destination,
                        play_dialtone=False,
                    )
                )
                return "Transfer initiated successfully."
            return "Unable to transfer right now."
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return "Unable to transfer right now."

    # ── Tool: End Call ────────────────────────────────────────────────────
    @llm.function_tool(
        description=(
            "End the call. Use ONLY when the caller has clearly said goodbye/bye "
            "and does not have more questions. Never use after a normal question."
        )
    )
    async def end_call(
        self,
        reason: Annotated[str, "Optional reason for ending the call; can be left empty."] = "",
    ) -> str:
        if not self._user_requested_hangup:
            logger.warning(
                "[TOOL] end_call blocked (LLM triggered early, turn=%s): %s",
                self.turn_count,
                reason,
            )
            return (
                "Do not end the call. The caller has not said goodbye. "
                "Continue helping with their property questions."
            )
        logger.info("[TOOL] end_call — user-requested hangup (turn=%s): %s", self.turn_count, reason)
        self.call_ended_by = "assistant"
        try:
            if self.ctx_api and self.room_name and self._sip_identity:
                await self.ctx_api.sip.transfer_sip_participant(
                    api.TransferSIPParticipantRequest(
                        room_name=self.room_name,
                        participant_identity=self._sip_identity,
                        transfer_to="tel:+00000000",
                        play_dialtone=False,
                    )
                )
        except Exception as e:
            logger.warning(f"[END-CALL] SIP hangup failed: {e}")
        sess = getattr(self, "_session", None)
        if sess is not None:
            try:
                await sess.shutdown()
            except Exception as e:
                logger.debug("[END-CALL] session.shutdown: %s", e)
        job_ctx = getattr(self, "_job_ctx", None)
        if job_ctx is not None:
            try:
                await job_ctx.shutdown()
            except Exception as e:
                logger.warning("[END-CALL] ctx.shutdown failed: %s", e)
        return "Call ended."

    # ── Tool: Save Booking Intent ─────────────────────────────────────────
    @llm.function_tool(
        description=(
            "Save OFFICE appointment intent when caller books a consultation at VK Realty office. "
            "Do NOT use this for property site visits — use schedule_viewing instead for site visits. "
            "Call ONCE after caller name, phone, and confirmed date+time are collected."
        )
    )
    async def save_booking_intent(
        self,
        start_time:  Annotated[str,  "ISO 8601 datetime e.g. '2026-03-01T10:00:00+05:30'"],
        caller_name: Annotated[str,  "Full name of the caller"],
        caller_phone:Annotated[str,  "Phone number of the caller"],
        notes:       Annotated[str,  "Any notes, email, or special requests"] = "",
        listing_id:  Annotated[str,  "Property key if this is a site visit e.g. godrej, myhome"] = "",
        property_name: Annotated[str, "Property display name for site visits"] = "",
    ) -> str:
        await self._speak_thinking_phrase("save_booking_intent")
        logger.info(f"[TOOL] save_booking_intent: {caller_name} at {start_time}")
        try:
            normalized_start_time = normalize_appointment_start_time_ist(start_time)
            if not is_within_booking_hours_ist(normalized_start_time):
                return "Appointments can only be booked between 9:00 AM and 8:00 PM IST. Please choose a time in that range."

            normalized_intent_phone = normalize_phone_number(caller_phone)
            if not is_valid_phone_number(normalized_intent_phone):
                normalized_intent_phone = normalize_phone_number(self.caller_phone)

            effective_phone = normalized_intent_phone if is_valid_phone_number(normalized_intent_phone) else self.caller_phone
            effective_name = (caller_name or "").strip() or (self.caller_name or "").strip() or effective_phone or "Caller"

            from site_visit_booking import (
                confirm_site_visit_from_booking_intent,
                looks_like_site_visit,
                site_visit_summary_line,
            )

            is_site_visit = bool(listing_id or property_name or looks_like_site_visit(notes))
            self.booking_intent = {
                "start_time":   normalized_start_time,
                "caller_name":  effective_name,
                "caller_phone": effective_phone,
                "notes":        notes,
                "is_site_visit": is_site_visit,
                "listing_id":   listing_id,
                "property_name": property_name,
            }
            if effective_phone and effective_phone != "unknown":
                self.caller_phone = effective_phone
            self.caller_name = effective_name
            # Buffer context update for post-call memory extraction
            self.buffer_context_update(
                key=f"booking:{normalized_start_time}",
                value=f"Caller {effective_name} booked {normalized_start_time}. Notes: {notes}"
            )

            if is_site_visit:
                visit_result = await confirm_site_visit_from_booking_intent(
                    self.booking_intent,
                    scheduled_keys=self._scheduled_site_visits,
                    room_id=self.room_name,
                    listing_id=listing_id,
                    property_name=property_name,
                )
                if visit_result and visit_result.get("record"):
                    self._site_visit_records.append(visit_result["record"])
                    summary = site_visit_summary_line(visit_result["record"])
                    self.buffer_context_update("viewing_scheduled", summary, listing_id or property_name)
                    self.buffer_context_update("site_visit_confirmed", summary, listing_id or property_name)
                    self._record_site_visit_conversion(listing_id or property_name)
                    self._mark_deal_booked()
                    if visit_result.get("brochure_sent"):
                        label = visit_result["record"].get("property_name") or listing_id
                        self.buffer_context_update("brochure_sent", f"{listing_id}:{label}", listing_id)
                    if visit_result.get("whatsapp_sent"):
                        return (
                            f"Site visit confirmed for {property_name or listing_id or 'the property'} "
                            f"on {normalized_start_time}. WhatsApp confirmation with directions "
                            f"and brochure sent to {effective_phone}."
                        )
                    return (
                        f"Site visit saved for {effective_name} at {normalized_start_time}, "
                        "but WhatsApp could not be delivered. Please confirm the WhatsApp number."
                    )

            from notify import send_whatsapp_booking_confirmation

            whatsapp_sent = send_whatsapp_booking_confirmation(
                effective_phone, effective_name, normalized_start_time
            )
            if whatsapp_sent:
                return (
                    f"Appointment confirmed for {effective_name} at {normalized_start_time}. "
                    f"WhatsApp confirmation sent to {effective_phone}."
                )
            return (
                f"Appointment saved for {effective_name} at {normalized_start_time}. "
                "I'll confirm the details shortly."
            )
        except Exception as e:
            logger.error(f"[TOOL] save_booking_intent failed: {e}")
            return "I had trouble saving the booking. Please try again."

    # ── Tool: Check Availability (#13) ────────────────────────────────────
    @llm.function_tool(description="Check available appointment slots for a given date. Call this when user asks about availability.")
    async def check_availability(
        self,
        date: Annotated[str, "Date to check in YYYY-MM-DD format e.g. '2026-03-01'"],
    ) -> str:
        await self._speak_thinking_phrase("check_availability")
        logger.info(f"[TOOL] check_availability: date={date}")
        try:
            setup_error = get_calendar_setup_error()
            if setup_error:
                return f"{setup_error} Please configure calendar credentials to enable appointment booking."
            slots = get_available_slots(date)
            if not slots:
                return (
                    f"No open slots were found on {date}. "
                    "If the caller requests a specific time, proceed to save_booking_intent directly."
                )
            slot_strings = []
            for s in slots[:6]:
                if isinstance(s, dict):
                    if s.get("label"):
                        slot_strings.append(str(s["label"]))
                        continue
                    if s.get("start_time"):
                        slot_strings.append(str(s["start_time"])[-8:][:5])
                        continue
                    if s.get("time"):
                        slot_strings.append(str(s["time"])[11:16])
                        continue
                slot_strings.append(str(s))
            return self._compact_tool_result(
                f"Available slots on {date}: {', '.join(slot_strings)} IST."
            )
        except Exception as e:
            logger.error(f"[TOOL] check_availability failed: {e}")
            return "I'm having trouble checking the calendar right now."

    @staticmethod
    def _compact_tool_result(result: str, max_chars: int = 300) -> str:
        """Truncate large tool results to keep context lean."""
        if not result or len(result) <= max_chars:
            return result
        head = result[:max_chars - 40]
        return f"{head}... [truncated {len(result)} → {max_chars} chars for context budget]"

    # ── Tool: Business Hours (#31) ────────────────────────────────────────
    @llm.function_tool(description="Check if the business is currently open and what the operating hours are.")
    async def get_business_hours(
        self,
        context: Annotated[str, "Optional context note; can be left empty."] = "",
    ) -> str:
        ist  = pytz.timezone("Asia/Kolkata")
        now  = datetime.now(ist)
        hours = {
            0: ("Monday",    "09:00", "20:00"),
            1: ("Tuesday",   "09:00", "20:00"),
            2: ("Wednesday", "09:00", "20:00"),
            3: ("Thursday",  "09:00", "20:00"),
            4: ("Friday",    "09:00", "20:00"),
            5: ("Saturday",  "09:00", "20:00"),
            6: ("Sunday",    "09:00", "20:00"),
        }
        day_name, open_t, close_t = hours[now.weekday()]
        current_time = now.strftime("%H:%M")
        if open_t <= current_time <= close_t:
            return f"We are OPEN. Today ({day_name}): {open_t}–{close_t} IST."
        return f"We are CLOSED. Today ({day_name}): {open_t}–{close_t} IST."

    # ═══════════════════════════════════════════════════════════════════════
    # RE Voice OS — Real Estate Agent Tools (Step 9)
    # ═══════════════════════════════════════════════════════════════════════

    @llm.function_tool(
        description=(
            "Schedule a property site visit for this lead. Call when the caller asks to visit "
            "a property, or when you suggest a site visit and they agree. Sends WhatsApp "
            "confirmation to the caller's number."
        )
    )
    async def schedule_viewing(
        self,
        listing_id: Annotated[str, "Property or project ID/name e.g. godrej, kokapet-3bhk, MUM-001"],
        requested_datetime: Annotated[str, "Requested date and time e.g. '2026-06-10 11:00'"],
        property_name: Annotated[str, "Display name of the property/project"] = "",
        location: Annotated[str, "Area or address for the visit e.g. Kokapet, Hyderabad"] = "",
        notes: Annotated[str, "Any extra visit notes"] = "",
    ) -> str:
        await self._speak_thinking_phrase("schedule_viewing")
        logger.info(
            "[RE-TOOL] schedule_viewing: %s at %s (%s)",
            listing_id,
            requested_datetime,
            property_name or location,
        )
        from site_visit_booking import confirm_site_visit, site_visit_summary_line

        result = await confirm_site_visit(
            caller_phone=self.caller_phone,
            caller_name=self.caller_name,
            listing_id=listing_id,
            requested_datetime=requested_datetime,
            property_name=property_name,
            location=location,
            notes=notes,
            room_id=self.room_name,
            scheduled_keys=self._scheduled_site_visits,
        )

        if result.get("record"):
            self._site_visit_records.append(result["record"])
            summary = site_visit_summary_line(result["record"])
            self.buffer_context_update("viewing_scheduled", summary, listing_id)
            self.buffer_context_update("site_visit_confirmed", summary, listing_id)
            self._record_site_visit_conversion(listing_id)
            self._mark_deal_booked()
            if result.get("brochure_sent"):
                label = result["record"].get("property_name") or listing_id
                self.buffer_context_update("brochure_sent", f"{listing_id}:{label}", listing_id)
        elif not result.get("duplicate"):
            self.buffer_context_update(
                "viewing_scheduled", f"{listing_id} at {requested_datetime}", listing_id
            )

        if result.get("duplicate"):
            return (
                f"You already have a site visit booked for {listing_id} at {requested_datetime}. "
                "Check your WhatsApp for the confirmation."
            )
        if result.get("success") and result.get("whatsapp_sent"):
            extras = []
            if result.get("maps_url"):
                extras.append("the property location link")
            if result.get("brochure_sent"):
                extras.append("the project brochure")
            extras_note = (
                f" along with {' and '.join(extras)}"
                if extras
                else ""
            )
            return (
                f"Perfect! I've confirmed your site visit for "
                f"{property_name or listing_id} on {requested_datetime}. "
                f"A confirmation with all the details{extras_note} has been sent to your WhatsApp "
                f"at {self.caller_phone}."
            )
        if result.get("success"):
            return (
                f"Your site visit for {property_name or listing_id} on {requested_datetime} "
                "is saved with our team. We couldn't deliver WhatsApp just now — "
                "please confirm this number is on WhatsApp."
            )
        return (
            f"I've noted your interest in visiting {listing_id} on {requested_datetime}, "
            "but couldn't complete the booking yet. Our team will call you back shortly."
        )

    @llm.function_tool(
        description=(
            "Send property brochures to the lead on WhatsApp during the call. "
            "Use listing_ids: godrej, myhome, signature, housing, indroyal, tricolt, or trilight."
        )
    )
    async def send_listing(
        self,
        listing_ids: Annotated[
            list[str],
            "Brochure keys to send: godrej, myhome, signature, housing, indroyal, tricolt, trilight",
        ],
        channel: Annotated[str, "Channel to send on, e.g. 'whatsapp'"] = "whatsapp",
    ) -> str:
        await self._speak_thinking_phrase("send_listing")
        channel_key = (channel or "whatsapp").strip().lower()
        logger.info(
            "[RE-TOOL] send_listing: %s via %s to %s",
            listing_ids,
            channel_key,
            self.caller_phone,
        )

        if not _WHATSAPP_AVAILABLE:
            return (
                "Sorry, WhatsApp brochure sending is not configured on the server. "
                "Please contact support."
            )

        try:
            from brochure_intent import send_brochure_if_mapped
        except Exception as exc:
            logger.error("[RE-TOOL] send_listing unavailable: %s", exc)
            return (
                "Sorry, WhatsApp brochure sending is temporarily unavailable. "
                "Please ask again in a moment."
            )

        if channel_key != "whatsapp":
            return "Sorry, I can only send brochures on WhatsApp right now."

        sent_labels: list[str] = []
        failed: list[str] = []
        for lid in listing_ids:
            result = await send_brochure_if_mapped(
                self.caller_phone,
                lid,
                self._sent_brochures,
                self._brochure_in_flight,
            )
            if result:
                label = result.get("label", lid)
                sent_labels.append(label)
                self.buffer_context_update("brochure_sent", f"{lid}:{label}", lid)
            else:
                failed.append(lid)
                logger.warning(
                    "[RE-TOOL] send_listing failed for %s → %s",
                    lid,
                    self.caller_phone,
                )

        if sent_labels and not failed:
            if len(sent_labels) == 1:
                return (
                    f"I've sent the {sent_labels[0]} brochure to your WhatsApp "
                    f"at {self.caller_phone}. Please check your messages."
                )
            joined = ", ".join(sent_labels[:-1]) + f" and {sent_labels[-1]}"
            return (
                f"I've sent the {joined} brochures to your WhatsApp "
                f"at {self.caller_phone}. Please check your messages."
            )

        if sent_labels:
            joined = ", ".join(sent_labels)
            return (
                f"Sent {joined} to WhatsApp, but could not send: {', '.join(failed)}. "
                f"Check WhatsApp on {self.caller_phone}."
            )

        return (
            f"I couldn't send the brochure to WhatsApp. "
            f"The number on this call is {self.caller_phone}. "
            "Please confirm this is your WhatsApp number and that you have joined "
            "our WhatsApp messaging service."
        )

    @llm.function_tool(description="Capture when a lead expresses intent to make an offer on a specific property.")
    async def capture_offer_intent(
        self,
        listing_id: Annotated[str, "The listing ID the lead wants to make an offer on"],
        verbal_amount: Annotated[str, "The verbal amount mentioned, e.g. '1.75Cr'"],
    ) -> str:
        logger.info(f"[RE-TOOL] capture_offer_intent: {listing_id} for {verbal_amount}")
        self.buffer_context_update(
            "offer_intent", f"{listing_id}:{verbal_amount}", listing_id
        )
        self._offer_intent_captured = True
        return (
            "Excellent! I've flagged this to our senior team. "
            "Someone will call you back within 30 minutes to walk through the offer process."
        )

    @llm.function_tool(description="Log an objection the lead has raised. Use when they push back on price, location, or features.")
    async def log_objection(
        self,
        objection_phrase: Annotated[str, "What the lead said to object"],
        listing_id: Annotated[str, "The listing ID this objection relates to"] = "",
    ) -> str:
        logger.info(f"[RE-TOOL] log_objection: {objection_phrase[:80]}")
        try:
            from re_voice_memory.objection_log import detect_objection_type, PLAYBOOK_SCRIPTS
            detected_type = detect_objection_type(objection_phrase) or "other"
            script = PLAYBOOK_SCRIPTS.get(detected_type, "generic")
        except Exception:
            detected_type, script = "other", "generic"
        lid = listing_id or None
        self.buffer_objection_handled(
            detected_type,
            objection_phrase,
            script_used=script,
            outcome="unresolved",
            listing_id=lid,
        )
        return ""  # silent logging — not spoken

    @llm.function_tool(description="Check the price change history for a specific listing.")
    async def check_price_history(
        self,
        listing_id: Annotated[str, "The listing ID to check price history for"],
    ) -> str:
        await self._speak_thinking_phrase("check_price_history")
        logger.info(f"[RE-TOOL] check_price_history: {listing_id}")
        try:
            from re_voice_memory.kv_store import get_listing_facts
            facts = await get_listing_facts(listing_id) or {}
            price_history = facts.get("price_history", [])
            if not price_history:
                return "This property is newly listed at its current price."
            original = price_history[0].get("price", "unknown")
            latest = price_history[-1].get("price", "unknown")
            return f"This property was listed at Rs {original}, currently at Rs {latest}."
        except Exception:
            return "Price history is unavailable at the moment."

    @llm.function_tool(description="Transfer this call to a human agent with full context.")
    async def escalate_to_agent(
        self,
        reason: Annotated[str, "Why the escalation is needed"],
        urgency: Annotated[str, "normal, high, or immediate"] = "normal",
    ) -> str:
        logger.info(f"[RE-TOOL] escalate_to_agent: {urgency} — {reason[:100]}")
        self.buffer_context_update("escalation", f"{urgency}:{reason}")
        if urgency == "immediate":
            return await self.transfer_call(context=reason)
        return (
            "Let me connect you with one of our senior property consultants right now. "
            "I'm passing them everything we've discussed so you don't need to repeat yourself."
        )


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ══════════════════════════════════════════════════════════════════════════════

class VoiceAgentAssistant(Agent):

    def __init__(self, agent_tools: AgentTools, first_line: str = "", live_config: dict | None = None, fallback_message: str = ""):
        tools = llm.find_function_tools(agent_tools)
        self._first_line  = first_line
        self._live_config = live_config or {}
        self._agent_tools = agent_tools
        self._fallback_message = fallback_message.strip()
        self._has_greeted = False
        self._tts_hold = ""
        self._direct_speech_lock = asyncio.Lock()
        live_config_loaded = self._live_config

        base_instructions = live_config_loaded.get("agent_instructions", "")
        ist_context       = get_ist_time_context()
        from language_router import default_language_for_config, resolve_lang_preset

        lang_preset       = resolve_lang_preset(live_config_loaded.get("lang_preset"), live_config_loaded)
        lang_instruction  = get_language_instruction(lang_preset)
        tts_language      = live_config_loaded.get("tts_language", "en-IN")
        tts_provider      = live_config_loaded.get("tts_provider", "sarvam")
        tts_safety_note   = ""
        if tts_provider == "sarvam" and tts_language in _TTS_SCRIPT_PATTERNS:
            safe_token = _TTS_SAFE_PREFIX.get(tts_language, "")
            tts_safety_note = (
                "\n\n[TTS SAFETY]\n"
                "For Sarvam TTS: include native-script words when speaking that language. "
                "English-only replies do NOT need a forced greeting prefix. "
                f"When mixing languages, you may use '{safe_token}' naturally — never on pure English lines."
            )
        tool_safety_note = (
            "\n\n[TOOL CALL SAFETY]\n"
            "Call tools only when required and only with valid arguments. "
            "Never invent missing required fields. "
            "If any required detail is missing, ask the caller a short follow-up question instead of calling a tool."
        )
        from interrupt_handler import get_interrupt_instruction
        from language_router import get_language_overlay
        detected_lang = live_config_loaded.get("detected_language", "")
        lang_overlay = get_language_overlay(detected_lang) if detected_lang else ""
        lang_overlay_note = f"\n\n[LANGUAGE OVERLAY]\n{lang_overlay}" if lang_overlay else ""

        # ── Identity Shield: build human identity anchor (HIGHEST PRIORITY) ──
        from identity_shield import (
            build_identity_anchor,
            build_identity_trap_handler_instruction,
            load_backstory,
        )
        lang_preset_key = resolve_lang_preset(live_config_loaded.get("lang_preset"), live_config_loaded)
        from knowledge_pack import preload_knowledge_packs

        preload_knowledge_packs()
        backstory = load_backstory(live_config_loaded)
        identity_anchor = build_identity_anchor(backstory, preset=lang_preset_key)
        identity_trap_handler = build_identity_trap_handler_instruction()

        # ── Conversational IQ: anti-AI patterns + mastery ──
        from conversational_iq import (
            build_anti_ai_response_patterns,
            build_conversational_mastery_prompt,
        )
        anti_ai_patterns = build_anti_ai_response_patterns()

        # ── Employee Knowledge: company & market awareness ──
        from employee_knowledge import build_employee_knowledge_instruction
        employee_knowledge = build_employee_knowledge_instruction(
            backstory,
            lang_preset=lang_preset_key,
            live_config=live_config_loaded,
        )

        # ── Sales Playbook: sales methodology injection ──
        from sales_playbook import build_sales_playbook_instruction
        sales_instruction = build_sales_playbook_instruction()

        # ── Build final instructions: Identity FIRST, then business, then directives ──
        final_instructions = (
            identity_anchor                           # Identity Anchor — HIGHEST PRIORITY
            + identity_trap_handler                    # How to handle "Are you AI?" questions
            + anti_ai_patterns                         # Anti-AI response patterns
            + "\n\n"                                   # Separator
            + base_instructions                        # Business-specific instructions
            + get_conversational_mastery(lang_preset)   # Conversational mastery
            + employee_knowledge                       # Company/market knowledge
            + sales_instruction                        # Sales methodology
            + ist_context                              # IST time context
            + lang_instruction                         # Language directive
            + lang_overlay_note                        # Language overlay
            + tts_safety_note                          # TTS safety
            + tool_safety_note                         # Tool safety
            + f"\n\n{get_interrupt_instruction(lang_preset, live_config_loaded)}"
        )

        from token_counter import count_tokens, truncate_at_boundary

        prompt_budget = int(
            live_config_loaded.get("system_prompt_token_budget") or SYSTEM_PROMPT_TOKEN_BUDGET
        )
        token_count = count_tokens(final_instructions, lang_preset)
        logger.info(f"[PROMPT] System prompt: {token_count} tokens (budget: {prompt_budget})")

        if token_count > prompt_budget:
            logger.warning(
                f"[PROMPT] Prompt exceeds {prompt_budget}-token budget ({token_count} tokens). Truncating..."
            )
            final_instructions = truncate_at_boundary(
                final_instructions, prompt_budget, lang_preset
            )
            token_count = count_tokens(final_instructions, lang_preset)
            logger.info(f"[PROMPT] Trimmed to {token_count} tokens")

        if token_count > prompt_budget * 0.9:
            logger.warning(
                f"[PROMPT] Token count ({token_count}) exceeds 90% of budget ({prompt_budget}) after truncation"
            )

        super().__init__(instructions=final_instructions, tools=tools)

    def reset_tts_hold(self) -> None:
        self._tts_hold = ""

    def _session_audio_output(self):
        return getattr(getattr(self.session, "output", None), "audio", None)

    async def _await_audio_output_idle(self) -> None:
        """Wait for RoomIO flush/playout before direct capture_frame calls."""
        audio_out = self._session_audio_output()
        if audio_out is None:
            return
        flush_task = getattr(audio_out, "_flush_task", None)
        if flush_task and not flush_task.done():
            try:
                await flush_task
            except Exception:
                pass
        try:
            await audio_out.wait_for_playout()
        except Exception:
            pass

    async def _play_frames_to_room(self, frames) -> int:
        """Push PCM frames through RoomIO with proper flush segmentation."""
        audio_out = self._session_audio_output()
        if audio_out is None:
            return 0

        await self._await_audio_output_idle()
        played = 0
        async for frame in frames:
            flush_task = getattr(audio_out, "_flush_task", None)
            if flush_task and not flush_task.done():
                await flush_task
            await audio_out.capture_frame(frame)
            played += 1

        if played:
            audio_out.flush()
            try:
                await audio_out.wait_for_playout()
            except Exception:
                pass
        return played

    def _effective_tts_provider(self) -> str:
        return (
            self._live_config.get("_effective_tts_provider")
            or self._live_config.get("tts_provider", "gemini")
        )

    def _sarvam_api_key(self) -> str:
        from secrets_manager import get_secret

        return (
            self._live_config.get("sarvam_api_key")
            or get_secret("SARVAM_API_KEY")
            or ""
        ).strip()

    def _sarvam_fallback_allowed(self) -> bool:
        from gemini_tts import tts_fallbacks_enabled

        return tts_fallbacks_enabled() and bool(self._sarvam_api_key())

    def _resolve_tts_lang_voice(self, activity=None) -> tuple[str, str, str]:
        from language_router import normalize_sarvam_voice, to_sarvam_tts_lang
        from gemini_tts import normalize_gemini_voice

        activity = activity or self._get_activity_or_raise()
        tts_provider = self._effective_tts_provider()
        if tts_provider == "gemini":
            voice = normalize_gemini_voice(self._live_config.get("tts_voice", "Rasalgethi"))
            return tts_provider, "", voice
        if tts_provider == "cartesia":
            from cartesia_tts import resolve_cartesia_voice, to_cartesia_lang

            voice = resolve_cartesia_voice(self._live_config.get("tts_voice", "nivas"))
            lang = to_cartesia_lang(self._live_config.get("tts_language", "te-IN"))
            return tts_provider, lang, voice
        normalize_sarvam_tts_opts(activity.tts)
        tts_lang = to_sarvam_tts_lang(
            getattr(getattr(activity.tts, "_opts", None), "target_language_code", None)
            or self._live_config.get("tts_language_sarvam")
            or self._live_config.get("tts_language", "en-IN")
        )
        voice = normalize_sarvam_voice(
            getattr(getattr(activity.tts, "_opts", None), "speaker", None)
            or self._live_config.get("tts_voice", "ashutosh")
        )
        return tts_provider, tts_lang, voice

    async def _yield_gemini_pcm_frames(self, safe_text: str, activity=None):
        """PCM frames via Gemini SSE/SDK stream."""
        from gemini_tts import (
            DEFAULT_GEMINI_TTS_MODEL,
            iter_gemini_pcm_frames_with_keys,
            resolve_gemini_tts_api_keys_for_call,
        )
        from secrets_manager import get_secret

        activity = activity or self._get_activity_or_raise()
        _, _, voice = self._resolve_tts_lang_voice(activity)
        model = (
            self._live_config.get("gemini_tts_model")
            or DEFAULT_GEMINI_TTS_MODEL
        )
        try:
            api_keys = resolve_gemini_tts_api_keys_for_call(self._live_config, get_secret)
        except ValueError:
            logger.error("[TTS] Missing Gemini/Google API key for TTS")
            return

        logger.info(
            "[TTS] Gemini synthesize (%d chars, model=%s, voice=%s, keys=%d)",
            len(safe_text),
            model,
            voice,
            len(api_keys),
        )
        frame_count = 0
        async for frame in iter_gemini_pcm_frames_with_keys(
            safe_text,
            api_keys=api_keys,
            model=model,
            voice=voice,
        ):
            frame_count += 1
            yield frame
        if frame_count == 0:
            logger.warning("[TTS] Gemini produced zero frames for: %r", safe_text[:80])

    async def _yield_cartesia_pcm_frames(self, safe_text: str, activity=None):
        activity = activity or self._get_activity_or_raise()
        tts_inst = activity.tts
        _, _, voice = self._resolve_tts_lang_voice(activity)
        logger.info(
            "[TTS] Cartesia synthesize (%d chars, voice=%s)",
            len(safe_text),
            voice,
        )
        stream = tts_inst.synthesize(safe_text)
        frame_count = 0
        async for event in stream:
            frame = getattr(event, "frame", None)
            if frame is not None:
                frame_count += 1
                yield frame
        if frame_count == 0:
            logger.warning("[TTS] Cartesia produced zero frames for: %r", safe_text[:80])

    async def _yield_tts_pcm_frames(self, safe_text: str, activity=None):
        from gemini_tts import (
            GeminiTTSRateLimitError,
            is_gemini_rate_limit_error,
            is_gemini_tts_in_cooldown,
            tts_fallbacks_enabled,
        )

        use_cartesia = self._effective_tts_provider() == "cartesia"
        if use_cartesia:
            try:
                got_frames = False
                async for frame in self._yield_cartesia_pcm_frames(safe_text, activity):
                    got_frames = True
                    yield frame
                if got_frames:
                    return
            except Exception as exc:
                if self._sarvam_fallback_allowed():
                    logger.warning("[TTS] Cartesia failed (%s) — Sarvam fallback", exc)
                else:
                    logger.warning("[TTS] Cartesia failed (%s) — no Sarvam fallback", exc)
                    return
            if self._sarvam_fallback_allowed():
                async for frame in self._yield_sarvam_pcm_frames(safe_text, activity, force=True):
                    yield frame
            return

        use_gemini = self._effective_tts_provider() == "gemini"
        if use_gemini and (not tts_fallbacks_enabled() or not is_gemini_tts_in_cooldown()):
            from circuit_breaker import protected_call

            async def _collect_gemini_frames():
                frames = []
                async for frame in self._yield_gemini_pcm_frames(safe_text, activity):
                    frames.append(frame)
                if not frames:
                    raise RuntimeError("gemini_tts zero frames")
                return frames

            async def _gemini_tts_fallback():
                if self._sarvam_fallback_allowed():
                    frames = []
                    async for frame in self._yield_sarvam_pcm_frames(safe_text, activity, force=True):
                        frames.append(frame)
                    if frames:
                        return frames
                return await self._collect_verbal_ack_pcm_frames()

            try:
                frames = await protected_call(
                    "gemini_tts",
                    _collect_gemini_frames(),
                    fallback=_gemini_tts_fallback,
                    sla_ms=2500.0,
                )
            except (GeminiTTSRateLimitError, Exception) as exc:
                if isinstance(exc, GeminiTTSRateLimitError) or is_gemini_rate_limit_error(exc):
                    logger.warning(
                        "[TTS] Gemini unavailable — protected_call fallback for: %r",
                        safe_text[:60],
                    )
                else:
                    logger.warning("[TTS] Gemini protected_call failed (%s) — fallback", exc)
                frames = await _gemini_tts_fallback()

            for frame in frames or []:
                yield frame
            if frames:
                return
        elif use_gemini and self._sarvam_fallback_allowed():
            logger.info("[TTS] Gemini cooldown — Sarvam fallback for: %r", safe_text[:60])

        if not self._sarvam_fallback_allowed():
            return

        async for frame in self._yield_sarvam_pcm_frames(safe_text, activity, force=True):
            yield frame

    async def _collect_verbal_ack_pcm_frames(self) -> list:
        """Pre-baked brief ack PCM when all TTS providers fail (circuit OPEN / zero frames)."""
        from circuit_breaker import get_fallback_response
        from greeting_cache import get_session_line_pcm

        brief = get_fallback_response("technical_issue")
        pcm = get_session_line_pcm(brief)
        if not pcm:
            pcm = get_session_line_pcm(get_fallback_response("general"))
        if not pcm:
            return []
        frames = []
        async for frame in self._iter_pcm_byte_frames(pcm):
            frames.append(frame)
        return frames

    async def _yield_sarvam_pcm_frames(self, safe_text: str, activity=None, *, force: bool = False):
        """PCM frames via Sarvam REST — avoids plugin WAV/mp3 decode bug."""
        from language_router import normalize_sarvam_voice, to_sarvam_tts_lang
        from tts_stream import synthesize_sarvam_audio_frames

        activity = activity or self._get_activity_or_raise()
        tts_provider, tts_lang, voice = self._resolve_tts_lang_voice(activity)
        if tts_provider != "sarvam" and not force:
            return

        if force and tts_provider in ("gemini", "cartesia"):
            tts_lang = to_sarvam_tts_lang(
                self._live_config.get("tts_language_sarvam")
                or self._live_config.get("tts_language", "en-IN")
            )
            voice = normalize_sarvam_voice(
                self._live_config.get("tts_sarvam_voice")
                or self._live_config.get("tts_voice", "ashutosh")
            )

        api_key = (
            getattr(getattr(activity.tts, "_opts", None), "api_key", None)
            or self._sarvam_api_key()
        )
        if not api_key:
            logger.error("[TTS] Missing Sarvam API key")
            return

        logger.info(
            "[TTS] PCM synthesize (%d chars, lang=%s, voice=%s)",
            len(safe_text),
            tts_lang,
            voice,
        )
        frame_count = 0
        async for frame in synthesize_sarvam_audio_frames(
            safe_text,
            api_key=api_key,
            language_code=tts_lang,
            voice=voice,
            speech_rate=float(self._live_config.get("tts_speech_rate", 0.92) or 0.92),
        ):
            frame_count += 1
            yield frame
        if frame_count == 0:
            logger.warning("[TTS] Sarvam PCM produced zero frames for: %r", safe_text[:80])

    @staticmethod
    async def _iter_pcm_byte_frames(pcm_bytes: bytes):
        import livekit.rtc as rtc

        sample_rate = 24000
        frame_bytes = int(sample_rate * 0.02) * 2
        for offset in range(0, len(pcm_bytes), frame_bytes):
            chunk = pcm_bytes[offset : offset + frame_bytes]
            if len(chunk) < 2:
                continue
            samples = len(chunk) // 2
            yield rtc.AudioFrame(
                data=chunk,
                sample_rate=sample_rate,
                num_channels=1,
                samples_per_channel=samples,
            )

    async def speak_session_line(
        self,
        text: str,
        *,
        add_to_chat_ctx: bool = False,
    ) -> bool:
        """Play idle/goodbye from Cartesia prewarm cache when available."""
        from greeting_cache import get_session_line_pcm

        cleaned = (text or "").strip()
        if not cleaned:
            return False

        pcm = get_session_line_pcm(cleaned)
        if pcm:
            self.reset_tts_hold()
            if self._session_audio_output() is None:
                logger.error("[SESSION-LINE] No session audio output — cannot speak")
                return False
            self._agent_tools.agent_is_speaking = True
            try:
                async with self._direct_speech_lock:
                    played = await self._play_frames_to_room(self._iter_pcm_byte_frames(pcm))
            finally:
                self._agent_tools.agent_is_speaking = False
            if played > 0:
                logger.info("[SESSION-LINE] Played pre-baked PCM: %r", cleaned[:60])
                return True
            logger.warning("[SESSION-LINE] Cached PCM produced zero frames: %r", cleaned[:60])

        return await self.speak_gated(cleaned, add_to_chat_ctx=add_to_chat_ctx)

    async def speak_gated(
        self,
        text: str,
        *,
        add_to_chat_ctx: bool = False,
    ):
        """Single-entry speak path — all assistant audio must pass the TTS gate."""
        from tts_text_transform import gate_assistant_speech_text

        activity = self._get_activity_or_raise()
        tts_provider, tts_lang, _voice = self._resolve_tts_lang_voice(activity)
        safe = gate_assistant_speech_text(
            text,
            apply_marker=enforce_tts_language_marker,
            tts_lang=tts_lang,
            tts_provider=tts_provider,
        )
        if not safe:
            logger.warning("[TTS-GATE] Dropped non-speakable text: %r", (text or "")[:80])
            return False

        self.reset_tts_hold()
        if self._session_audio_output() is None:
            logger.error("[TTS-GATE] No session audio output — cannot speak")
            return False

        self._agent_tools.agent_is_speaking = True
        try:
            async with self._direct_speech_lock:
                played = await self._play_frames_to_room(
                    self._yield_tts_pcm_frames(safe, activity),
                )
        finally:
            self._agent_tools.agent_is_speaking = False

        if played == 0:
            logger.warning("[TTS-GATE] No audio frames for gated text: %r", safe[:80])
            return False

        if add_to_chat_ctx:
            try:
                self._chat_ctx.add_message(role="assistant", content=safe)
            except Exception:
                pass

        try:
            from local_live_monitor import log_event as _local_log_event

            room = getattr(self._agent_tools, "room_name", "") or ""
            if room:
                _local_log_event(room, "assistant", safe, event="transcript")
        except Exception:
            pass
        return True

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Stream LLM output with circuit breaker + optional filler guard during TTFT."""
        from livekit.agents.voice.agent import Agent as LKAgent
        from circuit_breaker import get_fallback_response, protected_async_iter
        from filler_injection import generate_with_filler_guard
        from gemini_llm import apply_dynamic_temperature
        from language_router import default_language_for_config

        prompt_ver = self._live_config.get("prompt_version", "stable")
        logger.info("[PROMPT] version=%s llm_node turn=%s", prompt_ver, self._agent_tools.turn_count)

        last_user = ""
        try:
            for msg in reversed(list(chat_ctx.items)):
                if getattr(msg, "role", None) == "user":
                    last_user = getattr(msg, "text_content", None) or ""
                    if isinstance(last_user, list):
                        last_user = " ".join(str(x) for x in last_user)
                    last_user = str(last_user).strip()
                    break
        except Exception:
            pass

        tracker = self._live_config.get("_deal_tracker")
        deal_stage = getattr(tracker, "stage", None) if tracker else None
        try:
            activity = self._get_activity_or_raise()
            if activity.llm is not None:
                base_temp = float(self._live_config.get("llm_temperature", 0.55))
                applied = apply_dynamic_temperature(
                    activity.llm,
                    last_user,
                    deal_stage=deal_stage,
                    base_temperature=base_temp,
                )
                logger.debug(
                    "[LLM] dynamic temperature=%.2f stage=%s transcript=%r",
                    applied,
                    deal_stage,
                    last_user[:60],
                )
        except Exception as exc:
            logger.debug("[LLM] dynamic temperature skipped: %s", exc)

        default_stream = LKAgent.default.llm_node(self, chat_ctx, tools, model_settings)

        async def _breaker_stream():
            async for chunk in protected_async_iter(
                "gemini_llm",
                default_stream,
                fallback=lambda: get_fallback_response("general"),
                sla_ms=3000.0,
            ):
                yield chunk

        def _chat_context_text(ctx) -> str:
            parts: list[str] = []
            try:
                for msg in ctx.items:
                    content = getattr(msg, "text_content", None) or getattr(msg, "content", "") or ""
                    if isinstance(content, list):
                        content = " ".join(str(x) for x in content)
                    parts.append(str(content))
            except Exception:
                pass
            return "\n".join(parts)

        async def _with_llm_latency(stream):
            import time
            from language_router import resolve_lang_preset
            from token_counter import ingest_calibration

            start = time.perf_counter()
            first = True
            cal_lang = resolve_lang_preset(
                self._live_config.get("lang_preset"), self._live_config
            )
            cal_text = _chat_context_text(chat_ctx)
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    try:
                        ingest_calibration(cal_text, cal_lang, usage)
                    except Exception:
                        pass
                if first:
                    first = False
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    qs = self._live_config.get("_quality_scorer")
                    if qs:
                        qs.record_response_time(elapsed_ms)
                    try:
                        from re_voice_memory.metrics import record_llm_ttft

                        record_llm_ttft(elapsed_ms)
                    except Exception:
                        pass
                yield chunk

        if not bool(self._live_config.get("filler_phrases_enabled", False)):
            async for chunk in _with_llm_latency(_breaker_stream()):
                yield chunk
            return

        audio_out = self._session_audio_output()
        lang = default_language_for_config(self._live_config)

        if audio_out is None:
            async for chunk in _with_llm_latency(_breaker_stream()):
                yield chunk
            return

        wrapped = generate_with_filler_guard(
            _breaker_stream(),
            audio_out,
            lang,
            live_config=self._live_config,
        )
        async for chunk in _with_llm_latency(wrapped):
            yield chunk

    async def _synthesize_tts_segment(self, raw: str, activity):
        """Gate + synthesize one speakable segment."""
        from conversational_iq import DisfluencyInjector
        from identity_shield import identity_safe_gate, load_backstory
        from tts_text_transform import gate_assistant_speech_text, sanitize_tts_stutter

        tts_provider, tts_lang, _voice = self._resolve_tts_lang_voice(activity)
        backstory = load_backstory(self._live_config)

        raw = sanitize_tts_stutter(raw)
        if not raw.strip():
            return

        original = raw
        raw = identity_safe_gate(raw, backstory)
        if raw != original:
            qs = self._live_config.get("_quality_scorer")
            if qs:
                qs.record_ai_disclosure_attempt(caught=True)

        if not raw or not raw.strip():
            return

        raw = DisfluencyInjector.maybe_inject(raw, turn_count=self._agent_tools.turn_count)
        from voice_humanizer import humanize_enabled_for_config, humanize_for_tts

        enable_pauses, enable_breath = humanize_enabled_for_config(self._live_config)
        if enable_pauses or enable_breath:
            raw = humanize_for_tts(
                raw,
                enable_pauses=enable_pauses,
                enable_breath=enable_breath,
                tts_provider=tts_provider,
            )
        safe = gate_assistant_speech_text(
            raw,
            apply_marker=enforce_tts_language_marker,
            tts_lang=tts_lang,
            tts_provider=tts_provider,
        )
        if not safe:
            logger.warning("[TTS-NODE] Dropped non-speakable segment: %r", raw[:80])
            return

        try:
            from local_live_monitor import log_event as _local_log_event

            room = getattr(self._agent_tools, "room_name", "") or ""
            if room:
                _local_log_event(room, "assistant", safe, event="tts_segment")
        except Exception:
            pass

        frame_count = 0
        silence_guard = self._live_config.get("_silence_guard")
        async for frame in self._yield_tts_pcm_frames(safe, activity):
            frame_count += 1
            if silence_guard:
                silence_guard.on_agent_audio_output()
            yield frame

        if frame_count == 0:
            logger.warning("[TTS-NODE] Zero frames — retrying verbal fallback for: %r", safe[:80])
            retry_frames = 0
            if self._sarvam_fallback_allowed():
                async for frame in self._yield_sarvam_pcm_frames(safe, activity, force=True):
                    retry_frames += 1
                    if silence_guard:
                        silence_guard.on_agent_audio_output()
                    yield frame
            if retry_frames == 0:
                async for frame in self._collect_verbal_ack_pcm_frames():
                    retry_frames += 1
                    if silence_guard:
                        silence_guard.on_agent_audio_output()
                    yield frame
            if retry_frames == 0:
                logger.error("[TTS-NODE] No audio for segment after retry: %r", safe[:80])
        else:
            qs = self._live_config.get("_quality_scorer")
            if qs:
                qs.record_agent_turn(safe)

    async def tts_node(self, text, model_settings):
        """Sentence-streaming TTS — speak first clause while LLM still generates."""
        import time

        from tts_text_transform import drain_speakable_tts_chunks, merge_tts_hold_segment

        activity = self._get_activity_or_raise()
        tts_start = time.perf_counter()
        tts_latency_recorded = False

        async def _yield_with_tts_latency(frame_iter):
            nonlocal tts_latency_recorded
            async for frame in frame_iter:
                if not tts_latency_recorded:
                    tts_latency_recorded = True
                    elapsed_ms = (time.perf_counter() - tts_start) * 1000
                    qs = self._live_config.get("_quality_scorer")
                    if qs:
                        ttfa_server_ms = qs.record_tts_latency(elapsed_ms)
                        if ttfa_server_ms is not None:
                            try:
                                from re_voice_memory.metrics import record_e2e_turn_latency

                                record_e2e_turn_latency(ttfa_server_ms)
                            except Exception:
                                pass
                    try:
                        from re_voice_memory.metrics import record_tts_first_chunk

                        record_tts_first_chunk(elapsed_ms)
                    except Exception:
                        pass
                yield frame

        async for chunk in text:
            self._tts_hold = merge_tts_hold_segment(self._tts_hold, chunk)
            speakable_chunks, self._tts_hold = drain_speakable_tts_chunks(self._tts_hold)
            for segment in speakable_chunks:
                async for frame in _yield_with_tts_latency(
                    self._synthesize_tts_segment(segment, activity)
                ):
                    yield frame

        if self._tts_hold.strip():
            async for frame in _yield_with_tts_latency(
                self._synthesize_tts_segment(self._tts_hold, activity)
            ):
                yield frame
            self._tts_hold = ""

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Per-turn hook — LiveKit calls this then generate_reply → tts_node."""
        content = new_message.text_content
        if isinstance(content, list):
            transcript = " ".join(str(x) for x in content).strip()
        else:
            transcript = str(content or "").strip()

        self._agent_tools.turn_count += 1
        tc = self._agent_tools.turn_count
        max_t = int(self._live_config.get("max_turns", 40))
        logger.info("[TRANSCRIPT] Turn %s/%s: '%s'", tc, max_t, transcript)

        from speculative_reply import consume_speculative_hint, record_speculative_injected

        spec_hint = consume_speculative_hint(self._live_config, turn=tc)
        if spec_hint:
            try:
                turn_ctx.add_message(
                    role="system",
                    content=(
                        "[NEXT-TURN PREP — trial, use only if relevant to what the caller just said]\n"
                        + spec_hint
                    ),
                )
                record_speculative_injected(self._live_config, turn=tc, hint=spec_hint)
                logger.info("[SPECULATIVE] Injected prep hint for turn %s", tc)
            except Exception:
                pass

        qs = self._live_config.get("_quality_scorer")
        if qs:
            qs.record_turn_processing_start()
            if transcript:
                qs.record_user_turn(transcript)

        from identity_shield import handle_identity_trap_turn, load_backstory
        from language_router import apply_language_to_config, resolve_turn_language
        from livekit.agents.llm.tool_context import StopResponse

        turn_lang = resolve_turn_language(
            transcript,
            stt_language=self._live_config.get("stt_language"),
            fallback=self._live_config.get("detected_language", "en-in"),
            lang_preset=self._live_config.get("lang_preset"),
            live_config=self._live_config,
        )
        self._live_config.update(apply_language_to_config(self._live_config, turn_lang))

        backstory = load_backstory(self._live_config)
        deflection = handle_identity_trap_turn(
            transcript,
            lang=turn_lang,
            backstory=backstory,
            preset=self._live_config.get("lang_preset"),
        )
        if deflection:
            if qs:
                qs.record_identity_trap(handled=True)
            try:
                from local_live_monitor import log_event as _local_log_event

                room = getattr(self._agent_tools, "room_name", "") or ""
                if room:
                    _local_log_event(
                        room,
                        "system",
                        f"Identity trap → scripted deflection: {deflection[:160]}",
                        event="identity_trap",
                        meta={"transcript": transcript[:200]},
                    )
            except Exception:
                pass
            await self.speak_gated(deflection, add_to_chat_ctx=True)
            raise StopResponse()

        from turn_context_injection import (
            apply_turn_injection_result,
            compute_turn_injections,
            get_turn_inject_latency_stats,
        )

        injection_result = await compute_turn_injections(
            transcript=transcript,
            tc=tc,
            max_t=max_t,
            live_config=self._live_config,
            agent_tools=self._agent_tools,
        )
        apply_turn_injection_result(
            turn_ctx,
            injection_result,
            agent_tools=self._agent_tools,
            quality_scorer=qs,
        )
        if tc % 20 == 0:
            stats = get_turn_inject_latency_stats()
            if stats.get("all", {}).get("count", 0) > 0:
                logger.info("[TURN-INJECT] latency stats: %s", stats)

        if self._live_config.get("backchannel_enabled"):
            bc = self._live_config.get("_backchannel")
            if bc:
                bc.on_user_final()

        reset_idle = self._live_config.get("_reset_idle_timer")
        if callable(reset_idle):
            reset_idle()

    async def _play_greeting(
        self,
        greeting: str,
        *,
        allow_interruptions: bool,
        greeting_pcm: bytes | None = None,
    ) -> None:
        """Play welcome line via session.say so interruptable setting is honored."""
        import time

        greeting_start = time.perf_counter()

        def _record_greeting_tts_latency() -> None:
            qs = self._live_config.get("_quality_scorer")
            if not qs:
                return
            qs.record_tts_latency((time.perf_counter() - greeting_start) * 1000)

        if greeting_pcm:
            try:
                import livekit.rtc as rtc

                async def _greeting_frames():
                    sample_rate = 24000
                    chunk_bytes = int(sample_rate * 0.1) * 2
                    for offset in range(0, len(greeting_pcm), chunk_bytes):
                        chunk = greeting_pcm[offset : offset + chunk_bytes]
                        if len(chunk) < 2:
                            continue
                        samples = len(chunk) // 2
                        yield rtc.AudioFrame(
                            data=chunk,
                            sample_rate=sample_rate,
                            num_channels=1,
                            samples_per_channel=samples,
                        )

                handle = self.session.say(
                    greeting,
                    audio=_greeting_frames(),
                    allow_interruptions=allow_interruptions,
                    add_to_chat_ctx=True,
                )
                await handle.wait_for_playout()
                _record_greeting_tts_latency()
                logger.info(
                    "[GREETING] Played pre-baked PCM (interruptable=%s)",
                    allow_interruptions,
                )
                return
            except Exception as e:
                logger.warning(
                    "[GREETING] Pre-baked PCM via session.say failed: %s — TTS fallback",
                    e,
                )

        try:
            handle = self.session.say(
                greeting,
                allow_interruptions=allow_interruptions,
                add_to_chat_ctx=True,
            )
            await handle.wait_for_playout()
            _record_greeting_tts_latency()
            logger.info(
                "[GREETING] Played TTS greeting (interruptable=%s)",
                allow_interruptions,
            )
        except Exception as e:
            from gemini_tts import tts_fallbacks_enabled

            if tts_fallbacks_enabled():
                logger.warning("[GREETING] session.say failed: %s — speak_gated fallback", e)
                await self.speak_gated(greeting, add_to_chat_ctx=True)
            else:
                logger.error("[GREETING] session.say failed (fallbacks disabled): %s", e)

    async def on_enter(self):
        if self._has_greeted:
            return
        if self._fallback_message:
            await self.speak_gated(self._fallback_message, add_to_chat_ctx=False)
            await self._agent_tools.end_call()
            return

        from assistant_settings import resolve_welcome_message

        template = self._live_config.get(
            "first_line",
            self._first_line or (
                "Namaste! This is Aakriti Clinic — How can I help you?."
            ),
        )
        welcome_dynamic = bool(self._live_config.get("welcome_dynamic", True))
        welcome_interruptable = bool(self._live_config.get("welcome_interruptable", True))
        caller_name = (
            self._live_config.get("_caller_name")
            or self._agent_tools.caller_name
            or ""
        )
        greeting = resolve_welcome_message(
            template,
            dynamic=welcome_dynamic,
            caller_name=str(caller_name),
        )

        from language_router import default_language_for_config, resolve_lang_preset

        lang_preset = resolve_lang_preset(self._live_config.get("lang_preset"), self._live_config)
        detected_lang = self._live_config.get("detected_language") or default_language_for_config(self._live_config)
        personalized = welcome_dynamic and "[user_name]" in str(template)
        greeting_pcm = None
        caller_phone = getattr(self._agent_tools, "caller_phone", "") or ""
        if personalized:
            from greeting_cache import personalize_greeting_with_name

            greeting = personalize_greeting_with_name(
                greeting,
                phone=caller_phone,
                caller_name=str(caller_name),
            )
        else:
            from greeting_cache import get_greeting_pcm

            greeting_pcm = get_greeting_pcm(
                language_code=detected_lang,
                config_first_line=greeting,
                lang_preset=lang_preset,
                live_config=self._live_config,
                lead_phone=caller_phone,
            )

        from llm_prewarm import schedule_llm_context_prewarm

        schedule_llm_context_prewarm(
            self._live_config.get("_agent_llm"),
            self,
            llm.find_function_tools(self._agent_tools),
            greeting=greeting,
            live_config=self._live_config,
        )

        self._has_greeted = True
        self._agent_tools.agent_is_speaking = True
        try:
            await self._play_greeting(
                greeting,
                allow_interruptions=welcome_interruptable,
                greeting_pcm=greeting_pcm,
            )
        finally:
            self._agent_tools.agent_is_speaking = False

        reset_idle = self._live_config.get("_reset_idle_timer")
        if callable(reset_idle):
            reset_idle()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════

def _livekit_ice_mode() -> str:
    """Optional ICE transport override. Empty = SDK default (use JoinResponse ice_servers)."""
    return (os.environ.get("LIVEKIT_ICE_TRANSPORT") or "").strip().upper()


def _build_livekit_rtc_config(*, participant_identity: str | None = None):
    """Build RtcConfiguration only when explicitly needed.

    Audit finding: TRANSPORT_RELAY with empty ice_servers breaks room.connect() even when
    JoinResponse carries valid TURN (turns:turn.speaksy.online:443). Default = no override.
    """
    mode = _livekit_ice_mode()
    turn_urls: list[str] = []
    try:
        import livekit.rtc._proto.room_pb2 as pb
        from livekit import rtc
        from livekit_turn import build_ice_servers, ice_override_enabled, turn_urls_from_env

        ice_servers: list = []
        turn_urls = turn_urls_from_env()
        if ice_override_enabled() and participant_identity and turn_urls:
            ice_servers = build_ice_servers(participant_identity=participant_identity)
            logger.info(
                "[CONNECT] Using public ICE override identity=%s urls=%s",
                participant_identity,
                turn_urls,
            )

        if not mode and not ice_servers:
            return None, "SDK_DEFAULT", turn_urls

        mapping = {
            "RELAY": pb.IceTransportType.TRANSPORT_RELAY,
            "NOHOST": pb.IceTransportType.TRANSPORT_NOHOST,
            "ALL": pb.IceTransportType.TRANSPORT_ALL,
        }
        ice_type = mapping.get(mode) if mode else pb.IceTransportType.TRANSPORT_ALL
        if mode and ice_type is None:
            logger.warning("[CONNECT] Unknown LIVEKIT_ICE_TRANSPORT=%s — using SDK default", mode)
            return None, "SDK_DEFAULT", turn_urls

        rtc_config = rtc.RtcConfiguration(ice_transport_type=ice_type, ice_servers=ice_servers)
        label = mode or ("CUSTOM_ICE" if ice_servers else "ALL")
        return rtc_config, label, turn_urls
    except Exception as exc:
        logger.warning("[CONNECT] Could not build RTC config: %s — using SDK default", exc)
        return None, "SDK_DEFAULT", turn_urls


async def _connect_room(ctx: JobContext) -> None:
    """Join the LiveKit room. Do not wrap ctx.connect() in asyncio.wait_for — cancellation
    triggers room disconnect and kills the job before LiveKit's internal ICE retries finish."""
    participant_identity = ctx.token_claims().identity or "agent"
    rtc_config, ice_mode, turn_urls = _build_livekit_rtc_config(participant_identity=participant_identity)
    started = time.monotonic()
    logger.info(
        "[CONNECT] Joining room=%s ice=%s url=%s turn_override=%s",
        ctx.room.name,
        ice_mode,
        ctx._info.url,
        turn_urls or "off",
    )
    try:
        if rtc_config is not None:
            await ctx.connect(rtc_config=rtc_config)
        else:
            await ctx.connect()
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.error(
            "[CONNECT] WebRTC failed after %.0fms room=%s ice=%s: %s — "
            "run: python3 scripts/diagnose_livekit_rtc.py. "
            "If that FAILs, run the agent on the LiveKit server (same network), not only on your Mac.",
            elapsed_ms,
            ctx.room.name,
            ice_mode,
            exc,
        )
        raise RuntimeError(f"Could not connect to room {ctx.room.name}") from exc
    elapsed_ms = (time.monotonic() - started) * 1000
    logger.info("[CONNECT] Joined room=%s in %.0fms ice=%s", ctx.room.name, elapsed_ms, ice_mode)


def _normalize_gemini_api_env() -> None:
    """Avoid google-genai warning when both GEMINI_API_KEY and GOOGLE_API_KEY are set."""
    gemini = (os.environ.get("GEMINI_API_KEY") or "").strip()
    google = (os.environ.get("GOOGLE_API_KEY") or "").strip()
    if gemini and google:
        # TTS/clients pass api_key explicitly; keep GEMINI for conversation LLM.
        os.environ.pop("GOOGLE_API_KEY", None)


async def entrypoint(ctx: JobContext):
    shutdown_done = False
    shutdown_lock = asyncio.Lock()
    active_calls_enabled = True
    call_transcripts_enabled = True

    _normalize_gemini_api_env()
    await _connect_room(ctx)
    logger.info(f"[ROOM] Connected: {ctx.room.name}")

    if not ctx.proc.userdata.get("prewarmed"):
        from worker_prewarm import run_worker_prewarm
        safe_create_task(run_worker_prewarm(), name="worker_prewarm")

    async def _recover_crash_extractions() -> None:
        try:
            from re_voice_memory.extract import recover_pending_extractions
            recovered = await recover_pending_extractions()
            if recovered:
                logger.info(f"[STARTUP] Recovered {recovered} pending extractions from last crash")
        except Exception as e:
            logger.warning(f"[STARTUP] Extraction recovery skipped: {e}")

    safe_create_task(_recover_crash_extractions(), name="crash_recovery")

    # ── Extract caller info ───────────────────────────────────────────────
    dispatch_phone_number = None
    participant_phone_number = None
    caller_name  = ""
    caller_phone = "unknown"
    sip_from_number = ""
    sip_to_number = ""

    def _extract_valid_phone_from_text(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""

        normalized = normalize_phone_number(raw)
        if is_valid_phone_number(normalized):
            return normalized

        import re as _re
        for match in _re.findall(r"\+?\d[\d\s\-()]{7,}\d", raw):
            normalized_match = normalize_phone_number(match)
            if is_valid_phone_number(normalized_match):
                return normalized_match
        return ""

    # Try metadata first (outbound dispatch) — supports agent_id + phone aliases
    dispatch_meta = parse_job_metadata(ctx.job.metadata or "")
    dispatch_phone_number = dispatch_meta.get("phone_number") or dispatch_meta.get("phone")
    bulk_call_id = (dispatch_meta.get("bulk_call_id") or "").strip() or None
    dynamic_agent_id: str | None = None
    full_agent_config = None

    # Extract from SIP participants
    for identity, participant in ctx.room.remote_participants.items():
        # Name from caller ID (#32)
        if participant.name and participant.name not in ("", "Caller", "Unknown"):
            caller_name = participant.name
            logger.info(f"[CALLER-ID] Name from SIP: {caller_name}")
        attr = participant.attributes or {}
        if not participant_phone_number:
            preferred_keys = (
                "sip.phoneNumber",
                "sip.phone_number",
                "phoneNumber",
                "phone_number",
                "phone",
                "sip.from",
                "sip.to",
                "sip.caller_number",
                "sip.callerNumber",
            )
            for key in preferred_keys:
                extracted = _extract_valid_phone_from_text(attr.get(key))
                if extracted:
                    participant_phone_number = extracted
                    break

            if not participant_phone_number:
                for value in attr.values():
                    extracted = _extract_valid_phone_from_text(value)
                    if extracted:
                        participant_phone_number = extracted
                        break

        if not participant_phone_number:
            participant_phone_number = _extract_valid_phone_from_text(identity)

        if not participant_phone_number:
            participant_phone_number = _extract_valid_phone_from_text(getattr(participant, "name", ""))

        # Extract raw SIP from/to numbers for call_logs
        raw_sip_from = (attr.get("sip.from") or attr.get("sip_from") or "").strip()
        raw_sip_to   = (attr.get("sip.to")   or attr.get("sip_to")   or "").strip()
        if raw_sip_from and not sip_from_number:
            sip_from_number = _extract_valid_phone_from_text(raw_sip_from) or normalize_phone_number(raw_sip_from) or raw_sip_from
        if raw_sip_to and not sip_to_number:
            sip_to_number   = _extract_valid_phone_from_text(raw_sip_to)   or normalize_phone_number(raw_sip_to)   or raw_sip_to

    caller_phone = normalize_phone_number(dispatch_phone_number or participant_phone_number or "unknown")
    livekit_caller_phone = caller_phone
    is_outbound_job = bool(dispatch_phone_number and dispatch_phone_number != "demo")
    outbound_dial_ok = not is_outbound_job

    if is_outbound_job:
        dial_to = normalize_phone_number(dispatch_phone_number)
        outbound_agent_id = (dispatch_meta.get("agent_id") or os.environ.get("DEFAULT_AGENT_ID") or "").strip() or None
        outbound_dial_ok, caller_phone = await _perform_outbound_sip_dial(
            ctx=ctx,
            dial_to=dial_to,
            bulk_call_id=bulk_call_id,
            agent_id=outbound_agent_id,
        )
        if not outbound_dial_ok:
            return
        livekit_caller_phone = caller_phone

    # Keep caller naming stable across calls: once a name is saved for a phone,
    # always reuse it instead of replacing it with new caller-ID values.
    stable_caller_name = await db.fetch_stable_caller_name(caller_phone)
    if stable_caller_name:
        if caller_name and caller_name != stable_caller_name:
            logger.info(
                f"[CALLER-ID] Using saved caller name '{stable_caller_name}' for {caller_phone} "
                f"instead of incoming '{caller_name}'"
            )
        caller_name = stable_caller_name

    # ── Rate limiting (#37) — inbound only; outbound campaigns must not be blocked ──
    if not is_outbound_job and await is_rate_limited(caller_phone):
        logger.warning(f"[RATE-LIMIT] Blocked {caller_phone} — too many calls in 1h")
        return

    # ── Load config (file/default, then optional DB agent override) ───────
    live_config = get_live_config(caller_phone)
    live_config, dynamic_agent_id, full_agent_config = await resolve_dynamic_agent_config(
        dispatch_meta,
        live_config,
    )
    from agent_config_loader import apply_canary_to_live_config

    canary_seed = ctx.room.name or caller_phone or "default"
    live_config = apply_canary_to_live_config(live_config, canary_seed)
    logger.info(
        "[PROMPT] version=%s canary=%s percent=%s",
        live_config.get("prompt_version"),
        live_config.get("is_canary"),
        live_config.get("engine_canary_percent"),
    )
    if not dynamic_agent_id and dispatch_meta.get("agent_id"):
        dynamic_agent_id = str(dispatch_meta.get("agent_id"))
    from call_config import build_call_config
    call_cfg = build_call_config(live_config)
    # Validate config
    config_warnings = validate_config(live_config)
    for w in config_warnings:
        logger.warning(f"[CONFIG] {w}")
    # Low-latency turn-taking: tight endpointing without cutting off mid-thought.
    delay_setting = live_config.get("stt_min_endpointing_delay", 0.2)
    try:
        delay_setting = max(0.2, min(float(delay_setting), 1.20))
    except (TypeError, ValueError):
        delay_setting = 0.2
    max_delay_setting = live_config.get("stt_max_endpointing_delay", 0.2)
    try:
        max_delay_setting = max(0.2, min(float(max_delay_setting), 3.0))
        if max_delay_setting < delay_setting:
            max_delay_setting = delay_setting
    except (TypeError, ValueError):
        max_delay_setting = 0.2
    llm_model     = live_config.get("llm_model", "gpt-4o-mini")
    llm_provider  = live_config.get("llm_provider", "gemini")
    llm_max_tokens = live_config.get("llm_max_completion_tokens", 80)
    try:
        llm_max_tokens = max(60, min(int(llm_max_tokens), 300))
    except (TypeError, ValueError):
        llm_max_tokens = 120
    _probe_raw = live_config.get("llm_startup_probe", False)
    if isinstance(_probe_raw, str):
        llm_startup_probe = _probe_raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        llm_startup_probe = bool(_probe_raw)
    tts_voice     = live_config.get("tts_voice", "Rasalgethi")
    tts_language  = live_config.get("tts_language", "en-IN")
    tts_provider  = live_config.get("tts_provider", "sarvam")
    stt_provider  = live_config.get("stt_provider", "soniox")
    stt_language  = live_config.get("stt_language", "unknown")  # auto-detect (#20)
    max_turns     = live_config.get("max_turns", 30)
    live_config["max_turns"] = max_turns
    llm_temperature = live_config.get("llm_temperature", 0.55)
    try:
        llm_temperature = max(0.0, min(float(llm_temperature), 2.0))
    except (TypeError, ValueError):
        llm_temperature = 0.55

    from language_router import (
        apply_language_to_config,
        default_language_for_config,
        detect_language_from_config,
        get_language_config,
        normalize_sarvam_voice,
        resolve_lang_preset,
        HME_PRESET,
    )
    configured_lang = detect_language_from_config(live_config)
    fallback_lang = default_language_for_config(live_config)
    detected_language = configured_lang if configured_lang != "auto" else live_config.get("detected_language", fallback_lang)
    live_config = apply_language_to_config(live_config, detected_language)
    lang_cfg = get_language_config(detected_language)
    stt_language = live_config.get("stt_language", lang_cfg["stt_lang"])
    tts_language = live_config.get("tts_language", lang_cfg["stt_lang"])
    from tts_provider import resolve_tts_provider as _resolve_tts_provider

    _requested_tts = _resolve_tts_provider(live_config)
    if _requested_tts == "gemini":
        from gemini_tts import normalize_gemini_voice

        tts_voice = normalize_gemini_voice(live_config.get("tts_voice", "Rasalgethi"))
    elif _requested_tts == "cartesia":
        from cartesia_tts import resolve_cartesia_voice

        tts_voice = resolve_cartesia_voice(live_config.get("tts_voice", "nivas"))
    else:
        tts_voice = normalize_sarvam_voice(live_config.get("tts_voice", lang_cfg["tts_voice"]))
    live_config["tts_voice"] = tts_voice
    logger.info(
        "[LANG] Routed language=%s stt=%s tts=%s voice=%s",
        detected_language,
        stt_language,
        _requested_tts,
        tts_voice,
    )

    normalized_tts_language = normalize_sarvam_tts_language(tts_language, _requested_tts)
    if normalized_tts_language != tts_language:
        logger.warning(
            f"[TTS] Language '{tts_language}' is not supported reliably with Sarvam Bulbul v3 in this pipeline. "
            f"Auto-switching to '{normalized_tts_language}'."
        )
        tts_language = normalized_tts_language
        live_config["tts_language"] = normalized_tts_language

    # ── Caller memory injection via RE Voice OS durable memory ───────────
    # Load lead profile for role-aware context (async)
    lead_profile = await get_or_create_lead(caller_phone, caller_name or "")
    role = lead_profile.role if lead_profile else "buyer"
    lang_preset = resolve_lang_preset(live_config.get("lang_preset"), live_config)
    default_lang_pref = "hinglish" if lang_preset == HME_PRESET else "telugu"
    lang_pref = lead_profile.language_pref if lead_profile else default_lang_pref
    if lang_preset == HME_PRESET and lang_pref in ("telugu", "telugish"):
        lang_pref = "hinglish"
    prior_intent = lead_profile.intent_score if lead_profile and lead_profile.call_count > 0 else None

    # Build role-aware base instructions
    role_aware_base = build_role_aware_instructions(
        base_instructions=live_config.get("agent_instructions", ""),
        role=role,
        language_pref=lang_pref,
    )

    campaign_id = live_config.get("campaign_id")
    from continuous_improvement import apply_ab_prompt_variant, get_ab_prompt_variant

    ab_variant = get_ab_prompt_variant(campaign_id, caller_phone)
    live_config["ab_prompt_variant"] = ab_variant
    role_aware_base = apply_ab_prompt_variant(
        role_aware_base,
        ab_variant,
        campaign_id=campaign_id,
    )
    logger.info("[AB-PROMPT] campaign=%s variant=%s phone=%s", campaign_id, ab_variant, caller_phone)

    # Fire memory injection as background task — runs concurrently with
    # the provider setup below. Result is awaited before agent creation.
    memory_task = safe_create_task(inject_lead_context(
        phone=caller_phone,
        base_instructions=role_aware_base,
        campaign_id=campaign_id,
        max_re_tokens=300,
        lang_preset=lang_preset,
        intent_score=prior_intent,
        caller_name=caller_name or "",
        remember_enabled=bool(live_config.get("remember_caller_enabled", True)),
    ))

    # ── Instantiate tools ─────────────────────────────────────────────────
    agent_tools = AgentTools(caller_phone=caller_phone, caller_name=caller_name)
    live_config["_caller_name"] = caller_name
    live_config["welcome_dynamic"] = bool(live_config.get("welcome_dynamic", True))
    live_config["welcome_interruptable"] = bool(live_config.get("welcome_interruptable", True))
    agent_tools.call_ended_by = "caller"
    agent_tools._sip_identity = (
        f"sip_{caller_phone.replace('+','')}" if caller_phone != "unknown" else "inbound_caller"
    )
    agent_tools.ctx_api   = ctx.api
    agent_tools.room_name = ctx.room.name
    agent_tools._job_ctx = ctx
    # RE Voice OS metadata for tool use
    agent_tools._re_lead_phone = caller_phone
    agent_tools._re_campaign_id = campaign_id
    agent_tools._lead_role = role
    agent_tools._intent_score_before = prior_intent
    agent_tools._dynamic_agent_id = dynamic_agent_id
    agent_tools._full_agent_config = full_agent_config
    agent_tools._max_call_duration_seconds = live_config.get("max_call_duration_seconds", 1800)
    agent_tools._live_config = live_config

    call_cfg_meta = live_config.get("_call_config") or {}
    transfer_cfg = call_cfg_meta.get("transfer_routing") or {}
    agent_tools._transfer_enabled = bool(transfer_cfg.get("enabled"))
    agent_tools._transfer_number = str(transfer_cfg.get("transfer_number") or "")

    # ── Build LLM: Gemini 2.5 Flash for conversation ─────────────────────
    fallback_message = ""
    from gemini_llm import resolve_gemini_conversation_model

    gemini_model = resolve_gemini_conversation_model(live_config)
    live_config["gemini_model"] = gemini_model
    live_config["llm_model"] = gemini_model
    agent_llm = None

    def _build_gemini_llm_fast():
        key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            logger.warning("[LLM] GEMINI_API_KEY/GOOGLE_API_KEY missing.")
            return None
        from gemini_llm import build_gemini_conversation_llm

        return build_gemini_conversation_llm(
            model=gemini_model,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=key,
            max_completion_tokens=llm_max_tokens,
            temperature=llm_temperature,
        )

    async def _probe_gemini_llm() -> None:
        key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            return
        try:
            from google import genai as google_genai
            from gemini_client_config import gemini_afc_for_tools

            client = google_genai.Client(api_key=key)
            await asyncio.to_thread(
                client.models.generate_content,
                model=gemini_model,
                contents="ping",
                config={
                    "max_output_tokens": 1,
                    "automatic_function_calling": gemini_afc_for_tools(),
                },
            )
            logger.info("[LLM] Gemini startup probe OK, model=%s", gemini_model)
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("quota", "exhausted", "429", "permission_denied", "403")):
                logger.error("[LLM] Gemini key rejected/quota-exhausted: %s", e)
            else:
                logger.warning("[LLM] Gemini probe failed but continuing: %s", e)

    agent_llm = _build_gemini_llm_fast()
    if agent_llm and llm_startup_probe:
        safe_create_task(_probe_gemini_llm(), name="gemini_probe")
    llm_candidates: list = [agent_llm] if agent_llm else []

    if not agent_llm:
        fallback_message = (
            "I am having a temporary technical issue right now. "
            "Please try again in a few minutes."
        )
        logger.error("[LLM] Gemini unavailable for this call.")

    # ── STT: Soniox stt-rt-v5 WebSocket via official LiveKit plugin ──
    from stt_provider import build_soniox_stt

    from worker_prewarm import ensure_soniox_http_session

    soniox_http = await ensure_soniox_http_session(ctx.proc.userdata)
    agent_stt = build_soniox_stt(
        live_config,
        call_cfg,
        get_secret,
        http_session=soniox_http,
    )

    # ── Build TTS — cartesia | gemini | sarvam fallback | elevenlabs ────
    from tts_provider import build_agent_tts

    live_config["google_api_key"] = call_cfg.google_api_key
    live_config["gemini_api_key"] = call_cfg.gemini_api_key
    live_config["sarvam_api_key"] = call_cfg.sarvam_api_key
    live_config["soniox_api_key"] = call_cfg.soniox_api_key
    live_config["cartesia_api_key"] = call_cfg.cartesia_api_key

    agent_tts, effective_tts_provider = build_agent_tts(
        live_config=live_config,
        tts_voice=tts_voice,
        tts_language=tts_language,
        get_secret=get_secret,
        configure_sarvam_timeouts=configure_sarvam_tts_timeouts,
    )
    live_config["_effective_tts_provider"] = effective_tts_provider
    live_config["tts_provider"] = effective_tts_provider

    from gemini_tts import is_gemini_tts_in_cooldown, tts_fallbacks_enabled

    if not tts_fallbacks_enabled():
        logger.warning("[TTS] Fallbacks DISABLED — Gemini voice key only (GOOGLE_API_KEY)")
    elif effective_tts_provider == "gemini" and is_gemini_tts_in_cooldown():
        logger.warning(
            "[TTS] Gemini TTS quota/cooldown active — LLM replies will use Sarvam fallback via tts_node"
        )

    tts_hard_word_cap = live_config.get("tts_hard_word_cap", 0)
    try:
        tts_hard_word_cap = int(tts_hard_word_cap)
    except (TypeError, ValueError):
        tts_hard_word_cap = 0
    if tts_hard_word_cap > 0:
        logger.info(
            "[TTS] tts_hard_word_cap=%s ignored — brevity enforced via prompt + max_tokens",
            tts_hard_word_cap,
        )

    # ── Turn counter + auto-close (#29) ──────────────────────────────────
    turn_count    = 0
    interrupt_count = 0  # (#30)

    # ── Await memory injection (runs concurrently with provider setup) ────
    try:
        enriched = await memory_task
        live_config["agent_instructions"] = enriched
        from language_router import resolve_lang_preset
        from token_counter import count_tokens

        inject_lang = resolve_lang_preset(live_config.get("lang_preset"), live_config)
        estimated_tokens = count_tokens(enriched, inject_lang)
        prompt_budget = int(
            live_config.get("system_prompt_token_budget") or SYSTEM_PROMPT_TOKEN_BUDGET
        )
        if estimated_tokens > prompt_budget:
            logger.warning(
                f"[TOKEN-BUDGET] System prompt ~{estimated_tokens} tokens exceeds "
                f"{prompt_budget} cap"
            )
    except Exception as e:
        logger.warning("[INJECT] Memory injection failed: %s", e)

    # ── Build agent ───────────────────────────────────────────────────────
    agent = VoiceAgentAssistant(
        agent_tools=agent_tools,
        first_line=live_config.get("first_line", ""),
        live_config=live_config,
        fallback_message=fallback_message,
    )
    agent_tools._agent = agent

    # ── Build session (#3 noise cancellation attempted) ───────────────────
    noise_reduction = live_config.get("noise_reduction", True)
    _noise_cancel = None
    if noise_reduction:
        try:
            from livekit.agents import noise_cancellation as nc
            _noise_cancel = nc.BVC()
            logger.info("[AUDIO] BVC noise cancellation enabled")
        except Exception:
            logger.info("[AUDIO] BVC not available — running without noise cancellation")
    else:
        logger.info("[AUDIO] Noise cancellation disabled by agent config")

    room_input = RoomInputOptions(close_on_disconnect=False)
    if _noise_cancel:
        try:
            room_input = RoomInputOptions(close_on_disconnect=False, noise_cancellation=_noise_cancel)
        except Exception:
            room_input = RoomInputOptions(close_on_disconnect=False)

    min_interrupt_dur = float(live_config.get("min_interruption_duration", 0.28))
    min_interrupt_words = int(live_config.get("min_interruption_words", 1))
    allow_interruptions = bool(live_config.get("allow_interruptions", True))

    from worker_prewarm import load_vad

    agent_vad = ctx.proc.userdata.get("vad")
    if agent_vad is None:
        agent_vad = load_vad()
    if agent_vad is not None:
        logger.info("[VAD] Silero loaded — interruption detection only (turn_detection=stt)")
    else:
        logger.warning("[VAD] Silero unavailable — interruptions may be less reliable")

    def _touch_call_activity(*, user_speaking: bool | None = None) -> None:
        call_activity["last_user_ts"] = asyncio.get_event_loop().time()
        if user_speaking is not None:
            call_activity["user_speaking"] = user_speaking

    call_activity = {
        "last_user_ts": asyncio.get_event_loop().time(),
        "user_speaking": False,
    }
    live_config["_call_activity"] = call_activity
    live_config["_reset_idle_timer"] = lambda: _touch_call_activity(user_speaking=False)

    from livekit.agents import TurnHandlingOptions
    from livekit.agents.voice.turn import (
        EndpointingOptions,
        InterruptionOptions,
        PreemptiveGenerationOptions,
    )

    session = AgentSession(
        stt=agent_stt,
        llm=agent_llm,
        tts=agent_tts,
        vad=agent_vad,
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            endpointing=EndpointingOptions(
                min_delay=float(delay_setting),
                max_delay=float(max_delay_setting),
            ),
            interruption=InterruptionOptions(
                enabled=allow_interruptions,
                mode="vad",
                min_duration=min_interrupt_dur,
                min_words=min_interrupt_words,
            ),
            preemptive_generation=PreemptiveGenerationOptions(enabled=True),
        ),
        tts_text_transforms=("filter_markdown", "filter_emoji"),
    )
    agent_tools._session = session
    live_config["_agent_llm"] = agent_llm

    from knowledge_pack import preload_knowledge_packs
    from sales_playbook import DealStageTracker
    from call_planner import CallPlanner
    from conversational_iq import TopicMemory

    preload_knowledge_packs()
    live_config["_deal_tracker"] = DealStageTracker()
    live_config["_call_planner"] = CallPlanner()
    live_config["_topic_memory"] = TopicMemory()

    await session.start(room=ctx.room, agent=agent, room_input_options=room_input)

    from llm_prewarm import await_llm_context_prewarm

    await await_llm_context_prewarm(live_config)

    from background_audio import start_background_audio, stop_background_audio

    background_audio_player = await start_background_audio(
        room=ctx.room,
        session=session,
        live_config=live_config,
    )

    # ── Silence Guard (off by default — injects filler/bridge/network phrases) ──
    silence_guard = None
    if bool(live_config.get("silence_guard_enabled", False)):
        from silence_guard import SilenceGuard

        silence_guard = SilenceGuard(
            agent=agent,
            agent_tools=agent_tools,
            live_config=live_config,
        )
        await silence_guard.start()
    live_config["_silence_guard"] = silence_guard

    # ── Call Quality Scorer: real-time quality tracking ──
    from call_quality_scorer import CallQualityScorer
    quality_scorer = CallQualityScorer()
    live_config["_quality_scorer"] = quality_scorer

    from backchannel import BackchannelController

    backchannel = BackchannelController(agent=agent, live_config=live_config)
    live_config["_backchannel"] = backchannel


    # ── Per-agent call ending & user-idle (from DB call_configuration) ───
    call_ending_cfg = live_config.get("call_ending") or {}
    user_idle_cfg = live_config.get("user_idle") or {}
    max_dur_sec = int(
        call_ending_cfg.get("max_duration_sec")
        or live_config.get("max_call_duration_seconds")
        or 1800
    )
    goodbye_msg = str(
        call_ending_cfg.get("message")
        or "Thank you for calling. Have a great day! Goodbye."
    )
    # Greeting plays during session.start(); idle timer resets in on_enter after playout.
    logger.info(
        "[USER-IDLE] Monitor active — prompt after %ss: '%s'",
        user_idle_cfg.get("threshold_sec", 6),
        user_idle_cfg.get("prompt_message", "Are you there?"),
    )

    def _call_still_active() -> bool:
        if getattr(agent_tools, "_caller_disconnected", False):
            return False
        if shutdown_done:
            return False
        if getattr(agent_tools, "call_ended_by", None) == "assistant":
            return False
        return True

    async def _enforce_max_duration():
        await asyncio.sleep(max_dur_sec)
        if not _call_still_active():
            return
        try:
            await agent.speak_session_line(goodbye_msg, add_to_chat_ctx=False)
        except Exception as e:
            logger.warning(f"[CALL-ENDING] Max duration goodbye failed: {e}")
        finally:
            await agent_tools.end_call()

    safe_create_task(_enforce_max_duration(), name="max_duration_guard")

    async def _speak_session_line(text: str) -> None:
        """Idle/goodbye via Cartesia prewarm cache, then live TTS fallback."""
        await agent.speak_session_line(text, add_to_chat_ctx=False)

    async def _user_idle_monitor():
        threshold = int(user_idle_cfg.get("threshold_sec") or 0)
        if threshold <= 0:
            return
        max_prompts = int(user_idle_cfg.get("max_prompts") or 1)
        prompt_msg = str(user_idle_cfg.get("prompt_message") or "Are you still there?")
        last_msg = str(user_idle_cfg.get("last_message") or goodbye_msg)
        end_after = bool(user_idle_cfg.get("end_after_max", False))
        prompts_sent = 0
        while True:
            await asyncio.sleep(1.0)
            if not _call_still_active():
                return
            if call_activity.get("user_speaking"):
                continue
            if agent_tools.agent_is_speaking:
                continue
            idle_for = asyncio.get_event_loop().time() - call_activity["last_user_ts"]
            if idle_for >= threshold:
                if prompts_sent < max_prompts:
                    prompts_sent += 1
                    logger.info(
                        "[USER-IDLE] Prompting after %.0fs silence (prompt %s/%s)",
                        idle_for,
                        prompts_sent,
                        max_prompts,
                    )
                    try:
                        await _speak_session_line(prompt_msg)
                    except Exception as e:
                        logger.debug(f"[USER-IDLE] Prompt failed: {e}")
                    _touch_call_activity(user_speaking=False)
                elif end_after:
                    logger.info(
                        "[USER-IDLE] Ending call after %s idle prompts",
                        prompts_sent,
                    )
                    agent_tools._user_requested_hangup = True
                    try:
                        await _speak_session_line(last_msg)
                    except Exception as e:
                        logger.debug(f"[USER-IDLE] Final idle message failed: {e}")
                    await agent_tools.end_call()
                    return

    safe_create_task(_user_idle_monitor(), name="user_idle_monitor")

    async def _log_transcript(role: str, content: str):
        nonlocal call_transcripts_enabled
        if not call_transcripts_enabled or not content:
            return
        # call_transcripts.role CHECK allows only: user, assistant
        if role not in ("user", "assistant"):
            return
        try:
            sb = await db.get_supabase()
            if sb:
                sb.table("call_transcripts").insert({
                    "call_room_id": ctx.room.name,
                    "phone":        normalize_phone_number(livekit_caller_phone),
                    "role":         role,
                    "content":      content,
                }).execute()
        except Exception as e:
            err = str(e)
            if "PGRST205" in err and "call_transcripts" in err.lower():
                call_transcripts_enabled = False
                logger.info("[TRANSCRIPT-STREAM] Table not found; skipping transcript streaming.")
            elif "call_transcripts_role_check" in err or "23514" in err:
                logger.debug("[TRANSCRIPT-STREAM] Invalid role rejected by DB — skipped")
            else:
                logger.debug(f"[TRANSCRIPT-STREAM] {e}")

    recent_user_utterances: list[str] = []
    recent_site_visit_utterances: list[str] = []

    async def _handle_brochure_intent(transcript: str):
        recent_user_utterances.append(transcript)
        if len(recent_user_utterances) > 8:
            del recent_user_utterances[:-8]

        from brochure_intent import maybe_send_brochure_from_transcript

        result = await maybe_send_brochure_from_transcript(
            transcript=transcript,
            recent_utterances=recent_user_utterances,
            caller_phone=agent_tools.caller_phone,
            sent_brochures=agent_tools._sent_brochures,
            in_flight=agent_tools._brochure_in_flight,
            on_sent=lambda key, label: agent_tools.buffer_context_update(
                "brochure_sent", f"{key}:{label}", key
            ),
        )
        if not result:
            return
        try:
            agent._chat_ctx.add_message(
                role="system",
                content=(
                    f"[BROCHURE SENT: {result['label']} PDF sent to caller WhatsApp. "
                    "Briefly confirm they should check WhatsApp.]"
                ),
            )
        except Exception:
            pass

    async def _handle_site_visit_intent(transcript: str):
        recent_site_visit_utterances.append(transcript)
        if len(recent_site_visit_utterances) > 10:
            del recent_site_visit_utterances[:-10]

        from site_visit_intent import maybe_book_site_visit_from_transcript

        def _on_site_visit_confirmed(record: dict):
            agent_tools._site_visit_records.append(record)
            agent_tools.buffer_context_update(
                "site_visit_confirmed",
                f"{record.get('property_name', 'property')} at {record.get('appointment_time', '')}",
                record.get("listing_id"),
            )
            agent_tools._record_site_visit_conversion(record.get("listing_id") or "")
            agent_tools._mark_deal_booked()
            if record.get("brochure_sent"):
                lid = record.get("listing_id") or ""
                label = record.get("property_name") or lid
                agent_tools.buffer_context_update("brochure_sent", f"{lid}:{label}", lid)

        result = await maybe_book_site_visit_from_transcript(
            transcript=transcript,
            recent_utterances=recent_site_visit_utterances,
            caller_phone=agent_tools.caller_phone,
            caller_name=agent_tools.caller_name,
            scheduled_keys=agent_tools._scheduled_site_visits,
            room_id=ctx.room.name,
            on_confirmed=_on_site_visit_confirmed,
        )
        if not result or not result.get("whatsapp_sent"):
            return
        try:
            record = result.get("record") or {}
            agent._chat_ctx.add_message(
                role="system",
                content=(
                    f"[SITE VISIT CONFIRMED: {record.get('property_name', 'property')} "
                    f"on {record.get('appointment_time', '')}. WhatsApp confirmation sent. "
                    "Briefly tell the caller to check WhatsApp.]"
                ),
            )
        except Exception:
            pass

    # ── TTS pre-warm (#12) ────────────────────────────────────────────────
    try:
        prewarm_fn = getattr(session.tts, "prewarm", None)
        if callable(prewarm_fn):
            maybe_coro = prewarm_fn()
            if asyncio.iscoroutine(maybe_coro):
                await maybe_coro
            logger.info("[TTS] Pre-warmed successfully")
        else:
            logger.debug("[TTS] Pre-warm not supported by current TTS plugin")
    except Exception as e:
        logger.debug(f"[TTS] Pre-warm skipped: {e}")

    logger.info(
        "[AGENT] Pipeline live: Soniox STT → Gemini LLM → %s TTS — waiting for caller.",
        live_config.get("_effective_tts_provider", "gemini"),
    )
    call_start_time = now_ist()

    # ── Recording → LiveKit Egress (background — don't block first spoken line) ──
    egress_id = None
    recording_started = False
    recording_url = ""
    recording_ready = asyncio.Event()

    async def _start_recording_bg() -> None:
        nonlocal egress_id, recording_started, recording_url
        try:
            rec_result = await start_recording(ctx.room.name)
            if rec_result.get("success"):
                egress_id = rec_result.get("egress_id")
                recording_url = rec_result.get("recording_url", "")
                recording_started = True
                logger.info("[RECORDING] Started egress=%s room=%s", egress_id, ctx.room.name)
            else:
                logger.warning("[RECORDING] Failed: %s", rec_result.get("error"))
        except Exception as e:
            logger.warning("[RECORDING] Failed to start recording: %s", e)
        finally:
            recording_ready.set()

    safe_create_task(_start_recording_bg(), name="recording_start")

    # ── Upsert active_calls (#38) ─────────────────────────────────────────
    async def upsert_active_call(status: str):
        nonlocal active_calls_enabled
        if not active_calls_enabled:
            return
        try:
            sb = await db.get_supabase()
            if sb:
                sb.table("active_calls").upsert({
                    "room_id":     ctx.room.name,
                    "phone":       caller_phone,
                    "caller_name": caller_name,
                    "status":      status,
                    "last_updated": now_utc().isoformat(),
                }).execute()
        except Exception as e:
            err = str(e)
            if "PGRST205" in err and "active_calls" in err.lower():
                active_calls_enabled = False
                logger.info("[ACTIVE-CALL] Table not found; skipping active_calls updates.")
            else:
                logger.debug(f"[ACTIVE-CALL] {e}")

    await upsert_active_call("active")

    try:
        from language_router import resolve_lang_preset
        from local_live_monitor import call_started as _local_call_started

        _local_call_started(
            ctx.room.name,
            phone=caller_phone,
            caller_name=caller_name,
            preset=resolve_lang_preset(live_config.get("lang_preset"), live_config),
            direction="outbound" if dispatch_phone_number else "inbound",
        )
    except Exception:
        pass

    # ── Session event handlers ────────────────────────────────────────────
    quota_failure_announced = False
    leaked_key_failure_announced = False

    @session.on("error")
    def _on_session_error(ev):
        nonlocal quota_failure_announced, leaked_key_failure_announced
        llm_error = getattr(ev, "error", None)
        is_llm_error = getattr(llm_error, "type", "") == "llm_error"
        is_recoverable = bool(getattr(llm_error, "recoverable", False))
        wrapped_error = getattr(llm_error, "error", llm_error)
        err_text = str(wrapped_error or ev or "")
        err_lower = err_text.lower()

        if is_llm_error and "insufficient_quota" in err_lower:
            logger.error("[LLM] Provider returned insufficient_quota (429). This is an API/billing-side issue.")
            # If no fallback providers are available and the error is unrecoverable,
            # close gracefully so callers don't experience prolonged silence.
            if (not quota_failure_announced) and (len(llm_candidates) <= 1) and (not is_recoverable):
                quota_failure_announced = True

                async def _graceful_quota_close():
                    try:
                        await agent.speak_gated(
                            "I'm facing a temporary service issue right now. Please call again in a few minutes.",
                            add_to_chat_ctx=False,
                        )
                    except Exception as e:
                        logger.warning(f"[LLM] Failed to speak quota failure message: {e}")
                    finally:
                        await agent_tools.end_call()

                safe_create_task(_graceful_quota_close(), name='quota_close')
        elif is_llm_error and ("reported as leaked" in err_lower or "permission_denied" in err_lower):
            logger.error("[LLM] Provider rejected API key (403 permission denied / leaked key).")
            if (not leaked_key_failure_announced) and (len(llm_candidates) <= 1) and (not is_recoverable):
                leaked_key_failure_announced = True

                async def _graceful_leaked_key_close():
                    try:
                        await agent.speak_gated(
                            "I am facing a temporary service issue. Please call again shortly.",
                            add_to_chat_ctx=False,
                        )
                    except Exception as e:
                        logger.warning(f"[LLM] Failed to speak leaked-key failure message: {e}")
                    finally:
                        await agent_tools.end_call()

                safe_create_task(_graceful_leaked_key_close(), name='leaked_key_close')

    import interrupt_handler
    from interrupt_handler import create_interrupt_handlers
    from language_router import resolve_lang_preset

    interrupt_handlers = create_interrupt_handlers(
        session,
        agent_tools,
        lang_preset=resolve_lang_preset(live_config.get("lang_preset"), live_config),
        live_config=live_config,
    )

    @session.on("speech_created")
    def _on_speech_created(ev):
        if getattr(ev, "source", None) in ("generate_reply", "say"):
            agent.reset_tts_hold()

    # livekit-agents 1.6.x has no agent_speech_started/finished/interrupted
    # events — derive them from agent_state_changed + conversation_item_added.
    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev):
        new_state = str(getattr(ev, "new_state", ""))
        old_state = str(getattr(ev, "old_state", ""))
        if new_state == "speaking":
            _touch_call_activity(user_speaking=False)
            if silence_guard:
                silence_guard.on_agent_audio_output()
            bc = live_config.get("_backchannel")
            if bc:
                bc.on_agent_speaking()
            interrupt_handlers["speech_started"](ev)
            from speculative_reply import schedule_speculative_prep

            schedule_speculative_prep(agent, agent_tools, live_config)
        elif old_state == "speaking":
            agent.reset_tts_hold()
            _touch_call_activity(user_speaking=False)
            if silence_guard:
                silence_guard.on_agent_speech_finished()
            interrupt_handlers["speech_stopped"](ev)
            from speculative_reply import cancel_speculative_prep

            cancel_speculative_prep(live_config)

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev):
        nonlocal interrupt_count
        item = getattr(ev, "item", None)
        if getattr(item, "role", None) != "assistant" or not getattr(item, "interrupted", False):
            return
        interrupt_handlers["interrupted"](item)
        interrupt_count = interrupt_handler.interrupt_count
        from speculative_reply import cancel_speculative_prep

        cancel_speculative_prep(live_config)

    FILLER_WORDS = {
        "okay.", "okay", "ok", "uh", "hmm", "hm", "yeah", "yes",
        "no", "um", "ah", "oh", "right", "sure", "fine", "good",
        "haan", "han", "theek", "theek hai", "accha", "ji", "ha",
    }

    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev):
        """Side effects only — LiveKit auto-replies via turn_detection=stt."""
        transcript = (ev.transcript or "").strip()
        qs = live_config.get("_quality_scorer")
        if transcript:
            _touch_call_activity(user_speaking=not ev.is_final)
            if ev.is_final:
                if silence_guard:
                    silence_guard.on_user_speech_finished()
                bc = live_config.get("_backchannel")
                if bc:
                    bc.on_user_final()
            else:
                if silence_guard:
                    silence_guard.on_user_speech_started()
                bc = live_config.get("_backchannel")
                if bc:
                    bc.on_partial_transcript(transcript)

        if not ev.is_final:
            return

        if not transcript:
            return

        _touch_call_activity(user_speaking=False)
        if qs:
            # Soniox STT endpoint = user stopped speaking; start voice-to-voice TTFA clock.
            qs.record_user_speech_finished()
            qs.record_user_final_transcript()

        if user_wants_to_end_call(transcript, turn_count=agent_tools.turn_count):
            logger.info("[CALL-ENDING] Farewell detected: '%s'", transcript)

            async def _graceful_end_on_phrase():
                agent_tools._user_requested_hangup = True
                try:
                    await session.interrupt()
                except Exception as e:
                    logger.debug("[CALL-ENDING] interrupt before goodbye failed: %s", e)
                try:
                    await agent.speak_session_line(goodbye_msg, add_to_chat_ctx=False)
                except Exception as e:
                    logger.debug("[CALL-ENDING] End phrase goodbye failed: %s", e)
                finally:
                    await agent_tools.end_call()

            safe_create_task(_graceful_end_on_phrase(), name="end_phrase_close")
            return

        if len(transcript) < 3:
            return
        if transcript.lower().rstrip(".") in FILLER_WORDS:
            return

        safe_create_task(_log_transcript("user", transcript), name="log_transcript")
        try:
            from local_live_monitor import log_event as _local_log_event

            _local_log_event(ctx.room.name, "user", transcript, event="transcript")
        except Exception:
            pass
        safe_create_task(_handle_brochure_intent(transcript), name="brochure_intent")
        safe_create_task(_handle_site_visit_intent(transcript), name="site_visit_intent")
        from context_summariser import maybe_summarise_context
        from secondary_llm import SECONDARY_LLM_PROVIDER
        safe_create_task(
            maybe_summarise_context(agent._chat_ctx, agent_tools.turn_count, SECONDARY_LLM_PROVIDER),
            name="context_summarise",
        )

    @ctx.room.on("participant_disconnected")
    def on_participant_disconnected(participant):
        logger.info(f"[HANGUP] Participant disconnected: {participant.identity}")
        identity = participant.identity or ""
        sip_id = agent_tools._sip_identity or ""
        if identity == sip_id or identity.startswith("sip_"):
            agent_tools._caller_disconnected = True
        agent_tools.agent_is_speaking = False
        async def _safe_shutdown_after_disconnect():
            try:
                await unified_shutdown_hook(ctx)
            except Exception as e:
                logger.exception(f"[SHUTDOWN] Unhandled exception in disconnect hook: {e}")
                # Production fail-safe: persist at least a minimal call log even if
                # the main shutdown workflow crashes, so dashboard data is not lost.
                try:
                    fallback_duration = int((now_utc() - call_start_time).total_seconds())
                    fallback_messages = agent.chat_ctx.messages
                    if callable(fallback_messages):
                        fallback_messages = fallback_messages()
                    lines = []
                    for msg in fallback_messages:
                        if getattr(msg, "role", None) in ("user", "assistant"):
                            content = getattr(msg, "content", "")
                            if isinstance(content, list):
                                content = " ".join(str(c) for c in content if isinstance(c, str))
                            lines.append(f"[{msg.role.upper()}] {content}")
                    fallback_transcript = "\n".join(lines) if lines else "unavailable"

                    fallback_phone = normalize_phone_number(livekit_caller_phone)
                    if not is_valid_phone_number(fallback_phone):
                        candidate_phone = normalize_phone_number(caller_phone)
                        fallback_phone = candidate_phone if is_valid_phone_number(candidate_phone) else (caller_phone or "unknown")

                    from db import save_call_log as _save_call_log
                    await _save_call_log(
                        phone=fallback_phone,
                        duration=max(0, fallback_duration),
                        transcript=fallback_transcript,
                        summary=f"Call ended with shutdown recovery: {str(e)[:180]}",
                        call_purpose="general_inquiry",
                        call_summary="Recovered minimal log after shutdown hook failure.",
                        caller_name=agent_tools.caller_name or "",
                        was_booked=bool(agent_tools.booking_intent),
                    )
                except Exception as fallback_err:
                    logger.exception(f"[SHUTDOWN] Fallback persistence also failed: {fallback_err}")
        safe_create_task(_safe_shutdown_after_disconnect(), name='safe_shutdown')

    # ══════════════════════════════════════════════════════════════════════
    # POST-CALL SHUTDOWN HOOK
    # ══════════════════════════════════════════════════════════════════════

    async def unified_shutdown_hook(shutdown_ctx: JobContext):
        nonlocal shutdown_done, livekit_caller_phone, caller_phone
        async with shutdown_lock:
            if shutdown_done:
                logger.info("[SHUTDOWN] Already processed — skipping duplicate trigger.")
                return
            shutdown_done = True

        await stop_background_audio(background_audio_player)
        if silence_guard:
            silence_guard.on_call_ended()
            await silence_guard.stop()
        try:
            from conversion_metrics import record_call_completed

            record_call_completed(
                campaign_id=getattr(agent_tools, "_re_campaign_id", None),
                phone=caller_phone,
            )
        except Exception as conv_exc:
            logger.debug("[CONVERSION] call record failed: %s", conv_exc)
        quality_scorer.metrics.call_duration_sec = int((now_utc() - call_start_time).total_seconds())
        quality_scorer.metrics.total_turns = agent_tools.turn_count
        quality_scorer.metrics.interruptions = interrupt_count
        quality_payload = quality_scorer.to_dict()
        logger.info("[QUALITY] %s", quality_payload)
        if _sentry_dsn:
            with sentry_sdk.push_scope() as scope:
                scope.set_context("call_quality", quality_payload)
                scope.set_tag("prompt_version", live_config.get("prompt_version", "stable"))
                scope.set_tag("is_canary", str(bool(live_config.get("is_canary"))))
                sentry_sdk.capture_message("call_quality_report", level="info")
        logger.info("[SHUTDOWN] Sequence started.")
        try:
            from turn_context_injection import get_turn_inject_latency_stats

            inj_stats = get_turn_inject_latency_stats()
            if inj_stats.get("all", {}).get("count", 0) > 0:
                logger.info("[TURN-INJECT] call latency stats: %s", inj_stats)
        except Exception:
            pass

        try:
            from local_live_monitor import call_ended as _local_call_ended

            _local_call_ended(
                shutdown_ctx.room.name,
                reason=str(agent_tools.call_ended_by or "completed"),
                extra={"duration_sec": int((now_utc() - call_start_time).total_seconds())},
            )
        except Exception:
            pass

        duration = int((now_utc() - call_start_time).total_seconds())

        # Booking
        booking_status_msg = "No booking"
        appointment_time = None
        if agent_tools.booking_intent:
            try:
                from calendar_tools import async_create_booking
                from site_visit_booking import (
                    confirm_site_visit_from_booking_intent,
                    looks_like_site_visit,
                    site_visit_summary_line,
                )

                intent = agent_tools.booking_intent
                appointment_time = intent["start_time"]
                notes = str(intent.get("notes") or "")
                is_site_visit = bool(
                    intent.get("is_site_visit")
                    or intent.get("listing_id")
                    or intent.get("property_name")
                    or looks_like_site_visit(notes)
                )

                intent_phone = normalize_phone_number(intent.get("caller_phone"))
                if is_valid_phone_number(intent_phone):
                    caller_phone_for_booking = intent_phone
                else:
                    fallback_phone = normalize_phone_number(agent_tools.caller_phone)
                    caller_phone_for_booking = fallback_phone if is_valid_phone_number(fallback_phone) else agent_tools.caller_phone

                caller_name_for_booking = (intent.get("caller_name") or "").strip()
                if not caller_name_for_booking:
                    caller_name_for_booking = caller_phone_for_booking or agent_tools.caller_name or "Caller"

                if is_site_visit:
                    visit_result = None
                    if not agent_tools._site_visit_records:
                        visit_result = await confirm_site_visit_from_booking_intent(
                            intent,
                            scheduled_keys=agent_tools._scheduled_site_visits,
                            room_id=ctx.room.name,
                            listing_id=str(intent.get("listing_id") or ""),
                            property_name=str(intent.get("property_name") or ""),
                        )
                        if visit_result and visit_result.get("record"):
                            agent_tools._site_visit_records.append(visit_result["record"])
                    if agent_tools._site_visit_records:
                        booking_status_msg = site_visit_summary_line(agent_tools._site_visit_records[0])
                        if visit_result and visit_result.get("whatsapp_sent"):
                            booking_status_msg += " WhatsApp confirmation sent."
                        elif visit_result and not visit_result.get("whatsapp_sent"):
                            booking_status_msg += " WhatsApp delivery failed."
                    else:
                        booking_status_msg = "Site visit noted but WhatsApp confirmation failed."
                else:
                    result = await async_create_booking(
                        start_time=intent["start_time"],
                        caller_name=caller_name_for_booking,
                        caller_phone=caller_phone_for_booking,
                        notes=intent["notes"],
                    )
                    if not result.get("success") and "timed out" in str(result.get("message", "")).lower():
                        logger.warning("[BOOKING] First booking attempt timed out. Retrying once...")
                        result = await async_create_booking(
                            start_time=intent["start_time"],
                            caller_name=caller_name_for_booking,
                            caller_phone=caller_phone_for_booking,
                            notes=intent["notes"],
                        )
                    if result.get("success"):
                        notify_booking_confirmed(
                            caller_name=caller_name_for_booking,
                            caller_phone=caller_phone_for_booking,
                            booking_time_iso=intent["start_time"],
                            booking_id=result.get("booking_id"),
                            notes=intent["notes"],
                            tts_voice=tts_voice,
                            ai_summary="",
                        )
                        booking_status_msg = f"Booking Confirmed: {result.get('booking_id')}"
                    else:
                        booking_status_msg = f"Booking Failed: {result.get('message')}"
            except Exception as e:
                booking_status_msg = f"Booking Failed: {e}"
                logger.exception(f"[BOOKING] Shutdown booking step failed: {e}")
        elif agent_tools._site_visit_records:
            from site_visit_booking import site_visit_summary_line

            first_visit = agent_tools._site_visit_records[0]
            appointment_time = first_visit.get("appointment_time")
            booking_status_msg = site_visit_summary_line(first_visit)
            if len(agent_tools._site_visit_records) > 1:
                booking_status_msg += f" (+{len(agent_tools._site_visit_records) - 1} more)"
        else:
            try:
                notify_call_no_booking(
                    caller_name=agent_tools.caller_name,
                    caller_phone=agent_tools.caller_phone,
                    call_summary="Caller did not schedule during this call.",
                    tts_voice=tts_voice,
                    duration_seconds=duration,
                )
            except Exception as e:
                logger.warning(f"[BOOKING] No-booking notification failed: {e}")

        # Build transcript
        transcript_text = ""
        try:
            messages = agent.chat_ctx.messages
            if callable(messages):
                messages = messages()
            lines = []
            for msg in messages:
                if getattr(msg, "role", None) in ("user", "assistant"):
                    content = getattr(msg, "content", "")
                    if isinstance(content, list):
                        content = " ".join(str(c) for c in content if isinstance(c, str))
                    lines.append(f"[{msg.role.upper()}] {content}")
            transcript_text = "\n".join(lines)
        except Exception as e:
            logger.error(f"[SHUTDOWN] Transcript read failed: {e}")
            transcript_text = "unavailable"

        # Cost estimation (#34)
        def estimate_cost(dur: int, chars: int) -> float:
            return round(
                (dur / 60) * 0.002 +
                (dur / 60) * 0.006 +
                (chars / 1000) * 0.003 +
                (chars / 4000) * 0.0001,
                5
            )
        estimated_cost = estimate_cost(duration, len(transcript_text))
        logger.info(f"[COST] Estimated: ${estimated_cost}")

        site_visit_booked = bool(agent_tools._site_visit_records)

        # ── Unified post-call analysis (one DeepSeek call) ──
        precomputed_analysis = None
        ds_summary = None
        sentiment = "neutral"
        call_purpose = "general_inquiry"
        call_summary = booking_status_msg
        try:
            from deepseek_summariser import format_summary_for_db
            from re_voice_memory.extract import analyze_post_call

            if transcript_text and transcript_text != "unavailable":
                precomputed_analysis = await analyze_post_call(transcript_text)
                sentiment = precomputed_analysis.sentiment
                ds_summary = dict(precomputed_analysis.summary)
                ds_call_summary, ds_call_purpose = format_summary_for_db(ds_summary)
                if ds_call_summary and ds_call_summary != "Call completed.":
                    call_summary = ds_call_summary
                    call_purpose = ds_call_purpose
                logger.info(
                    "[POST-CALL] Unified: sentiment=%s summary=%s",
                    sentiment,
                    ds_call_summary[:80] if ds_call_summary else "",
                )
            else:
                logger.debug("[POST-CALL] Skipping analysis — no transcript")
        except Exception as e:
            logger.warning(f"[POST-CALL] Unified analysis failed: {e}")
            try:
                call_purpose, call_summary = infer_call_insights(
                    transcript_text=transcript_text,
                    booking_status_msg=booking_status_msg,
                    booking_detected=bool(agent_tools.booking_intent) or site_visit_booked,
                    sentiment="neutral",
                )
            except Exception as insight_err:
                logger.warning(f"[INSIGHTS] Fallback failed: {insight_err}")
                call_purpose, call_summary = "general_inquiry", booking_status_msg

        if site_visit_booked and not agent_tools.booking_intent:
            call_purpose = "site_visit"
            if "site visit" not in (call_summary or "").lower():
                call_summary = booking_status_msg

        # Analytics timestamps (#19)
        ist = pytz.timezone("Asia/Kolkata")
        call_dt = call_start_time.astimezone(ist)

        # Stop recording (wait briefly if background start is still in flight)
        recording_url = None
        if not egress_id:
            try:
                await asyncio.wait_for(recording_ready.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        if egress_id:
            stop_result = await stop_recording(egress_id, ctx.room.name)
            if stop_result.get("success"):
                recording_url = stop_result.get("recording_url") or ""
                logger.warning(f"[RECORDING] Stopped egress={egress_id} url={recording_url}")
            else:
                logger.warning(f"[RECORDING] Stop failed: {stop_result.get('error')}")
        else:
            logger.warning(f"[RECORDING] recording_url={recording_url} (no egress to stop)")

        # Update active_calls to completed (#38)
        try:
            await upsert_active_call("completed")
        except Exception as e:
            logger.warning(f"[ACTIVE_CALL] Could not mark call completed: {e}")

        # n8n webhook (#39)
        _n8n_url = os.getenv("N8N_WEBHOOK_URL")
        if _n8n_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(_n8n_url, json={
                        "event":        "call_completed",
                        "phone":        caller_phone,
                        "caller_name":  agent_tools.caller_name,
                        "duration":     duration,
                        "booked":       bool(agent_tools.booking_intent),
                        "sentiment":    sentiment,
                        "summary":      booking_status_msg,
                        "recording_url":recording_url,
                        "interrupt_count": interrupt_count,
                    })
                    resp.raise_for_status()
                logger.info("[N8N] Webhook triggered")
            except httpx.TimeoutException:
                logger.warning("[N8N] Webhook timed out — skipping")
            except httpx.HTTPStatusError as e:
                logger.warning(f"[N8N] Webhook returned {e.response.status_code}")
            except Exception as e:
                logger.warning(f"[N8N] Webhook failed: {e}")

        # Save to Supabase
        from db import save_call_log
        resolved_log_phone = normalize_phone_number(livekit_caller_phone)
        if not is_valid_phone_number(resolved_log_phone):
            candidate_phone = normalize_phone_number(caller_phone)
            resolved_log_phone = candidate_phone if is_valid_phone_number(candidate_phone) else caller_phone

        if not is_valid_phone_number(resolved_log_phone):
            # Final recovery pass: SIP metadata is often fully populated by call end.
            try:
                metadata_candidates = []
                for _, participant in ctx.room.remote_participants.items():
                    attr = participant.attributes or {}
                    preferred_keys = (
                        "sip.phoneNumber",
                        "sip.phone_number",
                        "phoneNumber",
                        "phone_number",
                        "phone",
                        "sip.from",
                        "sip.to",
                        "sip.caller_number",
                        "sip.callerNumber",
                    )
                    for key in preferred_keys:
                        metadata_candidates.append(attr.get(key))
                    metadata_candidates.append(getattr(participant, "identity", ""))
                    metadata_candidates.append(getattr(participant, "name", ""))
                    metadata_candidates.append(getattr(participant, "metadata", ""))

                for candidate in metadata_candidates:
                    extracted = _extract_valid_phone_from_text(candidate)
                    if extracted and is_valid_phone_number(extracted):
                        resolved_log_phone = extracted
                        break

                if is_valid_phone_number(resolved_log_phone):
                    livekit_caller_phone = resolved_log_phone
                    caller_phone = resolved_log_phone
                    agent_tools.caller_phone = resolved_log_phone
                    logger.info(f"[CALLER-ID] Recovered caller phone at shutdown: {resolved_log_phone}")
            except Exception as e:
                logger.warning(f"[CALLER-ID] Final phone recovery pass failed: {e}")

        # ── Remember-caller button: gates ALL post-call memory writes ──
        remember_caller = bool(live_config.get("remember_caller_enabled", True))
        if not remember_caller:
            logger.info("[MEMORY] remember_caller disabled — skipping caller history + extraction")

        # ── Caller history: persist summary for next-call prewarm injection ──
        if remember_caller and is_valid_phone_number(resolved_log_phone) and call_summary:
            async def _persist_caller_history() -> None:
                try:
                    from re_voice_memory.caller_history import append_call_history

                    await asyncio.to_thread(
                        append_call_history,
                        resolved_log_phone,
                        caller_name=agent_tools.caller_name or "",
                        summary=call_summary,
                        call_at=call_dt.isoformat(),
                    )
                except Exception as hist_err:
                    logger.warning("[CALLER-HISTORY] Save failed: %s", hist_err)

            safe_create_task(_persist_caller_history(), name="caller_history_save")

        # ── NEW: Generate raw.md transcript artifact ──
        raw_md_path = None
        try:
            ist_tz = pytz.timezone("Asia/Kolkata")
            end_time_str = datetime.now(ist_tz).strftime("%I:%M %p")
            start_time_str = call_start_time.astimezone(ist_tz).strftime("%I:%M %p")

            transcript_entries = []
            messages = agent.chat_ctx.messages
            if callable(messages):
                messages = messages()
            for msg in messages:
                role = str(getattr(msg, "role", "")).lower()
                if role not in ("user", "assistant"):
                    continue
                content = getattr(msg, "content", "")
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content if isinstance(c, str))

                tool_calls = []
                if role == "assistant":
                    for tc in getattr(msg, "tool_calls", []) or []:
                        tool_calls.append({
                            "name": str(getattr(tc, "name", "unknown")),
                            "result": str(getattr(tc, "result", "")),
                        })

                transcript_entries.append({
                    "role": role,
                    "content": content,
                    "timestamp": str(getattr(msg, "timestamp", "")),
                    "tool_calls": tool_calls,
                })

            # ── RE Voice OS: Generate canonical call transcript ──
            from re_voice_memory.raw_md import generate_raw_md as re_generate_raw_md
            from re_voice_memory.constants import MEMORY_DIR as RE_MEMORY_DIR

            # Drain context buffer for metadata
            buffered = agent_tools.drain_context_buffer()
            listings_discussed = list({b.get("listing_id") for b in buffered if b.get("listing_id")})
            viewing_requests = [b["value"] for b in buffered if b["key"] == "viewing_scheduled"]
            offer_intent_raw = next((b for b in buffered if b["key"] == "offer_intent"), None)
            offer_intent = None
            if offer_intent_raw:
                parts = offer_intent_raw["value"].split(":")
                offer_intent = {"listing_id": parts[0], "amount": parts[1] if len(parts) > 1 else ""}

            raw_md_path = await re_generate_raw_md(
                room_name=ctx.room.name,
                call_date=call_dt.date().isoformat(),
                start_time=start_time_str,
                end_time=end_time_str,
                duration=duration,
                caller_phone=resolved_log_phone if is_valid_phone_number(resolved_log_phone) else caller_phone,
                caller_name=agent_tools.caller_name or "Unknown",
                lead_role=getattr(agent_tools, '_lead_role', 'buyer'),
                deal_stage=getattr(agent_tools, '_deal_stage_before', 'unknown'),
                campaign_id=getattr(agent_tools, '_re_campaign_id', None),
                listings_discussed=listings_discussed,
                viewing_requests=viewing_requests,
                offer_intent=offer_intent,
                intent_score_before=getattr(agent_tools, '_intent_score_before', None),
                booking_status=booking_status_msg,
                sentiment=sentiment,
                turn_count=agent_tools.turn_count,
                interrupt_count=interrupt_count,
                system_prompt=str(getattr(agent, "instructions", "")),
                transcript_lines=transcript_entries,
                output_dir=RE_MEMORY_DIR,
            )
            logger.info(f"[RAW-MD] Generated: {raw_md_path}")
        except Exception as e:
            logger.warning(f"[RAW-MD] Failed to generate: {e}")
            buffered = agent_tools.drain_context_buffer()

        # ── RE Voice OS: Extract durable memories + fire dream + CRM ──
        # Gated by the remember-caller button: OFF = no durable memory writes.
        if remember_caller:
            try:
                from re_voice_memory.extract import extract_call_memories as re_extract
                from re_voice_memory.dream import run_dream_pipeline

                effective_phone = resolved_log_phone if is_valid_phone_number(resolved_log_phone) else caller_phone

                # Fire extraction as background task (non-blocking)
                safe_create_task(re_extract(
                    caller_phone=effective_phone,
                    caller_name=agent_tools.caller_name or "Unknown",
                    transcript_text=transcript_text,
                    buffered_context=buffered,
                    raw_md_path=raw_md_path,
                    output_dir=RE_MEMORY_DIR,
                    precomputed_analysis=precomputed_analysis,
                    duration=duration,
                ))

                # Dream pipeline (CRM sync runs inside re_extract with real scores)
                safe_create_task(run_dream_pipeline(effective_phone, RE_MEMORY_DIR), name='dream_pipeline')

                logger.info("[RE-EXTRACT] Post-call pipeline scheduled")
            except Exception as e:
                logger.warning(f"[RE-EXTRACT] Failed to schedule: {e}")
        resolved_call_status = "completed"
        if bool(agent_tools.booking_intent):
            resolved_call_status = "completed"
        elif "voicemail" in str(booking_status_msg or "").lower():
            resolved_call_status = "voicemail"
        elif is_outbound_job and not _transcript_has_user_speech(transcript_text):
            if duration < 12:
                resolved_call_status = "no_answer"
            elif duration < 30 and not bool(agent_tools.booking_intent):
                resolved_call_status = "no_answer"

        from call_audit import build_audit_report

        audit_report = build_audit_report(
            transcript_text,
            quality_scores=quality_payload,
        )
        from speculative_reply import get_speculative_journal

        extracted_info: dict = {
            "quality_scores": quality_payload,
            "call_audit": audit_report,
            "speculative_thinking": get_speculative_journal(live_config),
            "prompt_version": live_config.get("prompt_version"),
            "is_canary": bool(live_config.get("is_canary")),
        }
        post_call_cfg = getattr(full_agent_config, "post_call_action", None) if full_agent_config else None
        if post_call_cfg and getattr(post_call_cfg, "extracted_variables", None):
            for var in post_call_cfg.extracted_variables:
                name = getattr(var, "name", None) or (var.get("name") if isinstance(var, dict) else None)
                if name:
                    # Populate from known data sources
                    name_lower = name.lower().replace(" ", "_")
                    if name_lower in ("caller_name", "name", "full_name"):
                        extracted_info[name] = agent_tools.caller_name or ""
                    elif name_lower in ("phone", "phone_number", "caller_phone"):
                        extracted_info[name] = resolved_log_phone
                    elif name_lower == "booking_status":
                        extracted_info[name] = (
                            "booked"
                            if (agent_tools.booking_intent or site_visit_booked)
                            else "not_booked"
                        )
                    elif name_lower in ("call_purpose", "purpose", "program_interest"):
                        extracted_info[name] = call_purpose or ""
                    else:
                        extracted_info[name] = None

        post_call_payload = {
            "agent_id": dynamic_agent_id,
            "phone": resolved_log_phone,
            "caller_name": agent_tools.caller_name or "",
            "duration_seconds": duration,
            "room_id": ctx.room.name,
            "summary": booking_status_msg,
            "call_summary": call_summary,
            "transcript": transcript_text,
            "sentiment": sentiment,
            "recording_url": recording_url,
            "extracted_info": extracted_info,
        }
        try:
            await execute_post_call_actions(
                post_call_cfg,
                call_status=resolved_call_status,
                call_data=post_call_payload,
            )
        except Exception as post_err:
            logger.warning("[POST-CALL] Delivery failed: %s", post_err)

        logger.warning(f"[SAVE] Saving call_log: phone={resolved_log_phone} duration={duration}s recording_url={'set' if recording_url else 'empty'} call_ended_by={getattr(agent_tools, 'call_ended_by', 'caller')}")
        save_result = await save_call_log(
            phone=resolved_log_phone,
            duration=duration,
            transcript=transcript_text,
            summary=booking_status_msg,
            appointment_time=appointment_time,
            call_purpose=call_purpose or live_config.get("agent_purpose", ""),
            call_summary=call_summary,
            recording_url=recording_url,
            caller_name=agent_tools.caller_name or "",
            sentiment=sentiment,
            estimated_cost_usd=estimated_cost,
            call_date=call_dt.date().isoformat(),
            call_hour=call_dt.hour,
            call_day_of_week=call_dt.strftime("%A"),
            was_booked=bool(agent_tools.booking_intent) or site_visit_booked,
            interrupt_count=interrupt_count,
            ended_by=getattr(agent_tools, "call_ended_by", "caller"),
            agent_id=dynamic_agent_id,
            call_status=resolved_call_status,
            call_direction="outbound" if is_outbound_job else "inbound",
            channel_type="phone",
            room_id=ctx.room.name,
            from_number=resolved_log_phone if not is_outbound_job else (sip_from_number or None),
            to_number=(sip_to_number if not is_outbound_job else None) or (resolved_log_phone if is_outbound_job else None),
            extracted_info=extracted_info or None,
            ai_summary=ds_summary,
            bulk_call_id=bulk_call_id,
        )
        if not save_result.get("success"):
            logger.warning(f"[SAVE] save_call_log FAILED: {save_result.get('message', 'unknown error')}")
        else:
            logger.warning(f"[SAVE] save_call_log SUCCEEDED for {resolved_log_phone}")
            call_log_data = save_result.get("data")
            call_log_id = None
            if call_log_data:
                call_log_id = call_log_data[0].get("id") if isinstance(call_log_data, list) else call_log_data.get("id")
            await _update_campaign_contact_from_call(
                bulk_call_id=bulk_call_id,
                phone=resolved_log_phone,
                call_status=resolved_call_status,
                duration=duration,
                call_log_id=call_log_id,
            )
            # Update recording_url on the inserted row if stop_recording provided it
            call_log_data = save_result.get("data")
            if call_log_data and recording_url:
                call_log_id = call_log_data[0].get("id") if isinstance(call_log_data, list) else call_log_data.get("id")
                if call_log_id:
                    try:
                        sb_update = await db.get_supabase()
                        if sb_update:
                            sb_update.table("call_logs").update({
                                "recording_url": recording_url
                            }).eq("id", call_log_id).execute()
                            logger.warning(f"[SAVE] Updated call_log {call_log_id} with recording_url")
                    except Exception as e:
                        logger.warning(f"[SAVE] Failed to update recording_url on call_log {call_log_id}: {e}")

        # Backfill transcript rows so call_transcripts always has both user and assistant turns.
        if call_transcripts_enabled and transcript_text and transcript_text != "unavailable":
            try:
                sb = await db.get_supabase()
                if sb:
                    existing = (
                        sb.table("call_transcripts")
                        .select("role")
                        .eq("call_room_id", ctx.room.name)
                        .execute()
                    )
                    existing_roles = {str(r.get("role", "")).lower() for r in (existing.data or [])}
                    rows = []
                    for line in transcript_text.splitlines():
                        line = line.strip()
                        if line.startswith("[USER] "):
                            role = "user"
                            content = line[len("[USER] "):].strip()
                        elif line.startswith("[ASSISTANT] "):
                            role = "assistant"
                            content = line[len("[ASSISTANT] "):].strip()
                        else:
                            continue
                        if not content:
                            continue
                        if existing_roles and "user" in existing_roles and role == "user":
                            continue
                        rows.append(
                            {
                                "call_room_id": ctx.room.name,
                                "phone": normalize_phone_number(livekit_caller_phone),
                                "role": role,
                                "content": content,
                            }
                        )
                    if rows:
                        sb.table("call_transcripts").insert(rows).execute()
            except Exception as e:
                err = str(e)
                if "PGRST205" in err and "call_transcripts" in err.lower():
                    logger.info("[TRANSCRIPT-BACKFILL] call_transcripts table missing; run supabase_migration_v2.sql.")
                else:
                    logger.debug(f"[TRANSCRIPT-BACKFILL] {e}")

    ctx.add_shutdown_callback(unified_shutdown_hook)


# ══════════════════════════════════════════════════════════════════════════════
# WORKER ENTRY
# ══════════════════════════════════════════════════════════════════════════════

def _prewarm_fnc(proc):
    """Fast process init — VAD only. Soniox http_session created lazily in async entrypoint."""
    from worker_prewarm import load_vad

    proc.userdata["vad"] = load_vad()
    proc.userdata["soniox_http_session"] = None
    proc.userdata["prewarmed"] = False


if __name__ == "__main__":
    _worker_agent_name = os.environ.get("LIVEKIT_AGENT_NAME", "inbound-voice-demo")
    try:
        _num_idle = max(1, min(int(os.environ.get("AGENT_NUM_IDLE_PROCESSES", "4")), 16))
    except (TypeError, ValueError):
        _num_idle = 4
    cli.run_app(WorkerOptions(
        entrypoint_fnc=entrypoint,
        prewarm_fnc=_prewarm_fnc,
        agent_name=_worker_agent_name,
        num_idle_processes=_num_idle,
        initialize_process_timeout=30.0,
    ))
