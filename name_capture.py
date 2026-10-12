"""
Live in-call caller name + relation capture — IQ plan 0.3 and #4.

Regex-first, never guesses: a name is captured only from an explicit
self-introduction phrase, or from a capitalized token after a soft intro.
Low confidence → leave unset; primitives degrade to name-less variants.

Zero I/O on the turn path: kv write-through is fire-and-forget.
"""
from __future__ import annotations

import asyncio
import logging
import re

logger = logging.getLogger("inbound-voice-agent")

# Explicit self-introduction — any following token is trusted (title-cased)
_EXPLICIT_INTRO = re.compile(
    r"(?:na\s+peru|naa\s+peru|mera\s+naam|mera\s+name|my\s+name\s+is|myself|"
    r"this\s+is|naan\s+peru)\s+([A-Za-z]{3,20})\b",
    re.I,
)

# Soft intro ("nenu X", "I'm X") — only trusted when the token is capitalized
_SOFT_INTRO = re.compile(
    r"(?:\bnenu\b|\bi\s*am\b|\bi'm\b|\bmain\b)\s+([A-Z][a-z]{2,19})\b",
)

# Words that follow intro phrases but are never names
_NAME_STOPWORDS = frozenset({
    "looking", "interested", "calling", "asking", "searching", "thinking",
    "ready", "good", "fine", "okay", "busy", "driving", "sorry", "here",
    "just", "not", "also", "still", "actually", "waiting", "from",
    "cheppanu", "chestunnanu", "matladutunnanu", "unnanu", "adugutunnanu",
})

# IQ #4 — live social-graph capture ("na wife Priya", "mera bhai Rahul")
_RELATION_PATTERN = re.compile(
    r"(?:\bna\b|\bnaa\b|\bmera\b|\bmeri\b|\bmy\b)\s+"
    r"(wife|husband|father|mother|brother|sister|son|daughter|"
    r"nanna|amma|bhai|behen|didi|mission?es|wifey)\s+"
    r"(?:garu\s+|ji\s+)?([A-Z][a-z]{2,19})\b",
    re.I,
)

_RELATION_CANON = {
    "nanna": "father", "amma": "mother", "bhai": "brother",
    "behen": "sister", "didi": "sister", "missies": "wife", "wifey": "wife",
}


def _clean_name(token: str) -> str | None:
    name = (token or "").strip().strip(".,!?")
    if not name or name.lower() in _NAME_STOPWORDS:
        return None
    if not name.isalpha() or not (3 <= len(name) <= 20):
        return None
    return name.title()


def extract_caller_name(transcript: str) -> str | None:
    """Pure extraction — returns a name or None. Never guesses."""
    text = transcript or ""
    m = _EXPLICIT_INTRO.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name
    m = _SOFT_INTRO.search(text)
    if m:
        return _clean_name(m.group(1))
    return None


def extract_relations(transcript: str) -> list[dict]:
    """IQ #4 — [{'name': 'Priya', 'relation': 'wife'}] from live speech."""
    relations: list[dict] = []
    for m in _RELATION_PATTERN.finditer(transcript or ""):
        relation = _RELATION_CANON.get(m.group(1).lower(), m.group(1).lower())
        name = _clean_name(m.group(2))
        if name:
            relations.append({"name": name, "relation": relation})
    return relations


def _write_through_name(phone: str, name: str) -> None:
    """Fire-and-forget kv hot-context write so the NEXT call has the name instantly."""
    if not phone or phone in ("unknown", "demo"):
        return

    async def _write() -> None:
        try:
            from re_voice_memory.kv_store import kv_set
            await kv_set(f"lead:{phone}:live_name", name, ttl_seconds=365 * 86400)
        except Exception:
            pass

    try:
        asyncio.get_running_loop()
        asyncio.create_task(_write())
    except RuntimeError:
        pass  # no loop (unit tests) — skip silently


def capture_from_transcript(transcript: str, live_config: dict) -> None:
    """Turn-path entry point (~0ms). Sets live_config keys, never blocks."""
    if not live_config.get("_caller_name"):
        name = extract_caller_name(transcript)
        if name:
            live_config["_caller_name"] = name
            live_config["_caller_name_unconfirmed"] = True
            # Persist only when the remember-caller button is ON;
            # in-call use of the name is always fine (session-scoped).
            if live_config.get("remember_caller_enabled", True):
                phone = str(live_config.get("_re_lead_phone") or "")
                _write_through_name(phone, name)
            logger.debug("[NAME-CAPTURE] Live caller name: %s", name)

    new_relations = extract_relations(transcript)
    if new_relations:
        existing: list[dict] = live_config.setdefault("_relations", [])
        known = {(r.get("name"), r.get("relation")) for r in existing}
        for rel in new_relations:
            if (rel["name"], rel["relation"]) not in known:
                existing.append(rel)
        live_config["_relations_line"] = "; ".join(
            f"{r['name']} ({r['relation']})" for r in existing
        )
