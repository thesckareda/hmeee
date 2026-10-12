"""
Fast preset-aware knowledge retrieval — JSON hot path, no vector DB on live calls.

Latency budget: <3ms per turn (in-memory indexes, no I/O after first load).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

CONFIDENCE_ALLOW = 0.70
CONFIDENCE_LOOKUP_MIN = 0.45

_DATA_ROOT = Path(__file__).resolve().parent / "data"
_BACKSTORY_PATH = _DATA_ROOT / "employee_backstory.json"

# Codebase preset id -> data pack folder prefix
PRESET_ALIASES: dict[str, str] = {
    "tinglish": "tinglish",
    "telugu": "tinglish",
    "hme": "hte",
    "hi_en_mr": "hte",
    "hinglish": "hte",
    "english": "tinglish",
}

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "what", "who", "how", "when",
    "where", "why", "do", "does", "did", "you", "your", "me", "my", "i", "we",
    "our", "ka", "ki", "ke", "hai", "hain", "ho", "kya", "kaun", "kahan", "kab",
    "aur", "ya", "se", "mein", "main", "aap", "tum", "andi", "garu", "the",
})

_PROPERTY_TRIGGERS = re.compile(
    r"\b(godrej|prestige|sobha|my\s*home|lodha|dlf|puravankara|mahindra|"
    r"3\s*bhk|2\s*bhk|4\s*bhk|price|pricing|kitna|cost|rera|possession|"
    r"site\s*visit|brochure|emi|loan|builder|project|flat|apartment|villa)\b",
    re.I,
)

_MARKET_TRIGGERS = re.compile(
    r"\b(market|appreciation|rental|yield|interest\s*rate|loan\s*rate|"
    r"compare|competitor|vs|versus|infrastructure|metro|orr|invest)\b",
    re.I,
)


@dataclass
class FaqHit:
    faq_id: str
    answer: str
    category: str
    confidence: float
    matched_question: str = ""


@dataclass
class TurnKnowledge:
    faq: FaqHit | None = None
    inventory_lines: list[str] = field(default_factory=list)
    market_line: str = ""
    competitor_line: str = ""
    objection_hint: str = ""
    confidence: float = 0.0
    intent: str = "unknown"

    def has_content(self) -> bool:
        return bool(
            self.faq
            or self.inventory_lines
            or self.market_line
            or self.competitor_line
            or self.objection_hint
        )


class KnowledgePackStore:
    """Singleton — loads JSON once, serves indexed lookups."""

    def __init__(self) -> None:
        self._loaded = False
        self._company: dict[str, dict] = {}
        self._market: dict[str, dict] = {}
        self._inventory: dict[str, dict] = {}
        self._faq_index: dict[str, list[tuple[str, str, str, str, frozenset[str]]]] = {}
        # pack -> [(faq_id, category, answer, normalized_question, question_tokens)]
        self._listing_index: dict[str, dict[str, dict]] = {}
        # pack -> listing_id -> property dict
        self._competitor_index: dict[str, dict[str, str]] = {}
        # pack -> builder slug -> sample_script
        self._listing_aliases: dict[str, dict[str, str]] = {}
        # pack -> alias slug -> listing_id (precomputed for <3ms fuzzy match)

    def _pack_id(self, preset: str | None) -> str:
        key = (preset or "tinglish").strip().lower()
        return PRESET_ALIASES.get(key, key if key in ("tinglish", "hte") else "tinglish")

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        t0 = time.perf_counter()
        for pack in ("tinglish", "hte"):
            self._load_company(pack)
            self._load_market(pack)
            self._load_inventory(pack)
        self._loaded = True
        logger.info(
            "[KNOWLEDGE-PACK] Loaded tinglish+hte packs in %.1fms",
            (time.perf_counter() - t0) * 1000,
        )

    def _load_company(self, pack: str) -> None:
        path = _DATA_ROOT / "company_knowledge" / f"{pack}.json"
        if not path.is_file():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._company[pack] = data
        # Token sets precomputed at load — the per-turn scan is pure set
        # intersection, never re-tokenization (REDIS plan §3.5).
        index: list[tuple[str, str, str, str, frozenset[str]]] = []
        for entry in data.get("faq") or []:
            faq_id = str(entry.get("id") or "")
            answer = str(entry.get("answer") or "").strip()
            category = str(entry.get("category") or "company")
            if not faq_id or not answer:
                continue
            for q in entry.get("questions") or []:
                nq = _normalize(str(q))
                if nq:
                    index.append((faq_id, category, answer, nq, frozenset(_tokens(nq))))
        self._faq_index[pack] = index

    def _load_market(self, pack: str) -> None:
        path = _DATA_ROOT / f"{pack}_market_competitor.json"
        if not path.is_file():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._market[pack] = data
        comp_map: dict[str, str] = {}
        for comp in data.get("competitors") or []:
            name = str(comp.get("name") or "")
            script = str(
                comp.get("sample_script")
                or comp.get("sample_script_hinglish")
                or comp.get("sample_script_punjabi")
                or comp.get("our_positioning")
                or ""
            )
            if name and script:
                comp_map[_slug(name)] = script
                for proj in comp.get("hyderabad_projects") or comp.get("projects") or []:
                    comp_map[_slug(str(proj))] = script
        self._competitor_index[pack] = comp_map

    def _load_inventory(self, pack: str) -> None:
        path = _DATA_ROOT / f"{pack}_inventory.json"
        if not path.is_file():
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._inventory[pack] = data
        listings: dict[str, dict] = {}
        for prop in data.get("properties") or []:
            lid = str(prop.get("listing_id") or "")
            if lid:
                listings[lid] = prop
            name = str(prop.get("name") or "")
            if name:
                listings[_slug(name)] = prop
            builder = str(prop.get("builder") or "")
            if builder:
                listings.setdefault(_slug(builder), prop)
        self._listing_index[pack] = listings
        self._listing_aliases[pack] = _build_listing_aliases(listings)

    def hot_facts_block(self, preset: str | None, *, max_facts: int = 8) -> str:
        self.ensure_loaded()
        pack = self._pack_id(preset)
        data = self._company.get(pack) or {}
        facts = list(data.get("hot_facts") or [])[:max_facts]
        if not facts:
            return ""
        lines = "\n".join(f"• {f}" for f in facts)
        return (
            f"[COMPANY HOT FACTS — {pack.upper()} — NEVER INVENT BEYOND THIS]\n"
            f"{lines}\n"
            "If a fact is not listed, say you'll confirm with your manager."
        )

    def lookup_faq(self, transcript: str, preset: str | None) -> FaqHit | None:
        self.ensure_loaded()
        pack = self._pack_id(preset)
        index = self._faq_index.get(pack) or []
        if not index:
            return None

        norm_t = _normalize(transcript)
        if len(norm_t) < 4:
            return None

        best: FaqHit | None = None
        t_tokens = _tokens(norm_t)

        for faq_id, category, answer, nq, q_tokens in index:
            score = _match_score(norm_t, t_tokens, nq, q_tokens)
            if score < 0.45:
                continue
            if best is None or score > best.confidence:
                best = FaqHit(
                    faq_id=faq_id,
                    answer=answer,
                    category=category,
                    confidence=score,
                    matched_question=nq,
                )

        return best

    def lookup_faq_by_id(self, faq_id: str, preset: str | None) -> FaqHit | None:
        """Direct FAQ lookup when intent routing identifies the topic."""
        self.ensure_loaded()
        pack = self._pack_id(preset)
        data = self._company.get(pack) or {}
        for entry in data.get("faq") or []:
            if str(entry.get("id") or "") != faq_id:
                continue
            answer = str(entry.get("answer") or "").strip()
            if not answer:
                return None
            return FaqHit(
                faq_id=faq_id,
                answer=answer,
                category=str(entry.get("category") or "company"),
                confidence=0.88,
                matched_question=faq_id,
            )
        return None

    def _lookup_faq_by_intent(
        self,
        transcript: str,
        intent_result: Any,
        preset: str | None,
    ) -> FaqHit | None:
        """Fallback when STT is Telugu script and token overlap with English FAQs fails."""
        from employee_intent_router import EmployeeIntent

        if intent_result.confidence < 0.80:
            return None

        intent = intent_result.intent
        faq_id: str | None = None

        if intent == EmployeeIntent.ROLE:
            faq_id = "what_do_you_do"
        elif intent == EmployeeIntent.COMPANY:
            lowered = (transcript or "").lower()
            if re.search(
                r"founder|owner|started|start|evaru|ఎవరు|ఓనర్|స్టార్ట్|"
                r"kaun|kisne|peru\s*enti|పేరేంటి|స్థాపక",
                lowered,
                re.I | re.UNICODE,
            ):
                faq_id = "who_founded"
            elif re.search(
                r"team|employees|kitne\s*log|staff|ఎంతమంది|టీమ్",
                lowered,
                re.I | re.UNICODE,
            ):
                faq_id = "team_size"
            elif re.search(
                r"office|location|address|kahan|ఆఫీస్|చిరునామా",
                lowered,
                re.I | re.UNICODE,
            ):
                faq_id = "office_location"
            elif re.search(
                r"rera|registration|రీరా",
                lowered,
                re.I | re.UNICODE,
            ):
                faq_id = "rera_registration"
            elif re.search(
                r"mission|vision|gurinchi|గురించి|about",
                lowered,
                re.I | re.UNICODE,
            ):
                faq_id = "company_mission"

        if not faq_id:
            return None
        return self.lookup_faq_by_id(faq_id, preset)

    def lookup_inventory(
        self,
        transcript: str,
        preset: str | None,
        *,
        max_items: int = 2,
        force: bool = False,
    ) -> list[str]:
        self.ensure_loaded()
        if not force and not _PROPERTY_TRIGGERS.search(transcript):
            return []

        pack = self._pack_id(preset)
        listings = self._listing_index.get(pack) or {}
        if not listings:
            return []

        lowered = transcript.lower()
        norm_t = _normalize(transcript)
        hits: list[tuple[float, dict]] = []
        seen_lids: set[str] = set()
        aliases = self._listing_aliases.get(pack) or {}

        for alias, lid in aliases.items():
            if len(alias) < 4:
                continue
            if alias in norm_t or alias.replace("_", " ") in lowered:
                prop = listings.get(lid)
                if prop and lid not in seen_lids:
                    seen_lids.add(lid)
                    hits.append((0.95, prop))

        for key, prop in listings.items():
            if not isinstance(prop, dict) or "name" not in prop:
                continue
            lid = str(prop.get("listing_id") or key)
            if lid in seen_lids:
                continue
            score = 0.0
            name = str(prop.get("name") or "").lower()
            builder = str(prop.get("builder") or "").lower()
            location = str(prop.get("location") or "").lower()

            if name and name in lowered:
                score += 1.0
            if builder and builder in lowered:
                score += 0.8
            if location and any(part in lowered for part in location.split(",") if len(part) > 3):
                score += 0.5
            if lid.replace("_", " ") in lowered or lid in lowered:
                score += 0.9

            if score >= 0.5:
                seen_lids.add(lid)
                hits.append((score, prop))

        hits.sort(key=lambda x: x[0], reverse=True)
        lines: list[str] = []
        seen: set[str] = set()
        for _, prop in hits[:max_items]:
            lid = str(prop.get("listing_id") or "")
            if lid in seen:
                continue
            seen.add(lid)
            lines.append(_format_property_line(prop))
        return lines

    def lookup_market(self, transcript: str, preset: str | None) -> str:
        self.ensure_loaded()
        if not _MARKET_TRIGGERS.search(transcript):
            return ""

        pack = self._pack_id(preset)
        market = self._market.get(pack) or {}
        lowered = transcript.lower()

        for micro in market.get("micro_markets") or []:
            name = str(micro.get("name") or "")
            if name and any(part in lowered for part in name.lower().split("/") if len(part) > 3):
                return (
                    f"{name}: {micro.get('price_per_sqft', '')}. "
                    f"USP: {micro.get('usp', '')}"
                ).strip()

        rates = market.get("interest_rates") or {}
        if isinstance(rates, dict) and re.search(r"\b(interest|loan|emi|rate)\b", lowered):
            return str(
                rates.get("quote_to_callers")
                or rates.get("summary")
                or rates.get("current_range")
                or ""
            )

        if "market" in lowered or "appreciation" in lowered:
            micros = market.get("micro_markets") or []
            if micros:
                m = micros[0]
                return f"{m.get('name')}: appreciation {m.get('appreciation_5yr', 'strong')}."

        return ""

    def lookup_competitor(self, transcript: str, preset: str | None) -> str:
        self.ensure_loaded()
        pack = self._pack_id(preset)
        comp_map = self._competitor_index.get(pack) or {}
        lowered = transcript.lower()
        for slug, script in comp_map.items():
            if slug in lowered or slug.replace("_", " ") in lowered:
                return script[:400]
        return ""

    _OBJECTION_TYPE_MAPPING = (
        (r"\b(expensive|costly|zyada|mehnga|budget|afford|bahut)\b", "price_too_high"),
        (r"\b(not ready|wait|baad|later|sochna|think)\b", "need_to_think"),
        (r"\b(cheaper|other builder|dusra|compare)\b", "other_builder_cheaper"),
        (r"\b(far|dur|commute|distance)\b", "location_far"),
        (r"\b(rate.*fall|wait.*market|prices.*drop)\b", "will_wait_for_rates_to_fall"),
        (r"\b(ready.*move|immediate|possession.*now)\b", "not_ready_to_move"),
    )

    def _objection_key(self, transcript: str) -> str:
        lowered = (transcript or "").lower()
        for pattern, key in self._OBJECTION_TYPE_MAPPING:
            if re.search(pattern, lowered):
                return key
        return ""

    def objection_hint(self, transcript: str, preset: str | None) -> str:
        self.ensure_loaded()
        pack = self._pack_id(preset)
        market = self._market.get(pack) or {}
        scripts: dict[str, str] = market.get("objection_scripts") or {}
        if not scripts:
            return ""
        key = self._objection_key(transcript)
        if key and key in scripts:
            return scripts[key][:350]
        return ""

    def objection_story(self, transcript: str, preset: str | None) -> str:
        """IQ #21 — grounded 30-second social-proof story for the objection type."""
        self.ensure_loaded()
        pack = self._pack_id(preset)
        market = self._market.get(pack) or {}
        stories: dict[str, str] = market.get("objection_stories") or {}
        if not stories:
            return ""
        key = self._objection_key(transcript)
        if key and key in stories:
            return stories[key][:400]
        return ""

    def scarcity_fact(self, transcript: str, preset: str | None) -> dict | None:
        """IQ #24 — real inventory scarcity ONLY (units_left / offer_expiry).

        Returns None when no genuine fact exists — scripted urgency is banned.
        """
        self.ensure_loaded()
        pack = self._pack_id(preset)
        listings = self._listing_index.get(pack) or {}
        aliases = self._listing_aliases.get(pack) or {}
        lowered = (transcript or "").lower()
        norm_t = _normalize(transcript or "")

        for alias, lid in aliases.items():
            if len(alias) < 4:
                continue
            if alias in norm_t or alias.replace("_", " ") in lowered:
                prop = listings.get(lid) or {}
                units = prop.get("units_left")
                expiry = prop.get("offer_expiry")
                if units or expiry:
                    return {
                        "listing_id": lid,
                        "name": prop.get("name", ""),
                        "units_left": units,
                        "offer_expiry": expiry,
                    }
        return None

    def resolve_turn(self, transcript: str, preset: str | None) -> TurnKnowledge:
        """Sync JSON hot-path lookup (no Qdrant — safe for unit tests)."""
        return self._resolve_turn_core(transcript, preset)

    async def resolve_turn_async(self, transcript: str, preset: str | None) -> TurnKnowledge:
        """Live-call path — optional Qdrant vector fallback when JSON FAQ score is low."""
        result = self._resolve_turn_core(transcript, preset)
        needs_vector = (
            result.faq is None
            or result.faq.confidence < CONFIDENCE_ALLOW
        )
        if needs_vector:
            vec_hit = await self._lookup_faq_qdrant(transcript, preset)
            if vec_hit and (
                result.faq is None or vec_hit.confidence > result.faq.confidence
            ):
                result.faq = vec_hit
                result.confidence = vec_hit.confidence
        return result

    def _resolve_turn_core(self, transcript: str, preset: str | None) -> TurnKnowledge:
        from employee_intent_router import EmployeeIntent, classify_employee_intent

        t0 = time.perf_counter()
        intent_result = classify_employee_intent(transcript)
        intent = intent_result.intent
        result = TurnKnowledge(intent=intent.value)

        knowledge_intents = {
            EmployeeIntent.COMPANY,
            EmployeeIntent.ROLE,
            EmployeeIntent.POLICY,
            EmployeeIntent.SMALL_TALK,
            EmployeeIntent.UNKNOWN,
        }
        if intent in knowledge_intents or intent == EmployeeIntent.PROPERTY:
            faq = self.lookup_faq(transcript, preset)
            if faq is None or faq.confidence < CONFIDENCE_ALLOW:
                intent_faq = self._lookup_faq_by_intent(transcript, intent_result, preset)
                if intent_faq and (
                    faq is None or intent_faq.confidence > faq.confidence
                ):
                    faq = intent_faq
            if faq:
                result.faq = faq
                result.confidence = faq.confidence

        if intent == EmployeeIntent.PROPERTY:
            result.inventory_lines = self.lookup_inventory(transcript, preset, force=True)
            result.market_line = self.lookup_market(transcript, preset)
            result.competitor_line = self.lookup_competitor(transcript, preset)
        else:
            if _PROPERTY_TRIGGERS.search(transcript):
                result.inventory_lines = self.lookup_inventory(transcript, preset)
            if _MARKET_TRIGGERS.search(transcript):
                result.market_line = self.lookup_market(transcript, preset)
                result.competitor_line = self.lookup_competitor(transcript, preset)

        result.objection_hint = self.objection_hint(transcript, preset)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        if result.has_content():
            logger.debug(
                "[KNOWLEDGE-PACK] Turn lookup %.1fms preset=%s intent=%s",
                elapsed_ms,
                self._pack_id(preset),
                intent.value,
            )
        return result

    async def _lookup_faq_qdrant(
        self,
        transcript: str,
        preset: str | None,
    ) -> FaqHit | None:
        """Vector fallback when QDRANT_URL is set; degrades silently to JSON path."""
        if not os.getenv("QDRANT_URL", "").strip():
            return None
        try:
            from knowledge_system.retrieval_engine import get_retrieval_engine

            engine = get_retrieval_engine()
            result = await engine.retrieve(transcript, top_k=3)
            if not result.success or not result.chunks:
                return None

            best = result.chunks[0]
            score = float(best.get("score") or 0.0)
            text = str(best.get("text") or best.get("content") or "").strip()
            if score < CONFIDENCE_LOOKUP_MIN or not text:
                return None

            return FaqHit(
                faq_id=str(best.get("chunk_id") or best.get("doc_id") or "qdrant"),
                answer=text[:500],
                category=str(best.get("metadata", {}).get("category") or "company"),
                confidence=min(score, 0.95),
                matched_question=transcript[:120],
            )
        except Exception as exc:
            logger.debug("[KNOWLEDGE-PACK] Qdrant fallback skipped: %s", exc)
            return None


