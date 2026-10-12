"""
Pre-call context injection — assemble enriched system prompt.

This is the most latency-critical module in the RE Voice OS package.
All I/O is parallelised via asyncio.gather. Nothing runs sequentially.
Target: < 60ms for returning leads, 0ms for new leads.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any

from re_voice_memory.constants import (
    MEMORY_DIR,
    TOKEN_BUDGET_RE_SLOT,
    TOKEN_BUDGET_COMPANY_SLOT,
    TOKEN_BUDGET_PROPERTY_SLOT,
    HOT_INTENT_SCORE_THRESHOLD,
    MAX_INJECTED_MEMORIES,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# ROLE-AWARE PROMPT BUILDER (Step 8)
# ═════════════════════════════════════════════════════════════════════════════

_ROLE_TONES: dict[str, str] = {
    "buyer": (
        "\n\n[TONE: BUYER]\n"
        "Tone: warm, aspirational, lifestyle-focused. Lead with how the home will feel. "
        "Acknowledge the emotional weight of this decision. Surface school proximity, "
        "neighbourhood character, and future appreciation. Ask about family situation "
        "naturally. Avoid pure specification-reading."
    ),
    "investor": (
        "\n\n[TONE: INVESTOR]\n"
        "Tone: data-dense, ROI-focused, efficient. Lead with yield potential, comparable "
        "transactions, price-per-sqft vs area average, and appreciation history. Respect "
        "their time — they want numbers first, story second. Use terms: gross yield, "
        "capital appreciation, rental income, IRR."
    ),
    "seller": (
        "\n\n[TONE: SELLER]\n"
        "Tone: empathetic, pricing-reality-first. Acknowledge emotional attachment to "
        "the property. Be honest about market positioning. Surface comparable sold prices. "
        "Ask about their timeline and why they are selling — this governs the pricing "
        "conversation. Do not over-promise valuation."
    ),
    "renter": (
        "\n\n[TONE: RENTER]\n"
        "Tone: practical, fast. They have the shortest decision window. Lead with "
        "availability, move-in date, deposit amount, maintenance included. Respect that "
        "they likely have 3–5 options shortlisted already. Be the fastest, most helpful "
        "agent — that is the entire competitive advantage here."
    ),
}

_HME_LANG_OVERLAY = (
    "\n\n[LANGUAGE: HINGLISH / MARATHI+ENGLISH]\n"
    "Reply in natural spoken Hinglish, Marathi+English, or Indian English — match the caller. "
    "Hindi/Marathi connectors in Devanagari; English for numbers, BHK, Crore, sqft, EMI, site visit, "
    "place names, and property jargon. Never pure formal Hindi/Marathi. Never Telugu."
)

_LANG_OVERLAYS: dict[str, str] = {
    "telugu": (
        "\n\n[LANGUAGE: TELUGISH]\n"
        "Reply in natural spoken Telugish — Telugu connectors (andi, garu, avunu, sare) with English for "
        "numbers, BHK, Crore, sqft, EMI, site visit, place names, and property jargon. "
        "Never Hindi, pure literary Telugu, or other languages."
    ),
    "telugish": (
        "\n\n[LANGUAGE: TELUGISH]\n"
        "Reply in natural spoken Telugish — Telugu connectors (andi, garu, avunu, sare) with English for "
        "numbers, BHK, Crore, sqft, EMI, site visit, place names, and property jargon. "
        "Never Hindi, pure literary Telugu, or other languages."
    ),
    "hinglish": _HME_LANG_OVERLAY,
    "hindi": _HME_LANG_OVERLAY,
    "hi": _HME_LANG_OVERLAY,
    "marathi": _HME_LANG_OVERLAY,
    "mr": _HME_LANG_OVERLAY,
    "en": (
        "\n\n[LANGUAGE: ENGLISH]\n"
        "Reply in professional Indian English. Never Hindi or Telugu unless caller switches."
    ),
    "english": (
        "\n\n[LANGUAGE: ENGLISH]\n"
        "Reply in professional Indian English. Never Hindi or Telugu unless caller switches."
    ),
}


def build_role_aware_instructions(
    base_instructions: str,
    role: str,
    language_pref: str = "hinglish",
) -> str:
    """Append role-specific tone and language overlays to base instructions."""
    tone = _ROLE_TONES.get(role, _ROLE_TONES["buyer"])
    lang = _LANG_OVERLAYS.get(language_pref, _HME_LANG_OVERLAY)
    return base_instructions + tone + lang


# ═════════════════════════════════════════════════════════════════════════════
# TOKEN BUDGETING (zero-API local estimator via token_counter)
# ═════════════════════════════════════════════════════════════════════════════

def _assemble_budgeted(
    blocks: list[tuple[int, str]],
    max_tokens: int,
    lang_preset: str = "hme",
) -> str:
    """Join priority-sorted blocks without exceeding token budget (L2 sync, cached)."""
    from token_counter import count_tokens

    blocks.sort(key=lambda b: b[0])
    assembled = ""
    for _prio, text in blocks:
        candidate = assembled + ("\n" if assembled else "") + text
        if count_tokens(candidate, lang_preset) > max_tokens:
            continue
        assembled = candidate
    return assembled


def _format_source_context_block(source_context) -> str:
    """IQ #18 — [CALL SOURCE] block from campaign metadata (dict or plain string)."""
    if not source_context:
        return ""
    if isinstance(source_context, str):
        detail = source_context.strip()
        channel = "your records"
    elif isinstance(source_context, dict):
        detail = str(
            source_context.get("search_query")
            or source_context.get("listing")
            or source_context.get("detail")
            or ""
        ).strip()
        channel = str(source_context.get("channel") or "website").strip()
    else:
        return ""
    if not detail:
        return ""
    return (
        "[CALL SOURCE — IQ #18]\n"
        f"This caller came via {channel}: \"{detail}\".\n"
        "In your FIRST substantive reply, open with the EXACT source "
        f"(e.g. 'aapne {channel} pe {detail} search kiya tha — usi ke baare mein baat karein?'). "
        "NEVER say 'regarding your inquiry' or any generic reference."
    )


