"""
Live brochure intent detection during calls.

Fast keyword matching for obvious requests; DeepSeek for ambiguous cases.
Sends the matching PDF brochure to the caller's WhatsApp number.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from openai import OpenAI

from phone_utils import is_valid_phone_number
from secondary_llm import DEEPSEEK_MODEL, get_deepseek_api_key, get_deepseek_config
from send_whatsapp_media import BROCHURE_ALIASES, BROCHURES

logger = logging.getLogger("inbound-voice-agent")

PROJECT_PATTERNS: dict[str, list[str]] = {
    "godrej": [r"godrej", r"regal\s*pavilion"],
    "myhome": [r"my[\s-]*home", r"myhome", r"udyan"],
    "signature": [r"signature\s*developers?", r"signature"],
    "housing": [r"housing[\s-]*is", r"housing"],
    "indroyal": [r"ind[\s-]*royal", r"indroyal"],
    "tricolt": [r"tricolt", r"tri[\s-]*colt"],
    "trilight": [r"trilight", r"tri[\s-]*light", r"thetrilight"],
}

BROCHURE_SIGNALS = (
    "brochure", "e-brochure", "e brochure", "pdf", "pamphlet", "leaflet",
    "send me", "send on", "whatsapp", "whats app", "whatsaap", "whats app pe",
    "share", "bhejo", "bhej do", "bhejdo", "document", "details on whatsapp",
    "mail me", "forward",
)

INTEREST_SIGNALS = (
    "interested in", "tell me about", "more about", "know more",
    "information about", "details about", "want to see", "show me",
)

SEND_SIGNALS = ("send", "share", "whatsapp", "bhej", "forward", "mail")

def _build_system_prompt() -> str:
    brochure_lines = "\n".join(
        f"- {key} ({info['label']})" for key, info in sorted(BROCHURES.items())
    )
    keys = "|".join(sorted(BROCHURES.keys()))
    return f"""You detect if a real-estate phone caller wants a property brochure sent on WhatsApp.

Available brochures (return exactly these keys or null):
{brochure_lines}

Return JSON only:
{{"wants_brochure": true|false, "brochure": "{keys}"|null, "confidence": 0.0-1.0}}

