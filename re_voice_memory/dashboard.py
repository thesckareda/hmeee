"""
Dashboard API helpers — expose RE Voice memory by phone for CRM UI.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from phone_utils import normalize_phone_number

from re_voice_memory.constants import MEMORY_DIR
from re_voice_memory.lead import load_lead_profile
from re_voice_memory.objection_log import (
    format_objection_history_for_prompt,
    get_dominant_objection_type,
    get_failed_scripts,
    load_objection_log,
)
from re_voice_memory.property_graph import (
    format_graph_for_prompt,
    get_liked_listings,
    get_rejected_listings,
    load_property_graph,
)


def _format_budget(min_val: int | None, max_val: int | None) -> str:
    def _fmt(amt: int | None) -> str:
        if amt is None:
            return "?"
        if amt >= 10_000_000:
            return f"₹{amt / 10_000_000:.1f} Cr"
        if amt >= 100_000:
            return f"₹{amt / 100_000:.0f} L"
        return f"₹{amt:,}"

    if min_val or max_val:
        return f"{_fmt(min_val)} – {_fmt(max_val)}"
    return ""


def _profile_to_dict(profile) -> dict[str, Any]:
    return {
        "lead_id": profile.lead_id,
        "stable_name": profile.stable_name,
        "role": profile.role,
        "budget": _format_budget(profile.budget_min, profile.budget_max),
        "budget_min": profile.budget_min,
        "budget_max": profile.budget_max,
        "preferred_areas": list(profile.preferred_areas or []),
        "excluded_areas": list(profile.excluded_areas or []),
        "must_haves": list(profile.must_haves or []),
        "deal_breakers": list(profile.deal_breakers or []),
        "timeline_weeks": profile.timeline_weeks,
        "loan_status": profile.loan_status,
        "intent_score": round(float(profile.intent_score), 1),
        "deal_stage": profile.deal_stage,
        "call_count": profile.call_count,
        "last_contact_date": profile.last_contact_date,
        "language_pref": profile.language_pref,
        "notes": (profile.notes or "").strip()[:500],
    }


def _scan_memory_calls(phone: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Find raw.md call artifacts whose lead line matches this phone."""
    normalized = normalize_phone_number(phone)
    if not normalized:
        return []

    calls_dir = MEMORY_DIR / "calls"
    if not calls_dir.is_dir():
        return []

    hits: list[dict[str, Any]] = []
    phone_pattern = re.compile(
        rf"\*\*Lead:\*\*.*\({re.escape(normalized)}\)",
        re.I,
    )

    for room_dir in sorted(calls_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not room_dir.is_dir():
            continue
        raw_path = room_dir / "raw.md"
        if not raw_path.is_file():
            continue
        try:
            text = raw_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if normalized not in text and not phone_pattern.search(text):
            continue

        date_m = re.search(r"\*\*Date:\*\*\s*(.+)", text)
        duration_m = re.search(r"\*\*Duration:\*\*\s*(\d+)", text)
        outcome_m = re.search(r"\*\*Outcome:\*\*\s*(.+)", text)
        sentiment_m = re.search(r"\*\*Sentiment:\*\*\s*(.+)", text)
        listings_m = re.search(r"\*\*Listings discussed:\*\*\s*(.+)", text)

        hits.append({
            "room_name": room_dir.name,
            "call_date": (date_m.group(1).strip() if date_m else ""),
            "duration_seconds": int(duration_m.group(1)) if duration_m else 0,
            "outcome": (outcome_m.group(1).strip() if outcome_m else ""),
            "sentiment": (sentiment_m.group(1).strip() if sentiment_m else ""),
            "listings_discussed": (listings_m.group(1).strip() if listings_m else ""),
            "source": "re_voice_memory",
        })
        if len(hits) >= limit:
            break

    return hits


def get_lead_memory_bundle(phone: str) -> dict[str, Any]:
    """
    Aggregate all durable memory for a lead keyed by phone number.
    Used by CRM dashboard to show full interaction history beyond call_logs.
    """
    normalized = normalize_phone_number(phone)
    if not normalized:
        return {"phone": phone, "has_memory": False, "error": "invalid_phone"}

    profile = load_lead_profile(normalized)
    graph = load_property_graph(normalized)
    objections = load_objection_log(normalized)
    memory_calls = _scan_memory_calls(normalized)

    liked = get_liked_listings(normalized)
    rejected = get_rejected_listings(normalized)

    signals = []
    for sig in (graph.signals if graph else []):
        signals.append({
            "listing_id": sig.listing_id,
            "signal": sig.signal,
            "price_reaction": sig.price_reaction,
            "call_date": sig.call_date,
        })

    objection_rows = [
        {
            "type": o.type,
            "raw_phrase": o.raw_phrase[:120],
            "script_used": o.script_used,
            "outcome": o.outcome,
            "call_date": o.call_date,
        }
        for o in objections[:15]
    ]

    return {
        "phone": normalized,
        "has_memory": bool(profile or signals or objections or memory_calls),
        "profile": _profile_to_dict(profile) if profile else None,
        "property_interests": {
            "liked": liked,
            "rejected": rejected,
            "signals": signals,
            "summary": format_graph_for_prompt(normalized, max_tokens=80),
        },
        "objections": {
            "history_summary": format_objection_history_for_prompt(normalized, max_tokens=80),
            "dominant_type": get_dominant_objection_type(normalized),
            "failed_scripts": get_failed_scripts(normalized),
            "entries": objection_rows,
        },
        "memory_calls": memory_calls,
        "memory_dir": str(MEMORY_DIR / "leads" / normalized),
    }