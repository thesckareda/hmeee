"""
Objection classification and persistence system.

Stores objections in YAML frontmatter (not markdown table) for robust parsing.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

from re_voice_memory.constants import MEMORY_DIR
from re_voice_memory.lead import _get_phone_lock
from voice_memory.fs import atomic_write_text

ObjectionType = Literal["price", "location", "size", "amenity", "timing", "agent_trust", "process", "other"]
Outcome = Literal["resolved", "unresolved", "escalated", "deferred"]


@dataclass
class Objection:
    type: ObjectionType
    listing_id: str | None
    raw_phrase: str
    script_used: str
    outcome: Outcome
    call_date: str  # ISO8601
    call_count_at_time: int


OBJECTION_PATTERNS: dict[str, list[str]] = {
    "price": ["too expensive", "out of budget", "too high", "can't afford", "overpriced", "costly"],
    "location": ["too far", "wrong area", "connectivity", "commute", "not there", "different area"],
    "size": ["too small", "not enough space", "cramped", "need bigger", "sq ft"],
    "amenity": ["no parking", "no gym", "no pool", "no lift", "no society", "no security"],
    "timing": ["not ready", "just looking", "maybe later", "not now", "next year", "exploring"],
    "agent_trust": ["how do I know", "seen too many", "been cheated", "not sure", "verify"],
    "process": ["too complicated", "paperwork", "legal", "registration", "stamp duty"],
}

PLAYBOOK_SCRIPTS: dict[str, str] = {
    "price": "price_comparison_script",
    "location": "area_alternative_script",
    "size": "alternative_listing_script",
    "amenity": "feature_tradeoff_script",
    "timing": "nurture_low_commitment_script",
    "agent_trust": "trust_establishment_script",
    "process": "process_simplification_script",
}


def detect_objection_type(phrase: str) -> str | None:
    """Return first matched objection type for a lowercase keyword match, or None."""
    lower = phrase.lower()
    for obj_type, triggers in OBJECTION_PATTERNS.items():
        for trigger in triggers:
            if trigger in lower:
                return obj_type
    return None


def _log_path(phone: str) -> Path:
    return MEMORY_DIR / "leads" / phone / "objection_log.md"


def load_objection_log(phone: str) -> list[Objection]:
    """Read the objection log. Returns [] if missing or unparseable."""
    path = _log_path(phone)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []

    if not _HAS_YAML or yaml is None:
        return []

    # Parse YAML frontmatter
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return []

    try:
        fm_raw = "\n".join(lines[1:end_idx])
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        return []

    # Objections are stored as a YAML list under the 'objections' key
    objection_data = fm.get("objections", [])
    if not isinstance(objection_data, list):
        return []

    objections: list[Objection] = []
    for obj_dict in objection_data:
        if not isinstance(obj_dict, dict):
            continue
        try:
            objections.append(Objection(
                type=obj_dict.get("type", "other"),
                listing_id=obj_dict.get("listing_id") or None,
                raw_phrase=obj_dict.get("raw_phrase", ""),
                script_used=obj_dict.get("script_used", "generic"),
                outcome=obj_dict.get("outcome", "unresolved"),
                call_date=obj_dict.get("call_date", ""),
                call_count_at_time=obj_dict.get("call_count_at_time", 0),
            ))
        except Exception:
            continue
    return objections


async def append_objection(phone: str, objection: Objection) -> list[Objection]:
    """Load, append an objection, persist, and return the updated list (per-phone locked)."""
    lock = await _get_phone_lock(phone)
    async with lock:
        objections = load_objection_log(phone)
        objections.append(objection)
        _save_objection_log(phone, objections)
        return objections


def _save_objection_log(phone: str, objections: list[Objection]) -> Path:
    """Persist objection list as YAML frontmatter."""
    path = _log_path(phone)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not _HAS_YAML or yaml is None:
        raise RuntimeError("yaml module is not available; install PyYAML")

    # Serialize objections as list of dicts
    obj_data = []
    for o in objections:
        obj_data.append({
            "type": o.type,
            "listing_id": o.listing_id or "",
            "raw_phrase": o.raw_phrase,
            "script_used": o.script_used,
            "outcome": o.outcome,
            "call_date": o.call_date,
            "call_count_at_time": o.call_count_at_time,
        })

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    metadata: dict[str, Any] = {
        "count": len(objections),
        "last_updated": now_iso,
        "ttl_days": 365,
        "type": "objection_log",
        "objections": obj_data,
    }

    yaml_str = yaml.safe_dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip("\n")
    content = f"---\n{yaml_str}\n---\n"
    atomic_write_text(path, content)
    return path


def get_failed_scripts(phone: str) -> list[str]:
    """Return script names where outcome=unresolved across all calls."""
    objections = load_objection_log(phone)
    return sorted({o.script_used for o in objections if o.outcome == "unresolved"})


def get_dominant_objection_type(phone: str) -> str | None:
    """Return the most frequent objection type, or None if log is empty."""
    objections = load_objection_log(phone)
    if not objections:
        return None
    counts: dict[str, int] = {}
    for o in objections:
        counts[o.type] = counts.get(o.type, 0) + 1
    return max(counts, key=lambda k: counts[k])


def format_objection_history_for_prompt(phone: str, max_tokens: int = 80) -> str:
    """Return compact summary truncated to max_tokens via token_counter."""
    objections = load_objection_log(phone)
    if not objections:
        return ""

    grouped: dict[str, dict[Outcome, int]] = {}
    for o in objections:
        if o.type not in grouped:
            grouped[o.type] = {}
        grouped[o.type][o.outcome] = grouped[o.type].get(o.outcome, 0) + 1

    parts: list[str] = []
    for obj_type in sorted(grouped.keys()):
        outcomes = grouped[obj_type]
        total = sum(outcomes.values())
        outcome_strs = ", ".join(f"{outcome}" for outcome, _ in outcomes.items())
        parts.append(f"{obj_type} (x{total}, {outcome_strs})")

    result = "Past objections: " + ", ".join(parts) + "."
    from token_counter import truncate_to_token_budget_sync

    return truncate_to_token_budget_sync(result, max_tokens, "hme")