def _hot_lead_instruction(intent_score: float) -> str:
    return (
        f"[HOT LEAD — PROACTIVE CLOSE (intent {intent_score:.1f}/10)]\n"
        "This caller is highly engaged from prior calls. Lead with urgency, not discovery.\n"
        "• Open with a specific project or visit slot — skip generic qualifying questions.\n"
        "• Reference their prior interest (budget/area from LEAD CONTEXT).\n"
        "• Propose a concrete site visit time in your first substantive reply."
    )


# ═════════════════════════════════════════════════════════════════════════════
# PRE-CALL INJECTION (Step 7)
# ═════════════════════════════════════════════════════════════════════════════

async def inject_lead_context(
    phone: str,
    base_instructions: str,
    campaign_id: str | None = None,
    max_re_tokens: int = TOKEN_BUDGET_RE_SLOT,
    lang_preset: str | None = None,
    intent_score: float | None = None,
    caller_name: str | None = None,
    remember_enabled: bool = True,
) -> str:
    """
    Assemble enriched system prompt from all three memory tiers.

    All I/O is parallelised via asyncio.gather. Target: < 60ms.
    Returns base_instructions unchanged for new/unknown leads.
    remember_enabled=False (the "Remember the caller" button is OFF) skips
    ALL memory reads — the caller is treated as brand new.
    """
    t0 = _time.perf_counter()

    # ── Remember-caller button OFF → no memory reads at all ──
    if not remember_enabled:
        logger.debug("[INJECT] remember_caller disabled — skipping memory injection")
        return base_instructions

    # ── New-lead fast path ──
    if phone == "unknown" or not phone:
        logger.debug("[INJECT] New/unknown lead — no context injection")
        return base_instructions

    # ── Parallel I/O: all four tasks fire simultaneously ──
    async def _task_profile():
        from re_voice_memory.lead import load_lead_profile
        return await asyncio.to_thread(load_lead_profile, phone)

    async def _task_graph():
        from re_voice_memory.property_graph import load_property_graph
        return await asyncio.to_thread(load_property_graph, phone)

    async def _task_kv():
        from re_voice_memory.kv_store import get_lead_hot_context
        return await get_lead_hot_context(phone)

    async def _task_campaign():
        if not campaign_id:
            return None
        from re_voice_memory.kv_store import kv_get
        return await kv_get(f"campaign:{campaign_id}:listings")

    async def _task_source_context():
        # IQ #18 — external context (website search / ad / listing the call came from)
        if not campaign_id:
            return None
        from re_voice_memory.kv_store import kv_get
        return await kv_get(f"campaign:{campaign_id}:source_context")

    async def _task_caller_history():
        from re_voice_memory.caller_history import load_caller_history

        return await asyncio.to_thread(load_caller_history, phone)

    tasks = [
        _task_profile(),
        _task_graph(),
        _task_kv(),
        _task_campaign(),
        _task_caller_history(),
        _task_source_context(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    profile, graph, kv_context, campaign_listings, caller_history, source_context = results

    # Handle individual failures gracefully
    if isinstance(profile, Exception):
        logger.warning("[INJECT] Profile load failed: %s", profile)
        profile = None
    if isinstance(graph, Exception):
        logger.warning("[INJECT] Graph load failed: %s", graph)
        graph = None
    if isinstance(kv_context, Exception):
        logger.warning("[INJECT] KV fetch failed: %s", kv_context)
        kv_context = None
    if isinstance(campaign_listings, Exception):
        logger.warning("[INJECT] Campaign fetch failed: %s", campaign_listings)
        campaign_listings = None
    if isinstance(caller_history, Exception):
        logger.warning("[INJECT] Caller history load failed: %s", caller_history)
        caller_history = None
    if isinstance(source_context, Exception):
        logger.warning("[INJECT] Source context fetch failed: %s", source_context)
        source_context = None

    has_caller_history = bool(caller_history)

    # IQ #18 — open with the EXACT source; falls back silently when absent
    source_block = _format_source_context_block(source_context)

    # New-lead: no profile AND no KV context AND no prior call history
    if (
        profile is None
        and (kv_context is None or not kv_context.get("profile"))
        and not has_caller_history
    ):
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        logger.debug("[INJECT] New lead (no profile) — %.1fms", elapsed_ms)
        if source_block:
            return base_instructions + "\n\n" + source_block
        return base_instructions

    # ── Assemble context blocks (separate company / property / lead budgets) ──
    lead_blocks: list[tuple[int, str]] = []
    company_blocks: list[tuple[int, str]] = []
    property_blocks: list[tuple[int, str]] = []

    resolved_intent = intent_score
    if resolved_intent is None and profile is not None:
        resolved_intent = getattr(profile, "intent_score", None)
    if resolved_intent is None and kv_context:
        raw = kv_context.get("intent_score")
        if raw is not None:
            try:
                resolved_intent = float(raw)
            except (TypeError, ValueError):
                resolved_intent = None

    is_returning = bool(profile and getattr(profile, "call_count", 0) > 0)

    if is_returning and lang_preset:
        try:
            from knowledge_pack import get_store

            hot = get_store().hot_facts_block(lang_preset, max_facts=5)
            if hot:
                company_blocks.append((1, hot))
        except Exception as e:
            logger.warning("[INJECT] Company hot facts failed: %s", e)

    if resolved_intent is not None and float(resolved_intent) >= HOT_INTENT_SCORE_THRESHOLD:
        lead_blocks.append((0, _hot_lead_instruction(float(resolved_intent))))

    if has_caller_history:
        try:
            from re_voice_memory.caller_history import format_history_for_injection

            resolved_name = (caller_name or "").strip()
            if not resolved_name and profile is not None:
                resolved_name = getattr(profile, "stable_name", "") or ""
            history_text = format_history_for_injection(phone, resolved_name, caller_history)
            if history_text:
                lead_blocks.append((0, history_text))
        except Exception as e:
            logger.warning("[INJECT] Caller history format failed: %s", e)

    try:
        from dashboard_analytics import build_live_sales_context

        live_sales = build_live_sales_context(phone, profile)
        if live_sales:
            lead_blocks.append((0, live_sales))
    except Exception as e:
        logger.warning("[INJECT] Live sales context failed: %s", e)

    # Block 1: Lead identity (always if profile exists)
    if profile:
        name = getattr(profile, "stable_name", "") or phone
        role = getattr(profile, "role", "buyer")
        budget_min = getattr(profile, "budget_min", None)
        budget_max = getattr(profile, "budget_max", None)
        stage = getattr(profile, "deal_stage", "unknown")
        call_count = getattr(profile, "call_count", 0)

        budget_str = ""
        if budget_min or budget_max:
            def _fmt_budget(amt: int | None) -> str:
                if amt is None:
                    return "?"
                if amt >= 10_000_000:
                    return f"Rs{amt/10_000_000:.1f}Cr"
                elif amt >= 100_000:
                    return f"Rs{amt/100_000:.0f}L"
                else:
                    return f"Rs{amt:,}"
            min_cr = _fmt_budget(budget_min)
            max_cr = _fmt_budget(budget_max)
            budget_str = f" | Budget: {min_cr}–{max_cr}"

        lead_blocks.append((
            1,
            f"Lead: {name} | Role: {role}{budget_str} | Stage: {stage} | Call #{call_count}"
        ))

    # Block 2: Area and timeline
    if profile:
        preferred = getattr(profile, "preferred_areas", []) or []
        excluded = getattr(profile, "excluded_areas", []) or []
        timeline = getattr(profile, "timeline_weeks", None)
        loan = getattr(profile, "loan_status", "unknown")

        area_parts = []
        if preferred:
            area_parts.append(f"Prefers: {', '.join(preferred)}")
        if excluded:
            area_parts.append(f"Excludes: {', '.join(excluded)}")
        time_parts = []
        if timeline:
            time_parts.append(f"Timeline: {timeline}w")
        if loan and loan != "unknown":
            time_parts.append(f"Loan: {loan}")
        if area_parts or time_parts:
            lead_blocks.append((2, ". ".join(area_parts + time_parts) + "."))

    # IQ #4 — social graph: household / decision-makers from prior calls
    if profile:
        relations = getattr(profile, "relation_names", []) or []
        if relations:
            rel_parts = []
            for rel in relations[:3]:
                part = f"{rel.get('name', '')} ({rel.get('relation', '')}"
                context = str(rel.get("context") or "").strip()
                if context:
                    part += f" — {context[:60]}"
                rel_parts.append(part + ")")
            lead_blocks.append((
                2,
                "Household: " + "; ".join(rel_parts)
                + ". Reference family members naturally where relevant — never robotically.",
            ))

    # IQ #17 — final slot values from prior calls (temporal anchors)
    if profile:
        slot_values = getattr(profile, "slot_values", {}) or {}
        value_parts = [f"{slot}: {val}" for slot, val in slot_values.items() if val]
        if value_parts:
            lead_blocks.append((
                2,
                "Previously settled: " + ", ".join(value_parts[:5])
                + ". Confirm, don't re-ask from scratch.",
            ))

    # Property graph (property budget slot)
    if graph is not None and hasattr(graph, "signals") and graph.signals:
        from re_voice_memory.property_graph import format_graph_for_prompt
        graph_summary = format_graph_for_prompt(phone, max_tokens=80)
        if graph_summary and graph_summary != "No property history." and graph_summary.strip():
            property_blocks.append((1, graph_summary))

    # Objection history (lead slot)
    try:
        from re_voice_memory.objection_log import (
            format_objection_history_for_prompt,
            get_failed_scripts,
        )
        obj_text = format_objection_history_for_prompt(phone, max_tokens=60)
        if obj_text and obj_text.strip():
            lead_blocks.append((3, obj_text))
        failed_scripts = get_failed_scripts(phone)
        if failed_scripts:
            lead_blocks.append((
                1,
                f"SCRIPT HISTORY: Do not use {', '.join(failed_scripts)} — these failed on prior calls.",
            ))
    except Exception as e:
        logger.warning("[INJECT] Objection load failed: %s", e)

    # Campaign listings (property slot)
    if campaign_listings and isinstance(campaign_listings, list) and campaign_listings:
        property_blocks.append((
            2,
            f"Active listings for this call: {', '.join(campaign_listings)}. Match lead criteria.",
        ))

    resolved_lang = lang_preset or "hme"
    company_text = _assemble_budgeted(company_blocks, TOKEN_BUDGET_COMPANY_SLOT, resolved_lang)
    property_text = _assemble_budgeted(property_blocks, TOKEN_BUDGET_PROPERTY_SLOT, resolved_lang)
    lead_text = _assemble_budgeted(lead_blocks, max_re_tokens, resolved_lang)

    sections: list[str] = []
    if source_block:
        sections.append(source_block)
    if company_text:
        sections.append("[COMPANY CONTEXT — verified facts]\n" + company_text)
    if property_text:
        sections.append("[PROPERTY CONTEXT — listings & history]\n" + property_text)
    if lead_text:
        sections.append("[LEAD CONTEXT — from prior interactions]\n" + lead_text)

    if not sections:
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        logger.debug("[INJECT] No context assembled — %.1fms", elapsed_ms)
        return base_instructions

    re_context_block = (
        "\n\n"
        + "\n\n".join(sections)
        + "\n\nUse this context to personalise the call. Do not tell the lead you are reading records."
    )

    elapsed_ms = (_time.perf_counter() - t0) * 1000
    from token_counter import count_tokens

    token_est = count_tokens(re_context_block, resolved_lang)
    logger.debug("[INJECT] Injected %d tokens in %.1fms for %s", token_est, elapsed_ms, phone)

    # ── Emit metrics ──
    try:
        from re_voice_memory.metrics import record_injection_latency
        lead_type = "new" if (profile is None and (kv_context is None or not kv_context.get("profile"))) else "returning"
        record_injection_latency(lead_type, len(sections), elapsed_ms)
    except Exception:
        pass

    return base_instructions + re_context_block
