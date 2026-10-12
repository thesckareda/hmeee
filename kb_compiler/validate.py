"""
Stage D — VALIDATE (deterministic, blocking — the trust layer).

The grounding check is the core promise: every number the compiler put in a
spoken artifact must exist in the source corpus. Ungrounded numbers FAIL the
build — a hallucinated price talking to real customers is the one unforgivable
failure mode.
"""
from __future__ import annotations

import re
from typing import Any

_OBJECTION_KEYS = (
    "price_too_high", "need_to_think", "other_builder_cheaper",
    "location_far", "will_wait_for_rates_to_fall", "not_ready_to_move",
)

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?\s*%?")


def _normalize_number(raw: str) -> str:
    return raw.replace(",", "").replace(" ", "").strip()


def _corpus_numbers(chunks: list[dict]) -> set[str]:
    numbers: set[str] = set()
    for chunk in chunks:
        for m in _NUMBER.finditer(chunk.get("text", "")):
            numbers.add(_normalize_number(m.group(0)))
            numbers.add(_normalize_number(m.group(0)).rstrip("%"))
    return numbers


def _spoken_strings(artifacts: dict) -> list[tuple[str, str]]:
    """(artifact_path, text) for every string a caller could hear."""
    out: list[tuple[str, str]] = []
    ck = artifacts.get("company_knowledge") or {}
    for i, entry in enumerate(ck.get("faq") or []):
        out.append((f"faq[{i}].answer", str(entry.get("answer") or "")))
    for i, fact in enumerate(ck.get("hot_facts") or []):
        out.append((f"hot_facts[{i}]", str(fact)))
    inv = artifacts.get("inventory") or {}
    for i, prop in enumerate(inv.get("properties") or []):
        out.append((f"inventory[{i}].usp", str(prop.get("usp") or "")))
        out.append((f"inventory[{i}].price_range", str(prop.get("price_range") or "")))
    mc = artifacts.get("market_competitor") or {}
    for key, text in (mc.get("objection_scripts") or {}).items():
        out.append((f"objection_scripts.{key}", str(text)))
    for key, text in (mc.get("objection_stories") or {}).items():
        out.append((f"objection_stories.{key}", str(text)))
    persona = artifacts.get("persona") or {}
    out.append(("persona.first_line", str(persona.get("first_line") or "")))
    return out


def check_grounding(artifacts: dict, chunks: list[dict]) -> list[str]:
    """Every number in spoken output must exist in the sources. Blocking."""
    allowed = _corpus_numbers(chunks)
    # Small conversational numbers are fine ("2 sentences", "10 minutes", "1-2 options")
    trivial = {str(n) for n in range(0, 61)}
    errors: list[str] = []
    for path, text in _spoken_strings(artifacts):
        for m in _NUMBER.finditer(text):
            norm = _normalize_number(m.group(0)).rstrip("%")
            if norm in trivial or not norm:
                continue
            if norm not in allowed and _normalize_number(m.group(0)) not in allowed:
                errors.append(f"UNGROUNDED NUMBER '{m.group(0).strip()}' in {path}")
    return errors


def check_schemas(artifacts: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    ck = artifacts.get("company_knowledge") or {}
    faqs = ck.get("faq") or []
    if not faqs:
        errors.append("company_knowledge: no FAQ entries")
    seen_q: dict[str, str] = {}
    for i, entry in enumerate(faqs):
        if not entry.get("id") or not entry.get("answer"):
            errors.append(f"faq[{i}]: missing id/answer")
            continue
        questions = entry.get("questions") or []
        if len(questions) < 4:
            warnings.append(f"faq[{entry['id']}]: only {len(questions)} paraphrases (want 8-15)")
        if len(str(entry.get("answer"))) > 350:
            warnings.append(f"faq[{entry['id']}]: answer over 350 chars — too long for voice")
        for q in questions:
            nq = re.sub(r"\W+", " ", str(q).lower()).strip()
            if nq in seen_q and seen_q[nq] != entry["id"]:
                errors.append(f"faq collision: '{q}' maps to both {seen_q[nq]} and {entry['id']}")
            seen_q[nq] = entry["id"]
    if len(ck.get("hot_facts") or []) > 8:
        errors.append("hot_facts: more than 8 (dilutes the pinned context)")

    inv = artifacts.get("inventory") or {}
    props = inv.get("properties") or []
    if not props:
        warnings.append("inventory: zero items — knowledge base had no sellable items?")
    seen_ids: set[str] = set()
    for i, prop in enumerate(props):
        lid = str(prop.get("listing_id") or "")
        if not lid or not prop.get("name"):
            errors.append(f"inventory[{i}]: missing listing_id/name")
        if lid in seen_ids:
            errors.append(f"inventory: duplicate listing_id {lid}")
        seen_ids.add(lid)
        units = prop.get("units_left")
        if units is not None and not isinstance(units, int):
            errors.append(f"inventory[{lid}]: units_left must be integer or null (honest scarcity)")
        if not str(prop.get("usp") or "").strip():
            warnings.append(f"inventory[{lid}]: empty usp — agent will have no differentiator")

    mc = artifacts.get("market_competitor") or {}
    scripts = mc.get("objection_scripts") or {}
    stories = mc.get("objection_stories") or {}
    for key in _OBJECTION_KEYS:
        if not str(scripts.get(key) or "").strip():
            warnings.append(f"objection_scripts.{key}: missing")
        if not str(stories.get(key) or "").strip():
            warnings.append(f"objection_stories.{key}: missing")

    persona = artifacts.get("persona") or {}
    if not str(persona.get("agent_instructions") or "").strip():
        errors.append("persona: missing agent_instructions")
    if not str(persona.get("first_line") or "").strip():
        errors.append("persona: missing first_line")
    first_line = str(persona.get("first_line") or "")
    if len(first_line.split()) > 26:
        warnings.append("persona.first_line: over ~20 words — too long for a greeting")

    return errors, warnings


def validate_artifacts(artifacts: dict, chunks: list[dict]) -> dict[str, Any]:
    errors, warnings = check_schemas(artifacts)
    errors.extend(check_grounding(artifacts, chunks))
    ck = artifacts.get("company_knowledge") or {}
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "faq_entries": len(ck.get("faq") or []),
            "faq_paraphrases": sum(len(e.get("questions") or []) for e in ck.get("faq") or []),
            "hot_facts": len(ck.get("hot_facts") or []),
            "items": len((artifacts.get("inventory") or {}).get("properties") or []),
            "objection_stories": len((artifacts.get("market_competitor") or {}).get("objection_stories") or {}),
        },
    }