_STORE = KnowledgePackStore()


def get_store() -> KnowledgePackStore:
    return _STORE


def resolve_preset_pack(preset: str | None) -> str:
    return _STORE._pack_id(preset)


def _normalize(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _tokens(text: str) -> set[str]:
    return {w for w in text.split() if w not in _STOPWORDS and len(w) > 2}


def _build_listing_aliases(listings: dict[str, dict]) -> dict[str, str]:
    """Precompute alias → listing_id map for fuzzy/transliterated mentions."""
    aliases: dict[str, str] = {}
    seen_props: set[str] = set()

    for key, prop in listings.items():
        if not isinstance(prop, dict) or "name" not in prop:
            continue
        lid = str(prop.get("listing_id") or key)
        if lid in seen_props:
            continue
        seen_props.add(lid)

        name = str(prop.get("name") or "")
        builder = str(prop.get("builder") or "")
        micro = str(prop.get("micro_market") or "")

        candidates = [
            name,
            builder,
            micro,
            lid.replace("_", " "),
            *name.split(),
            *builder.split(),
        ]
        for raw in candidates:
            slug = _slug(raw)
            if len(slug) >= 4:
                aliases[slug] = lid
            compact = slug.replace("_", "")
            if len(compact) >= 4:
                aliases[compact] = lid

        # Common caller shortenings
        lower_name = name.lower()
        if "krishe" in lower_name:
            aliases["krishe"] = lid
        if "madison" in lower_name:
            aliases["madison"] = lid
        if "clairmont" in lower_name:
            aliases["clairmont"] = lid
            aliases["clairemont"] = lid
        if "provincia" in lower_name:
            aliases["provincia"] = lid
        if "broadway" in lower_name:
            aliases["broadway"] = lid
        if "skyline" in lower_name:
            aliases["skyline"] = lid
        if "florenza" in lower_name:
            aliases["florenza"] = lid
        if "waterfront" in lower_name:
            aliases["waterfront"] = lid

    return aliases


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _match_score(
    norm_transcript: str,
    t_tokens: set[str],
    norm_question: str,
    q_tokens: frozenset[str] | set[str] | None = None,
) -> float:
    if norm_question in norm_transcript:
        return 1.0
    if len(norm_question) > 12 and norm_transcript in norm_question:
        return 0.92

    if q_tokens is None:  # legacy callers — tokenize on the fly
        q_tokens = _tokens(norm_question)
    if not q_tokens:
        return 0.0

    overlap = len(t_tokens & q_tokens)
    if overlap == 0:
        return 0.0

    recall = overlap / len(q_tokens)
    precision = overlap / max(len(t_tokens), 1)
    return 0.55 * recall + 0.45 * min(precision, 1.0)


def _format_property_line(prop: dict) -> str:
    name = prop.get("name", "Project")
    price = prop.get("price_range", "")
    possession = prop.get("possession", "")
    rera = prop.get("rera_id", "")
    usp = prop.get("usp", "")
    return (
        f"{name}: {price}, possession {possession}, RERA {rera}. "
        f"USP: {usp}"
    )[:420]


def get_escalation_phrase(preset: str | None) -> str:
    pack = resolve_preset_pack(preset)
    try:
        with open(_BACKSTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    phrases = data.get("escalation_phrases") or {}
    if pack == "hte":
        return (
            phrases.get("hte_hinglish")
            or phrases.get("hte_marathi")
            or "I'll confirm with Vikram sir and call you back in 10 minutes."
        )
    return phrases.get("tinglish") or "I'll confirm with Vikram sir and call you back in 10 minutes."


def format_turn_injection(turn: TurnKnowledge, *, preset: str | None = None) -> str | None:
    """Build a compact system injection for this turn only (<~250 tokens)."""
    if not turn.has_content():
        return None

    parts: list[str] = ["[KNOWLEDGE CONTEXT — USE THESE FACTS ONLY]"]
    if turn.intent and turn.intent != "unknown":
        parts.append(f"INTENT: {turn.intent.upper()}")

    if turn.faq:
        if turn.faq.confidence >= CONFIDENCE_ALLOW:
            parts.append(f"FAQ ({turn.faq.faq_id}): {turn.faq.answer}")
        else:
            parts.append(f"ESCALATE: {get_escalation_phrase(preset)}")
            return "\n".join(parts)

    for line in turn.inventory_lines[:2]:
        parts.append(f"LISTING: {line}")

    if turn.market_line:
        parts.append(f"MARKET: {turn.market_line}")

    if turn.competitor_line:
        parts.append(f"COMPETITOR: {turn.competitor_line}")

    if turn.objection_hint:
        parts.append(f"OBJECTION HINT: {turn.objection_hint}")

    parts.append("Answer in 1-2 natural sentences. Never invent prices or RERA IDs.")
    return "\n".join(parts)


def preload_knowledge_packs() -> None:
    """Call at worker prewarm — avoids first-call latency."""
    _STORE.ensure_loaded()