Rules:
- wants_brochure=true only if the caller clearly asks for a brochure/pdf/details to be sent or shared on WhatsApp.
- brochure=null if they want a brochure but did not specify which project.
- brochure=specific key only if that project is clearly requested in recent speech.
- General property questions without send/share intent: wants_brochure=false.
"""


SYSTEM_PROMPT = _build_system_prompt()

_deepseek_client: OpenAI | None = None


def _normalize_key(raw: str) -> str | None:
    key = BROCHURE_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    return key if key in BROCHURES else None


def detect_brochure_keyword(transcript: str) -> str | None:
    text = transcript.lower()
    for key, patterns in PROJECT_PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return key
    return None


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def has_brochure_signal(transcript: str) -> bool:
    return _contains_any(transcript.lower(), BROCHURE_SIGNALS)


def has_interest_signal(transcript: str) -> bool:
    return _contains_any(transcript.lower(), INTEREST_SIGNALS)


def should_analyze_transcript(transcript: str) -> bool:
    """Gate DeepSeek calls to utterances with plausible brochure intent."""
    if has_brochure_signal(transcript):
        return True
    project = detect_brochure_keyword(transcript)
    if not project:
        return False
    text = transcript.lower()
    return has_interest_signal(transcript) or _contains_any(text, SEND_SIGNALS)


def fast_detect_brochure(transcript: str) -> str | None:
    project = detect_brochure_keyword(transcript)
    if not project:
        return None
    if has_brochure_signal(transcript):
        return project
    if has_interest_signal(transcript) and _contains_any(transcript.lower(), SEND_SIGNALS):
        return project
    return None


def _get_deepseek_client() -> OpenAI | None:
    global _deepseek_client
    if _deepseek_client is not None:
        return _deepseek_client
    if not get_deepseek_api_key():
        logger.debug("[BROCHURE-INTENT] DEEPSEEK_API_KEY not set")
        return None
    api_key, base_url, _ = get_deepseek_config()
    _deepseek_client = OpenAI(api_key=api_key, base_url=base_url)
    return _deepseek_client


def _deepseek_detect(transcript: str, recent_utterances: list[str]) -> dict:
    client = _get_deepseek_client()
    if not client:
        return {"wants_brochure": False, "brochure": None, "confidence": 0.0}

    context = "\n".join(f"Caller: {utterance}" for utterance in recent_utterances[-6:])
    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Recent caller speech:\n{context}\n\n"
                        f"Latest utterance:\nCaller: {transcript}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=80,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        brochure = parsed.get("brochure")
        if brochure is not None:
            parsed["brochure"] = _normalize_key(str(brochure))
        return parsed
    except Exception as exc:
        logger.warning("[BROCHURE-INTENT] DeepSeek failed: %s", exc)
        return {"wants_brochure": False, "brochure": None, "confidence": 0.0}


async def _send_brochure_async(caller_phone: str, brochure_key: str) -> dict | None:
    from send_whatsapp_media import send_brochure_via_whatsapp

    try:
        return await asyncio.to_thread(send_brochure_via_whatsapp, caller_phone, brochure_key)
    except Exception as exc:
        logger.error("[BROCHURE-INTENT] WhatsApp send failed: %s", exc)
        return None


async def _dispatch_brochure(
    caller_phone: str,
    brochure_key: str,
    sent_brochures: set[str],
    in_flight: set[str] | None = None,
    on_sent=None,
) -> dict | None:
    if not is_valid_phone_number(caller_phone):
        logger.warning("[BROCHURE-INTENT] Invalid caller phone: %s", caller_phone)
        return None
    if brochure_key in sent_brochures:
        logger.info("[BROCHURE-INTENT] Already sent %s this call", brochure_key)
        return None

    if in_flight is not None:
        if brochure_key in in_flight:
            return None
        in_flight.add(brochure_key)

    try:
        result = await _send_brochure_async(caller_phone, brochure_key)
        if result:
            sent_brochures.add(brochure_key)
            logger.info(
                "[BROCHURE-INTENT] Sent %s brochure to %s",
                result.get("label", brochure_key),
                caller_phone,
            )
            if on_sent:
                on_sent(brochure_key, result.get("label", brochure_key))
        return result
    finally:
        if in_flight is not None:
            in_flight.discard(brochure_key)


async def send_brochure_if_mapped(
    caller_phone: str,
    listing_id: str,
    sent_brochures: set[str],
    in_flight: set[str] | None = None,
) -> dict | None:
    key = _normalize_key(listing_id)
    if not key:
        logger.warning("[BROCHURE-INTENT] Unknown brochure key: %s", listing_id)
        return None
    return await _dispatch_brochure(caller_phone, key, sent_brochures, in_flight)


async def maybe_send_brochure_from_transcript(
    transcript: str,
    recent_utterances: list[str],
    caller_phone: str,
    sent_brochures: set[str],
    in_flight: set[str] | None = None,
    on_sent=None,
) -> dict | None:
    cleaned = transcript.strip()
    if not cleaned:
        return None

    fast = fast_detect_brochure(cleaned)
    if fast:
        logger.info("[BROCHURE-INTENT] Fast match: %s", fast)
        return await _dispatch_brochure(
            caller_phone, fast, sent_brochures, in_flight, on_sent
        )

    if not should_analyze_transcript(cleaned):
        return None

    analysis = await asyncio.to_thread(_deepseek_detect, cleaned, recent_utterances)
    logger.info("[BROCHURE-INTENT] DeepSeek result: %s", analysis)

    if not analysis.get("wants_brochure"):
        return None
    if float(analysis.get("confidence", 0)) < 0.6:
        return None

    brochure_key = analysis.get("brochure")
    if not brochure_key:
        return None

    return await _dispatch_brochure(
        caller_phone, brochure_key, sent_brochures, in_flight, on_sent
    )