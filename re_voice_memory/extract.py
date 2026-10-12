"""Post-call extraction pipeline — intent scoring, fact extraction, lead updates."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from re_voice_memory.constants import MEMORY_DIR, LEAD_MEMORY_TTL_DAYS
from re_voice_memory.intent_score import IntentScores, _SCORING_RUBRIC, stage_from_intent
from voice_memory.fs import atomic_write_text

logger = logging.getLogger(__name__)


@dataclass
class PostCallAnalysis:
    """Single LLM pass: sentiment, dashboard summary, intent scores, and lead facts."""

    sentiment: str
    summary: dict[str, Any]
    scores: IntentScores | None
    facts: dict[str, Any] = field(default_factory=dict)


def _resolve_llm_config(llm_provider: str) -> tuple[str, str, str]:
    if llm_provider == "deepseek":
        from secondary_llm import get_deepseek_config
        return get_deepseek_config()

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = "gpt-4o-mini"
    if llm_provider == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    elif llm_provider == "groq":
        api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
        model = "llama-3.3-70b-versatile"
    return api_key, base_url.rstrip("/"), model


_FACT_SYSTEM_PROMPT = (
    "You are a real estate lead intelligence system. "
    "Extract structured facts from call transcripts. "
    "Always respond in JSON only. No preamble. No markdown."
)

_FACT_USER_TEMPLATE = """Extract structured facts from this real estate call transcript.

Transcript:
---
{transcript}
---

Return a single JSON object with these exact keys:
  budget_mentioned (boolean): any price/budget discussed
  budget_min (integer or null): minimum budget in INR lakhs
  budget_max (integer or null): maximum budget in INR lakhs
  budget_raw (string or null): verbatim budget quote
  areas_mentioned (list of strings): areas/neighbourhoods discussed
  must_haves_mentioned (list of strings): must-have features
  deal_breakers (list of strings): deal-breaking constraints
  timeline_weeks (integer or null): expected move-in timeline in weeks
  timeline_raw (string or null): verbatim timeline quote
  loan_status (string): "pre_approved", "exploring", "cash", or "unknown"
  decision_factors (list of strings): factors influencing decision
  objections (list of objects): each with "type", "phrase", "outcome"
  stable_name (string or null): caller's name if mentioned
  role (string): "buyer", "seller", "renter", or "investor"
  language_pref (string): "en", "hi", "hinglish", "tamil", or "telugu"
  assigned_agent (string or null): agent mentioned
  sentiment (string): "positive", "neutral", "negative"
  summary_sentence (string): one-sentence call summary
  viewing_scheduled (boolean): property viewing arranged
  offer_intent (object or null): {{"listing_id": "...", "amount": ...}}
  relation_names (list of objects): family/decision-makers mentioned, each {{"name": "...", "relation": "wife/husband/father/...", "context": "why they matter"}}
  slot_values (object): final discussed values as short strings or null: {{"budget": "...", "location": "...", "bhk": "...", "timeline": "...", "purpose": "..."}}
  objection_outcomes (list of objects): each {{"type": "...", "script_used": "...", "resolved": true/false}}"""


# DEPRECATED: Use extract_all_call_data() instead (single combined LLM call)
async def extract_lead_facts(
    transcript_text: str,
    llm_provider: str = "deepseek",
) -> dict[str, Any]:
    """Extract structured facts from a call transcript via LLM.

    DEPRECATED — replaced by extract_all_call_data which combines
    intent scoring and fact extraction in one LLM call.
    """
    api_key, base_url, model = _resolve_llm_config(llm_provider)

    user_prompt = _FACT_USER_TEMPLATE.format(transcript=transcript_text)

    try:
        url = base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _FACT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 800,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            raw = body["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning("LLM fact extraction failed, returning defaults: %s", exc)
        return {
            "budget_mentioned": False,
            "budget_min": None,
            "budget_max": None,
            "budget_raw": None,
            "areas_mentioned": [],
            "must_haves_mentioned": [],
            "deal_breakers": [],
            "timeline_weeks": None,
            "timeline_raw": None,
            "loan_status": "unknown",
            "decision_factors": [],
            "objections": [],
            "stable_name": None,
            "role": "buyer",
            "language_pref": "en",
            "assigned_agent": None,
            "sentiment": "neutral",
            "summary_sentence": "",
            "viewing_scheduled": False,
            "offer_intent": None,
        }

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON for fact extraction: %s", raw[:200])
        return {
            "budget_mentioned": False,
            "budget_min": None,
            "budget_max": None,
            "budget_raw": None,
            "areas_mentioned": [],
            "must_haves_mentioned": [],
            "deal_breakers": [],
            "timeline_weeks": None,
            "timeline_raw": None,
            "loan_status": "unknown",
            "decision_factors": [],
            "objections": [],
            "stable_name": None,
            "role": "buyer",
            "language_pref": "en",
            "assigned_agent": None,
            "sentiment": "neutral",
            "summary_sentence": "",
            "viewing_scheduled": False,
            "offer_intent": None,
        }


_COMBINED_SYSTEM_PROMPT = (
    "You are a real estate lead intelligence system. "
    "Analyze call transcripts once and return sentiment, call summary, intent scores, and structured facts. "
    "Always respond in JSON only. No preamble. No markdown."
)

_COMBINED_USER_TEMPLATE = """Analyze this real estate call transcript in a single pass.

