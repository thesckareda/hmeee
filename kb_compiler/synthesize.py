"""
Stages B + C — EXTRACT (Gemini 3.1 Flash) and SYNTHESIZE (Gemini 3.1 Pro).

B: chunks → grounded facts with source refs — high-volume, cheap model,
   validated downstream.
C: facts → the five knowledge containers + persona, schema-forced JSON —
   correctness-critical, strongest model.

Honesty is enforced twice: prompts forbid invented numbers, and
validate.py's grounding check blocks any number not present in the sources.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from kb_compiler.llm import EXTRACT_MODEL, generate_json

logger = logging.getLogger("kb-compiler")

# Industry noun mapping — same containers, different vocabulary (guide §3.0)
INDUSTRY_NOUNS: dict[str, dict[str, str]] = {
    "real_estate": {"item": "property/project listing", "provider": "builder",
                    "price": "price range in ₹", "timeline": "possession date",
                    "registration": "RERA id"},
    "finance": {"item": "loan scheme / financial product", "provider": "bank/NBFC",
                "price": "interest rate range % p.a.", "timeline": "sanction/processing time",
                "registration": "RBI/NBFC registration"},
    "insurance": {"item": "policy / plan", "provider": "insurer",
                  "price": "premium range", "timeline": "claim settlement time",
                  "registration": "IRDAI registration"},
    "healthcare": {"item": "treatment / package", "provider": "hospital/clinic",
                   "price": "package cost range", "timeline": "appointment lead time",
                   "registration": "NABH/registration id"},
    "ecommerce": {"item": "product", "provider": "brand/seller",
                  "price": "price range", "timeline": "delivery time",
                  "registration": "GSTIN/seller id"},
}

_LANG_STYLE = {
    "tinglish": (
        "Spoken Telugish: Telugu connectors (andi, garu, avunu, sare, kada) in Latin script + "
        "English for all numbers, prices, product terms, place names. Phone-natural, warm, ≤2 sentences. "
        "Never Hindi, never pure literary Telugu."
    ),
    "hte": (
        "Spoken Hinglish: Hindi connectors (haan, bilkul, na, toh) + English for all numbers, prices, "
        "product terms, place names. Phone-natural, warm, ≤2 sentences. Never Telugu."
    ),
    "english": "Professional, warm Indian English. Phone-natural, ≤2 sentences.",
}

_HONESTY = (
    "HARD RULES: Use ONLY facts provided below. NEVER invent numbers, prices, percentages, "
    "dates, registration ids, or names. If a detail is not in the facts, omit it. "
    "Every number you output must appear verbatim in the provided facts/sources."
)


def _facts_block(facts: list[dict], limit: int = 120) -> str:
    lines = []
    for f in facts[:limit]:
        lines.append(f"- {f.get('claim', '')} [{f.get('source_ref', '')}]")
    return "\n".join(lines)


async def extract_facts(chunks: list[dict]) -> list[dict]:
    """Stage B — per-batch fact extraction with source refs."""
    facts: list[dict] = []
    batch: list[dict] = []
    batch_chars = 0
    batches: list[list[dict]] = []
    for chunk in chunks:
        if batch_chars + len(chunk["text"]) > 12000 and batch:
            batches.append(batch)
            batch, batch_chars = [], 0
        batch.append(chunk)
        batch_chars += len(chunk["text"])
    if batch:
        batches.append(batch)

    for i, group in enumerate(batches):
        corpus = "\n\n".join(f"[{c['chunk_id']}]\n{c['text']}" for c in group)
        prompt = (
            "Extract every atomic, verifiable business fact from these knowledge-base excerpts. "
            "Return JSON: {\"facts\": [{\"claim\": \"one factual sentence\", "
            "\"value\": \"the number/name if any, else empty\", "
            "\"source_ref\": \"the [chunk_id] it came from\"}]}\n"
            "Facts = company info, items for sale (with prices, sizes, availability), market data, "
            "policies, USPs, competitor claims, compliance lines. NO opinions, NO duplicates.\n\n"
            f"{corpus}"
        )
        try:
            result = await generate_json(prompt, temperature=0.1, model=EXTRACT_MODEL)
            for f in result.get("facts") or []:
                if isinstance(f, dict) and f.get("claim"):
                    facts.append(f)
        except Exception as exc:
            logger.warning("[EXTRACT] batch %d failed: %s", i, exc)
    logger.info("[EXTRACT] %d facts from %d batches", len(facts), len(batches))
    return facts


async def build_company_knowledge(facts: list[dict], manifest: dict) -> dict:
    persona = manifest.get("persona") or {}
    style = _LANG_STYLE.get(manifest.get("lang_preset", "tinglish"), _LANG_STYLE["english"])
    prompt = (
        f"{_HONESTY}\n\n"
        f"You are building the FAQ pack for a phone voice agent: {persona.get('name', 'Agent')}, "
        f"{persona.get('role', 'consultant')} at {persona.get('company', 'the company')}.\n"
        f"Answer style: {style}\n\n"
        "From the facts below, produce JSON:\n"
        "{\"faq\": [{\"id\": \"snake_case_id\", \"category\": \"company|policy|process|product\", "
        "\"questions\": [8 to 15 paraphrases — mix English, the local code-switch style, and the "
        "short lazy phrasings real callers use], \"answer\": \"spoken answer, ≤2 sentences, ≤300 chars\"}], "
        "\"hot_facts\": [up to 8 one-line strings — the most important always-true facts]}\n"
        "Cover: who the company is, registrations, fees/charges, process, service area, timings — "
        "whatever the facts support. 8–20 FAQ entries.\n\n"
        f"FACTS:\n{_facts_block(facts)}"
    )
    return await generate_json(prompt, temperature=0.3, max_output_tokens=8192)


async def build_inventory(facts: list[dict], manifest: dict) -> dict:
    industry = manifest.get("industry", "real_estate")
    nouns = INDUSTRY_NOUNS.get(industry, INDUSTRY_NOUNS["real_estate"])
    prompt = (
        f"{_HONESTY}\n\n"
        f"Industry: {industry}. Build the ITEMS container ({nouns['item']}s).\n"
        "From the facts below, produce JSON:\n"
        "{\"properties\": [{\"listing_id\": \"snake_case unique\", \"name\": \"...\", "
        f"\"builder\": \"{nouns['provider']}\", \"location\": \"...\", \"micro_market\": \"...\", "
        "\"configurations\": [\"...\"], "
        f"\"price_range\": \"{nouns['price']} — EXACTLY as stated in facts\", "
        f"\"possession\": \"{nouns['timeline']}\", \"rera_id\": \"{nouns['registration']} or empty\", "
        "\"amenities\": [\"...\"], "
        "\"usp\": \"ONE concrete differentiator a senior agent would say on the phone — a verifiable "
        "detail with a number or proper noun from the facts, NEVER marketing adjectives\", "
        "\"units_left\": null_or_integer_ONLY_if_facts_state_real_availability, "
        "\"offer_expiry\": null_or_string_ONLY_if_facts_state_a_real_deadline}]}\n"
        "One entry per distinct item in the facts. Omit fields the facts don't support "
        "(empty string / null) — NEVER fabricate.\n\n"
        f"FACTS:\n{_facts_block(facts)}"
    )
    return await generate_json(prompt, temperature=0.2, max_output_tokens=8192)


async def build_market_competitor(facts: list[dict], manifest: dict) -> dict:
    preset = manifest.get("lang_preset", "tinglish")
    style = _LANG_STYLE.get(preset, _LANG_STYLE["english"])
    prompt = (
        f"{_HONESTY}\n\n"
        f"Build the MARKET + OBJECTION container. Spoken style: {style}\n\n"
        "From the facts below, produce JSON with keys:\n"
        "\"micro_markets\": [{\"name\", \"price_per_sqft\" (or rate/premium equivalent), "
        "\"usp\", \"appreciation_5yr\" (only if in facts), \"key_projects\": []}],\n"
        "\"competitors\": [{\"name\", \"our_positioning\": \"honest 1-2 sentence comparison from facts\"}],\n"
        "\"objection_scripts\": {\"price_too_high\", \"need_to_think\", \"other_builder_cheaper\", "
        "\"location_far\", \"will_wait_for_rates_to_fall\", \"not_ready_to_move\"} — each a ≤2 sentence "
        "spoken rebuttal grounded in the facts,\n"
        "\"objection_stories\": same 6 keys — each a 30-second social-proof story using ONLY real "
        "numbers/names from the facts. A story without a grounded detail should be a shorter "
        "reassurance instead — never an invented case study.\n\n"
        f"FACTS:\n{_facts_block(facts)}"
    )
    return await generate_json(prompt, temperature=0.3, max_output_tokens=8192)


async def build_persona(facts: list[dict], manifest: dict) -> dict:
    persona = manifest.get("persona") or {}
    preset = manifest.get("lang_preset", "tinglish")
    style = _LANG_STYLE.get(preset, _LANG_STYLE["english"])
    industry = manifest.get("industry", "real_estate")
    prompt = (
        f"{_HONESTY}\n\n"
        f"Write the voice-agent persona. Agent: {persona.get('name', 'Agent')}, "
        f"{persona.get('role', 'consultant')} at {persona.get('company', 'the company')}. "
        f"Industry: {industry}. Language style: {style}\n\n"
        "Produce JSON:\n"
        "{\"agent_instructions\": \"system prompt, 120-200 words: who the agent is, exact speaking "
        "style with 2-3 example phrases in the code-switch style, what it sells (from facts), "
        "the rule to keep replies 1-2 sentences and always end with a question, and any compliance "
        "lines the facts require\",\n"
        "\"first_line\": \"the greeting the agent opens every call with, in the code-switch style, "
        "≤20 words, naming the agent and company\",\n"
        "\"stt_hints\": [up to 25 domain words/names from the facts that speech recognition "
        "must not mis-hear — item names, places, the company name]}\n\n"
        f"FACTS:\n{_facts_block(facts, limit=60)}"
    )
    return await generate_json(prompt, temperature=0.4)


# Minimum viable shape per artifact — a response missing its top-level key
# is a malformed generation, not a content decision; retry once before the
# validators see it.
_EXPECTED_KEY = {
    "company_knowledge": "faq",
    "inventory": "properties",
    "market_competitor": "objection_scripts",
    "persona": "agent_instructions",
}


async def synthesize_all(facts: list[dict], manifest: dict, progress=None) -> dict[str, Any]:
    """Stage C — the four artifact builders, sequential (rate-limit friendly).
    Each builder gets one shape-guarded retry."""
    artifacts: dict[str, Any] = {}
    steps = [
        ("company_knowledge", build_company_knowledge),
        ("inventory", build_inventory),
        ("market_competitor", build_market_competitor),
        ("persona", build_persona),
    ]
    for name, builder in steps:
        if progress:
            progress(f"synthesizing {name}")
        result = await builder(facts, manifest)
        key = _EXPECTED_KEY[name]
        if not isinstance(result, dict) or not result.get(key):
            logger.warning("[SYNTH] %s came back without '%s' — retrying once", name, key)
            if progress:
                progress(f"synthesizing {name} (retry)")
            result = await builder(facts, manifest)
        artifacts[name] = result if isinstance(result, dict) else {}
        logger.info("[SYNTH] %s done", name)
    return artifacts
