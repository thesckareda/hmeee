"""
Live site visit intent detection during calls.

Fast keyword matching for obvious requests; DeepSeek for date/time extraction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from openai import OpenAI

from phone_utils import is_valid_phone_number
from secondary_llm import DEEPSEEK_MODEL, get_deepseek_api_key, get_deepseek_config
from send_whatsapp_media import BROCHURE_ALIASES, BROCHURES, resolve_property

logger = logging.getLogger("inbound-voice-agent")

SITE_VISIT_SIGNALS = (
    "site visit", "sitevisit", "site-visit", "property visit", "visit the property",
    "visit the site", "come see", "see the property", "see the flat", "see the villa",
    "book a visit", "schedule a visit", "schedule visit", "visit tomorrow",
    "visit on", "visit at", "when can i visit", "want to visit", "like to visit",
    "arrange a visit", "fix a visit", "set up a visit",
)

CONFIRM_SIGNALS = (
    "confirm", "book it", "book that", "yes please", "sounds good", "works for me",
    "that works", "okay", "ok sure", "please book", "go ahead", "schedule it",
)

PROPERTY_PATTERNS: dict[str, list[str]] = {
    "godrej": [r"godrej", r"regal\s*pavilion"],
    "myhome": [r"my[\s-]*home", r"myhome", r"udyan"],
    "signature": [r"signature"],
    "housing": [r"housing"],
    "indroyal": [r"ind[\s-]*royal", r"indroyal"],
    "tricolt": [r"tricolt", r"tri[\s-]*colt"],
    "trilight": [r"trilight", r"tri[\s-]*light"],
    "trident": [r"trident", r"triednt"],
    "kokapet": [r"kokapet"],
    "gachibowli": [r"gachibowli"],
    "narsingi": [r"narsingi"],
    "neopolis": [r"neopolis"],
}

LOCATION_ALIASES = {
    "kokapet": "Kokapet, Hyderabad",
    "gachibowli": "Gachibowli, Hyderabad",
    "narsingi": "Narsingi, Hyderabad",
    "neopolis": "Neopolis, Hyderabad",
}

_deepseek_client: OpenAI | None = None


def _build_system_prompt() -> str:
    properties = ", ".join(sorted(BROCHURES.keys()))
    return f"""You detect if a real-estate phone caller wants to book a property site visit.

Known projects: {properties}, kokapet, gachibowli, narsingi, neopolis

Return JSON only:
{{
  "wants_site_visit": true|false,
  "property": "property or project name"|null,
  "location": "area or city"|null,
  "requested_datetime": "YYYY-MM-DD HH:MM"|null,
  "confidence": 0.0-1.0
}}

Rules:
- wants_site_visit=true when caller asks to visit, book a site visit, or confirms a suggested visit time.
- requested_datetime: extract only if a specific date and/or time is mentioned; use 24h format.
- If agent suggested a visit and caller agrees ("yes", "sounds good", "book it"), wants_site_visit=true.
- General curiosity without booking intent: wants_site_visit=false.
"""


SYSTEM_PROMPT = _build_system_prompt()


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def has_site_visit_signal(transcript: str) -> bool:
    return _contains_any(transcript.lower(), SITE_VISIT_SIGNALS)


def detect_property_keyword(transcript: str) -> str | None:
    text = transcript.lower()
    for key, patterns in PROPERTY_PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return key
    return None


def should_analyze_transcript(transcript: str) -> bool:
    text = transcript.lower()
    if has_site_visit_signal(transcript):
        return True
    if detect_property_keyword(transcript) and _contains_any(text, CONFIRM_SIGNALS):
        return True
    return bool(re.search(r"\b(tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text))


def _get_deepseek_client() -> OpenAI | None:
    global _deepseek_client
    if _deepseek_client is not None:
        return _deepseek_client
    if not get_deepseek_api_key():
        return None
    api_key, base_url, _ = get_deepseek_config()
    _deepseek_client = OpenAI(api_key=api_key, base_url=base_url)
    return _deepseek_client


def _deepseek_detect(transcript: str, recent_utterances: list[str]) -> dict:
    client = _get_deepseek_client()
    if not client:
        return {
            "wants_site_visit": False,
            "property": None,
            "location": None,
            "requested_datetime": None,
            "confidence": 0.0,
        }

    context = "\n".join(f"Caller: {u}" for u in recent_utterances[-8:])
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
            max_tokens=120,
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as exc:
        logger.warning("[SITE-VISIT-INTENT] DeepSeek failed: %s", exc)
        return {
            "wants_site_visit": False,
            "property": None,
            "location": None,
            "requested_datetime": None,
            "confidence": 0.0,
        }


def _resolve_property_labels(property_key: str | None) -> tuple[str, str, str]:
    if not property_key:
        return "property", "", ""
    resolved = resolve_property(property_key)
    if resolved:
        return resolved["key"], resolved["label"], ""
    key = BROCHURE_ALIASES.get(property_key.strip().lower(), property_key.strip().lower())
    if key in LOCATION_ALIASES:
        return key, key.title(), LOCATION_ALIASES[key]
    return property_key, property_key.title(), ""


def _default_datetime_if_missing(requested: str | None) -> str | None:
    if requested and requested.strip():
        return requested.strip()
    return None


async def maybe_book_site_visit_from_transcript(
    transcript: str,
    recent_utterances: list[str],
    caller_phone: str,
    caller_name: str,
    scheduled_keys: set[str],
    room_id: str | None = None,
    on_confirmed=None,
) -> dict | None:
    cleaned = transcript.strip()
    if not cleaned or not is_valid_phone_number(caller_phone):
        return None

    if not should_analyze_transcript(cleaned):
        return None

    analysis = await asyncio.to_thread(_deepseek_detect, cleaned, recent_utterances)
    logger.info("[SITE-VISIT-INTENT] DeepSeek result: %s", analysis)

    if not analysis.get("wants_site_visit"):
        return None
    if float(analysis.get("confidence", 0)) < 0.65:
        return None

    requested_datetime = _default_datetime_if_missing(analysis.get("requested_datetime"))
    if not requested_datetime:
        return None

    property_raw = analysis.get("property") or detect_property_keyword(cleaned)
    listing_id, property_name, location = _resolve_property_labels(
        str(property_raw) if property_raw else detect_property_keyword(cleaned)
    )
    if analysis.get("location"):
        location = str(analysis["location"])

    from site_visit_booking import confirm_site_visit

    result = await confirm_site_visit(
        caller_phone=caller_phone,
        caller_name=caller_name,
        listing_id=listing_id,
        requested_datetime=requested_datetime,
        property_name=property_name,
        location=location,
        room_id=room_id,
        scheduled_keys=scheduled_keys,
    )
    if result.get("success") and result.get("record") and on_confirmed:
        on_confirmed(result["record"])
    return result if result.get("success") else None