{scoring_rubric}

Transcript:
---
{transcript}
---

Return one JSON object with these top-level keys:

sentiment (string: positive, neutral, or negative)

summary:
  short_summary (one sentence, max 25 words, plain English)
  call_purpose (specific caller intent, e.g. "Inquiry about 3BHK in Kokapet, budget ₹3Cr")
  outcome (exactly one of: interested, booked, not_interested, follow_up_needed, voicemail)
  lead_score (integer 1-10)
  next_action (what should happen next)
  key_points (list of strings, max 5, each under 15 words)
  objections (list of short objection strings, or empty list)
  action_items (list of follow-up tasks)

intent_scores:
  timeline_urgency (integer 1-10)
  timeline_rationale (one sentence)
  budget_seriousness (integer 1-10)
  budget_rationale (one sentence)
  area_commitment (integer 1-10)
  area_rationale (one sentence)

facts:
  budget_mentioned (boolean)
  budget_min (integer or null, INR lakhs)
  budget_max (integer or null, INR lakhs)
  budget_raw (string or null)
  areas_mentioned (list of strings)
  must_haves_mentioned (list of strings)
  deal_breakers (list of strings)
  timeline_weeks (integer or null)
  timeline_raw (string or null)
  loan_status (string: pre_approved/exploring/cash/unknown)
  decision_factors (list of strings)
  objections (list of objects: {{type, phrase, outcome}})
  stable_name (string or null)
  role (string: buyer/seller/renter/investor)
  language_pref (string: en/hi/hinglish/tamil/telugu)
  assigned_agent (string or null)
  summary_sentence (one sentence — same as short_summary)
  viewing_scheduled (boolean)
  offer_intent (object or null: {{listing_id, amount}})
  relation_names (list of objects: {{name, relation, context}} — family/decision-makers mentioned)
  slot_values (object: {{budget, location, bhk, timeline, purpose}} — final values as short strings or null)
  objection_outcomes (list of objects: {{type, script_used, resolved}})"""


def _default_facts() -> dict[str, Any]:
    return {
        "budget_mentioned": False,
        "budget_min": None,
        "budget_max": None,
        "budget_raw": None,
        "areas_mentioned": [],
        "must_haves_mentioned": [],
        "deal_breakers": [],
        "timeline_weeks": None,
        "timeline_raw": None,
        "loan_status": "unknown",
        "decision_factors": [],
        "objections": [],
        "stable_name": None,
        "role": "buyer",
        "language_pref": "en",
        "assigned_agent": None,
        "summary_sentence": "",
        "viewing_scheduled": False,
        "offer_intent": None,
        "relation_names": [],
        "slot_values": {},
        "objection_outcomes": [],
    }


def _default_summary(transcript_text: str = "") -> dict[str, Any]:
    lines = [line.strip() for line in (transcript_text or "").splitlines() if line.strip()]
    user_lines = sum(1 for line in lines if line.startswith("[USER]"))
    total_turns = sum(1 for line in lines if line.startswith("[USER]") or line.startswith("[ASSISTANT]"))
    outcome = "interested" if user_lines > 2 else "voicemail" if total_turns == 0 else "follow_up_needed"
    return {
        "short_summary": f"Call transcript ({total_turns} turns).",
        "call_purpose": "General conversation",
        "outcome": outcome,
        "lead_score": 3,
        "next_action": "Review transcript manually.",
        "key_points": [],
        "objections": [],
        "action_items": [],
    }


def _fallback_post_call_analysis(transcript_text: str = "") -> PostCallAnalysis:
    facts = _default_facts()
    summary = _default_summary(transcript_text)
    facts["summary_sentence"] = summary["short_summary"]
    return PostCallAnalysis(
        sentiment="neutral",
        summary=summary,
        scores=None,
        facts=facts,
    )


def _parse_post_call_response(raw: str, transcript_text: str) -> PostCallAnalysis:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON for post-call analysis: %s", raw[:200])
        return _fallback_post_call_analysis(transcript_text)

    sentiment = str(parsed.get("sentiment") or "neutral").strip().lower()
    if sentiment not in ("positive", "neutral", "negative"):
        sentiment = "neutral"

    summary_raw = parsed.get("summary") if isinstance(parsed.get("summary"), dict) else {}
    summary = _default_summary(transcript_text)
    for key in summary:
        if key in summary_raw and summary_raw[key] is not None:
            summary[key] = summary_raw[key]
    try:
        summary["lead_score"] = int(summary.get("lead_score") or 3)
    except (TypeError, ValueError):
        summary["lead_score"] = 3

    intent_raw = parsed.get("intent_scores") if isinstance(parsed.get("intent_scores"), dict) else {}
    scores: IntentScores | None = None
    try:
        scores = IntentScores(
            timeline_urgency=float(intent_raw.get("timeline_urgency", 3)),
            budget_seriousness=float(intent_raw.get("budget_seriousness", 3)),
            area_commitment=float(intent_raw.get("area_commitment", 3)),
        )
        scores.rationale = (
            f"timeline: {intent_raw.get('timeline_rationale', '')}; "
            f"budget: {intent_raw.get('budget_rationale', '')}; "
            f"area: {intent_raw.get('area_rationale', '')}"
        )
    except Exception:
        logger.warning("Failed to construct IntentScores from post-call response")
        scores = None

    facts_raw = parsed.get("facts") if isinstance(parsed.get("facts"), dict) else {}
    facts = _default_facts()
    facts.update({k: v for k, v in facts_raw.items() if k in facts})
    facts["sentiment"] = sentiment
    if not facts.get("summary_sentence"):
        facts["summary_sentence"] = str(summary.get("short_summary") or "")

    return PostCallAnalysis(
        sentiment=sentiment,
        summary=summary,
        scores=scores,
        facts=facts,
    )


async def analyze_post_call(
    transcript_text: str,
    llm_provider: str = "deepseek",
) -> PostCallAnalysis:
    """One LLM call: sentiment, dashboard summary, intent scores, and lead facts."""
    if not transcript_text or transcript_text == "unavailable" or len(transcript_text.strip()) < 20:
        return _fallback_post_call_analysis(transcript_text or "")

    truncated = transcript_text[-100_000:] if len(transcript_text) > 100_000 else transcript_text

    try:
        api_key, base_url, model = _resolve_llm_config(llm_provider)
    except Exception as exc:
        logger.warning("[POST-CALL] LLM config unavailable: %s", exc)
        return _fallback_post_call_analysis(transcript_text)

    user_prompt = _COMBINED_USER_TEMPLATE.format(
        scoring_rubric=_SCORING_RUBRIC,
        transcript=truncated,
    )

    try:
        url = base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _COMBINED_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1600,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            body = resp.json()
            raw = body["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                raw = raw.rsplit("```", 1)[0].strip()
    except Exception as exc:
        logger.warning("[POST-CALL] Unified analysis failed: %s", exc)
        return _fallback_post_call_analysis(transcript_text)

    analysis = _parse_post_call_response(raw, transcript_text)
    logger.info(
        "[POST-CALL] Unified analysis: sentiment=%s outcome=%s lead_score=%s composite=%s",
        analysis.sentiment,
        analysis.summary.get("outcome"),
        analysis.summary.get("lead_score"),
        getattr(analysis.scores, "composite", None),
    )
    return analysis


async def extract_all_call_data(
    transcript_text: str,
    llm_provider: str = "deepseek",
) -> tuple[IntentScores | None, dict[str, Any]]:
    """Backward-compatible wrapper around analyze_post_call()."""
    analysis = await analyze_post_call(transcript_text, llm_provider=llm_provider)
    return analysis.scores, analysis.facts


def _parse_budget(s: str | None) -> dict[str, Any]:
    """Parse budget info from a raw string like '80L-1.2Cr'."""
    if not s:
        return {"min": None, "max": None, "raw": None}
    s = s.strip().replace("₹", "").replace("Rs.", "").replace("INR", "").strip()
    parts = re.split(r"[-–—to]+", s, maxsplit=1)
    result: dict[str, Any] = {"raw": s}

    def _to_lakhs(val: str) -> int | None:
        val = val.strip().lower().replace(",", "")
        if not val:
            return None
        multiplier = 1
        if "cr" in val:
            multiplier = 100
            val = val.replace("cr", "").strip()
        elif "l" in val or "lakh" in val or "lac" in val:
            val = val.replace("lakh", "").replace("lac", "").replace("l", "").strip()
        elif "k" in val:
            multiplier = 0.1
            val = val.replace("k", "").strip()
        try:
            num = float(val)
            return int(num * multiplier)
        except ValueError:
            return None

    if len(parts) == 1:
        val = _to_lakhs(parts[0])
        result["min"] = val
        result["max"] = val
    else:
        result["min"] = _to_lakhs(parts[0])
        result["max"] = _to_lakhs(parts[1])

    return result


def _parse_timeline_weeks(s: str | None) -> int | None:
    """Convert timeline string like '3 months' to approximate weeks."""
    if not s:
        return None
    s = s.strip().lower()
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(days?|weeks?|months?|years?)")
    match = pattern.search(s)
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2)
    if unit.startswith("day"):
        return max(1, int(num / 7))
    elif unit.startswith("week"):
        return int(num)
    elif unit.startswith("month"):
        return int(num * 4)
    elif unit.startswith("year"):
        return int(num * 52)
    return None


def _merge_unique(existing: list[str], new: list[str]) -> list[str]:
    """Merge two string lists, preserving order and uniqueness (case-insensitive)."""
    seen = {s.lower() for s in existing}
    merged = list(existing)
    for item in new:
        if item.lower() not in seen:
            seen.add(item.lower())
            merged.append(item)
    return merged


async def extract_call_memories(
    caller_phone: str,
    transcript_text: str = "",
    buffered_context: list[dict] | None = None,
    raw_md_path: Path | None = None,
    output_dir: Path | None = None,
    **kwargs: Any,
) -> None:
    """Main extraction pipeline — runs after every call completes.

    Sub-tasks:
      a) Parse raw.md if transcript_text is empty
      b) Call extract_all_call_data for scores + facts
      c) Update lead profile
      d) Write call summary markdown
      e) Log objections
      f) Update property graph
      g) Sync CRM
      h) Emit metrics
    """
    # Per-phone lock to serialise extraction for a single lead
    from re_voice_memory.lead import _get_phone_lock

    lock = await _get_phone_lock(caller_phone)
    async with lock:
        await _extract_call_memories_locked(
            caller_phone=caller_phone,
            transcript_text=transcript_text,
            buffered_context=buffered_context or [],
            raw_md_path=raw_md_path,
            output_dir=output_dir or MEMORY_DIR,
            **kwargs,
        )


async def _extract_call_memories_locked(
    caller_phone: str,
    transcript_text: str,
    buffered_context: list[dict],
    raw_md_path: Path | None,
    output_dir: Path,
    **kwargs: Any,
) -> None:
    # (a) Parse raw.md if transcript_text is empty
    if not transcript_text and raw_md_path is not None:
        transcript_text = _parse_raw_md_to_transcript(raw_md_path)
    if not transcript_text.strip():
        logger.warning("[EXTRACT] No transcript text for %s, aborting extraction", caller_phone)
        return

    # (b) Use precomputed unified analysis or run a single LLM pass
    llm_provider = kwargs.get("llm_provider", "deepseek")
    precomputed: PostCallAnalysis | None = kwargs.get("precomputed_analysis")
    if precomputed is not None:
        scores = precomputed.scores
        facts = dict(precomputed.facts)
        if precomputed.sentiment:
            facts["sentiment"] = precomputed.sentiment
        if not facts.get("summary_sentence"):
            facts["summary_sentence"] = str(precomputed.summary.get("short_summary") or "")
        logger.info("[EXTRACT] Using precomputed post-call analysis for %s", caller_phone)
    else:
        analysis = await analyze_post_call(transcript_text, llm_provider=llm_provider)
        scores = analysis.scores
        facts = dict(analysis.facts)

    call_date = datetime.now(timezone.utc).isoformat()

    # (c) Update lead profile
    from re_voice_memory.lead import (
        load_lead_profile,
        save_lead_profile,
        get_or_create_lead,
    )

    profile = load_lead_profile(caller_phone)
    if profile is None:
        profile = await get_or_create_lead(
            caller_phone,
            stable_name=facts.get("stable_name", ""),
        )

    old_stage = profile.deal_stage
    profile.call_count += 1
    profile.last_contact_date = call_date

    if scores is not None:
        profile.intent_score = scores.composite

    if facts.get("stable_name"):
        profile.stable_name = facts["stable_name"]
    if facts.get("role"):
        profile.role = facts["role"]
    if facts.get("language_pref"):
        profile.language_pref = facts["language_pref"]
    if facts.get("assigned_agent"):
        profile.assigned_agent = facts["assigned_agent"]
    if facts.get("loan_status"):
        profile.loan_status = facts["loan_status"]

    budget_result = _parse_budget(facts.get("budget_raw"))
    if budget_result.get("min") is not None:
        profile.budget_min = budget_result["min"]
    if budget_result.get("max") is not None:
        profile.budget_max = budget_result["max"]

    timeline_weeks = _parse_timeline_weeks(facts.get("timeline_raw"))
    if timeline_weeks is not None:
        profile.timeline_weeks = timeline_weeks

    profile.preferred_areas = _merge_unique(
        profile.preferred_areas or [],
        facts.get("areas_mentioned", []),
    )
    profile.must_haves = _merge_unique(
        profile.must_haves or [],
        facts.get("must_haves_mentioned", []),
    )
    profile.deal_breakers = _merge_unique(
        profile.deal_breakers or [],
        facts.get("deal_breakers", []),
    )
    profile.decision_factors = _merge_unique(
        profile.decision_factors or [],
        facts.get("decision_factors", []),
    )

    # IQ #4 — social graph: merge relations by (name, relation)
    new_relations = [
        r for r in (facts.get("relation_names") or [])
        if isinstance(r, dict) and r.get("name")
    ]
    if new_relations:
        known = {
            (str(r.get("name", "")).lower(), str(r.get("relation", "")).lower())
            for r in (profile.relation_names or [])
        }
        for rel in new_relations:
            sig = (str(rel.get("name", "")).lower(), str(rel.get("relation", "")).lower())
            if sig not in known:
                profile.relation_names.append({
                    "name": str(rel.get("name", "")),
                    "relation": str(rel.get("relation", "")),
                    "context": str(rel.get("context", "")),
                })

    # IQ #17 — final slot values from this call overwrite stale ones
    slot_values = facts.get("slot_values") or {}
    if isinstance(slot_values, dict):
        for slot, value in slot_values.items():
            if value:
                profile.slot_values[str(slot)] = str(value)

    if scores is not None:
        profile.deal_stage = stage_from_intent(scores, profile.call_count)

    await save_lead_profile(profile)

    # (d) Write call summary markdown
    summary_dir = output_dir / "leads" / caller_phone / "call_summaries"
    summary_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary_path = summary_dir / f"summary_{timestamp}.md"

    intent_section = ""
    if scores is not None:
        intent_section = (
            f"**Intent Scores:**\n"
            f"- Timeline Urgency: {scores.timeline_urgency}/10\n"
            f"- Budget Seriousness: {scores.budget_seriousness}/10\n"
            f"- Area Commitment: {scores.area_commitment}/10\n"
            f"- Composite: {scores.composite}\n"
            f"- Rationale: {scores.rationale}\n"
        )

    summary_lines = [
        f"# Call Summary — {caller_phone}",
        "",
        f"**Date:** {call_date}",
        f"**Lead:** {profile.stable_name or 'Unknown'}",
        f"**Call #:** {profile.call_count}",
        f"**Stage:** {profile.deal_stage} (was {old_stage})",
        "",
        intent_section,
        "",
        "## Extracted Facts",
        "",
        f"- Budget: {facts.get('budget_raw') or 'none'} "
        f"(₹{facts.get('budget_min', '?')}L – ₹{facts.get('budget_max', '?')}L)",
        f"- Areas: {', '.join(facts.get('areas_mentioned', [])) or 'none'}",
        f"- Must-haves: {', '.join(facts.get('must_haves_mentioned', [])) or 'none'}",
        f"- Deal-breakers: {', '.join(facts.get('deal_breakers', [])) or 'none'}",
        f"- Timeline: {facts.get('timeline_raw') or 'not specified'}",
        f"- Loan: {facts.get('loan_status', 'unknown')}",
        f"- Sentiment: {facts.get('sentiment', 'neutral')}",
        f"- Language: {facts.get('language_pref', 'en')}",
        "",
        f"**Summary:** {facts.get('summary_sentence', '')}",
        "",
    ]
    atomic_write_text(summary_path, "\n".join(summary_lines))

    # (e) Log objections — buffered in-call first, then LLM-extracted
    from re_voice_memory.objection_log import append_objection, Objection

    for entry in buffered_context:
        if not isinstance(entry, dict) or entry.get("key") != "objection":
            continue
        raw_val = str(entry.get("value") or "")
        parts = raw_val.split(":", 3)
        if len(parts) < 3:
            continue
        obj_type, phrase, script = parts[0], parts[1], parts[2]
        outcome = parts[3] if len(parts) > 3 else "unresolved"
        try:
            await append_objection(
                caller_phone,
                Objection(
                    type=obj_type,  # type: ignore[arg-type]
                    listing_id=entry.get("listing_id"),
                    raw_phrase=phrase,
                    script_used=script,
                    outcome=outcome,  # type: ignore[arg-type]
                    call_date=call_date,
                    call_count_at_time=profile.call_count,
                ),
            )
        except Exception as buf_exc:
            logger.warning("[EXTRACT] Buffered objection persist failed: %s", buf_exc)

    raw_objections = facts.get("objections", [])
    if isinstance(raw_objections, list):
        for obj in raw_objections:
            if isinstance(obj, dict):
                try:
                    objection = Objection(
                        type=obj.get("type", "other"),
                        listing_id=obj.get("listing_id"),
                        raw_phrase=obj.get("phrase", ""),
                        script_used=obj.get("script_used", "generic"),
                        outcome=obj.get("outcome", "unresolved"),
                        call_date=call_date,
                        call_count_at_time=profile.call_count,
                    )
                    await append_objection(caller_phone, objection)
                except Exception as obj_exc:
                    logger.warning("[EXTRACT] Failed to log objection: %s", obj_exc)

    # (f) Update property graph
    try:
        from re_voice_memory.property_graph import add_signal
        listings_discussed: list[str] = []
        for entry in buffered_context:
            if isinstance(entry, dict) and entry.get("key") == "listing_discussed":
                lid = entry.get("value", "")
                if lid:
                    listings_discussed.append(lid)

        for lid in listings_discussed:
            await add_signal(
                phone=caller_phone,
                listing_id=lid,
                signal="viewed",
                call_date=call_date,
            )
    except Exception as pg_exc:
        logger.warning("[EXTRACT] Property graph update failed: %s", pg_exc)

    # (g) Sync CRM
    try:
        from re_voice_memory.crm_sync import push_crm_update, build_crm_update_from_call

        duration = kwargs.get("duration", 0)
        crm_update = build_crm_update_from_call(
            profile=profile,
            scores=scores,
            buffered_context=buffered_context,
            sentiment=facts.get("sentiment", "neutral"),
            duration=duration,
            call_date=call_date,
        )
        await push_crm_update(crm_update)
    except Exception as crm_exc:
        logger.warning("[EXTRACT] CRM sync failed: %s", crm_exc)

    # (h) Emit metrics
    try:
        from re_voice_memory import metrics as m

        if scores is not None:
            m.record_intent_score(call_count=profile.call_count, score=scores.composite)
        m.record_stage_transition(from_stage=old_stage, to_stage=profile.deal_stage)
        for obj in (facts.get("objections") or []):
            if isinstance(obj, dict):
                m.record_objection(obj.get("type", "other"))
        if facts.get("viewing_scheduled"):
            m.record_viewing()
        if facts.get("offer_intent"):
            m.record_offer_intent()
    except Exception as metric_exc:
        logger.warning("[EXTRACT] Metrics emission failed: %s", metric_exc)

    logger.info(
        "[EXTRACT] Completed for %s: stage %s→%s, score=%s",
        caller_phone,
        old_stage,
        profile.deal_stage,
        getattr(scores, "composite", "N/A") if scores else "N/A",
    )


# =============================================================================
# EXTRACTION DURABILITY — CRASH RECOVERY
# =============================================================================
from datetime_utils import now_utc, parse_iso

PENDING_DIR = MEMORY_DIR / ".extraction_pending"
PENDING_DIR.mkdir(exist_ok=True)


def _parse_raw_md_to_transcript(raw_md_path: Path) -> str:
    """Extract transcript text from a raw.md file."""
    try:
        content = raw_md_path.read_text(encoding="utf-8")
        lines: list[str] = []
        in_transcript = False
        for line in content.splitlines():
            if "Transcript" in line and line.strip().startswith("#"):
                in_transcript = True
                continue
            if in_transcript and line.strip().startswith("#") and "Transcript" not in line:
                break
            if in_transcript and line.strip():
                colon_idx = line.find(":")
                if 0 < colon_idx < 30:
                    lines.append(line[colon_idx + 1:].strip())
                else:
                    lines.append(line.strip())
        return "\n".join(lines)
    except Exception:
        return ""


async def extract_call_memories_safe(
    room_name: str,
    caller_phone: str,
    **kwargs: Any,
) -> None:
    """Wrapper with pending marker for crash recovery."""
    marker_path = PENDING_DIR / f"{room_name}.json"
    marker_data = {
        "room_name": room_name,
        "caller_phone": caller_phone,
        "started_at": now_utc().isoformat(),
        "kwargs_summary": {k: str(v)[:100] for k, v in kwargs.items()},
    }
    marker_path.write_text(json.dumps(marker_data), encoding="utf-8")

    try:
        await extract_call_memories(caller_phone=caller_phone, **kwargs)
    finally:
        marker_path.unlink(missing_ok=True)


async def recover_pending_extractions() -> int:
    """Process pending extraction markers from a crashed worker. Returns recovered count."""
    recovered = 0
    for marker_path in PENDING_DIR.glob("*.json"):
        try:
            data = json.loads(marker_path.read_text())
            age_seconds = (now_utc() - parse_iso(data["started_at"])).total_seconds()
            if age_seconds < 30:
                continue
            logger.info("[RECOVERY] Processing stale extraction: %s", data["room_name"])
            raw_md_path = MEMORY_DIR / "calls" / data["room_name"] / "raw.md"
            if raw_md_path.exists():
                transcript_text = _parse_raw_md_to_transcript(raw_md_path)
                await extract_call_memories(
                    caller_phone=data["caller_phone"],
                    transcript_text=transcript_text,
                    buffered_context=[],
                    raw_md_path=raw_md_path,
                    output_dir=MEMORY_DIR,
                )
            marker_path.unlink(missing_ok=True)
            recovered += 1
        except Exception as e:
            logger.error("[RECOVERY] Failed for %s: %s", marker_path.name, e)
    return recovered
