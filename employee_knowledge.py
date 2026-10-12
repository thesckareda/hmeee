"""
Employee Knowledge — Company & personal knowledge layer.

Provides the agent with answers to company questions, personal questions,
market commentary, and competitive knowledge. A real employee knows about
their company, team, and industry — the AI must too.

Layers:
  1. Company FAQ (who founded, team size, office, USPs)
  2. Role-aware responses (daily routine, work experience)
  3. Market commentary (trends, interest rates, area insights)
  4. Competitive knowledge (other builders, comparison points)
  5. Personal question handler (natural deflections)
"""
from __future__ import annotations

import logging
import random
import re
from typing import Any

logger = logging.getLogger("inbound-voice-agent")


# ══════════════════════════════════════════════════════════════════════════════
# 1. COMPANY FAQ ENGINE
# ══════════════════════════════════════════════════════════════════════════════

# Default company knowledge — override via config or data/company_knowledge.json
_COMPANY_FAQ: dict[str, list[str]] = {
    "who_founded": [
        "Vikram sir started VK Realty about 8 years ago. He's been in real estate for over 15 years now.",
        "Our founder is Vikram sir. He started VK Realty with a vision to make property buying simple and transparent.",
    ],
    "how_many_employees": [
        "We're a team of about 25-30 people. Pretty tight-knit group.",
        "Around 25-30 people. Growing fast though — just hired 3 new consultants last month.",
    ],
    "office_location": [
        "Our main office is in Gachibowli, near the tech park. Easy to find — right next to the flyover.",
        "We're in Gachibowli. Very accessible if you're coming from HITEC City or Financial District.",
    ],
    "what_does_company_do": [
        "We help people find the right property — residential and commercial. Mostly in Hyderabad, focused on areas like Kokapet, Gachibowli, Financial District.",
        "VK Realty is a real estate consultancy. We work with top builders like Godrej, My Home, and Signature Global. Our job is to find you the perfect property.",
    ],
    "company_usp": [
        "Our biggest strength is we don't just sell — we advise. I'll honestly tell you if a property is not right for you.",
        "What makes us different is transparency. We share actual price sheets, not inflated quotes. And our after-sales support is really good.",
    ],
    "service_areas": [
        "We mainly cover Hyderabad — Kokapet, Gachibowli, Financial District, Narsingi, Puppalguda, and now expanding to Shamshabad corridor.",
        "All of west Hyderabad is our focus area. Kokapet, Gachibowli, FD, Narsingi. These are the hottest markets right now.",
    ],
    "working_hours": [
        "We're open Monday to Saturday, 9 AM to 7 PM. I'm usually available even on Sunday by appointment.",
        "Office hours are 9 to 7, Monday to Saturday. But you can call me anytime — I usually pick up till 9 PM.",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# 2. MARKET COMMENTARY
# ══════════════════════════════════════════════════════════════════════════════

_MARKET_INSIGHTS: dict[str, list[str]] = {
    "hyderabad_market": [
        "Hyderabad market is doing really well right now. Especially the western corridor — Kokapet, FD, Gachibowli have seen 15-20% appreciation in the last year.",
        "Honestly, Hyderabad is one of the best real estate markets in India right now. IT growth is driving demand, and supply is controlled. Good time to invest.",
    ],
    "interest_rates": [
        "Home loan rates are around 8.5-9% right now. SBI is at 8.5, HDFC at 8.75. It's decent, not the best we've seen but still very manageable.",
        "Interest rates are stable right now. If you compare with 2-3 years ago, they're about the same. The EMI on a crore comes to roughly 75-80K per month.",
    ],
    "kokapet": [
        "Kokapet is the hottest micro-market in Hyderabad right now. The Regional Ring Road and flyover connectivity have made it very accessible. Prices have doubled in 3 years.",
        "I'm bullish on Kokapet. The infrastructure development is amazing — flyover, RRR, metro extension. It's the next Gachibowli, and we all know what happened to Gachibowli prices.",
    ],
    "gachibowli": [
        "Gachibowli is more mature now — prices are higher but stable. If you're looking for appreciation, Kokapet gives better returns. But for ready possession, Gachibowli has great options.",
    ],
    "financial_district": [
        "Financial District is premium — you're paying for the location. Great for professionals working in the area. Rental yields are good too.",
    ],
    "investment_advice": [
        "For pure investment, look at under-construction projects in Kokapet — you get pre-launch prices. For living, ready-to-move in Gachibowli is comfortable.",
        "I always tell my clients — if your timeline is 2-3 years, buy under construction. You save 15-20% versus ready-to-move-in. And appreciation happens during construction too.",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# 3. COMPETITIVE KNOWLEDGE
# ══════════════════════════════════════════════════════════════════════════════

_COMPETITOR_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "godrej": {
        "name": "Godrej Properties",
        "positives": ["Trusted brand name", "Good construction quality", "RERA compliant", "Strong resale value"],
        "pricing": "Premium — about 10-15% above average for the area",
        "commentary": "Godrej is a safe bet. Brand value is strong, construction quality is excellent. You pay a premium, but it's worth it for the peace of mind.",
    },
    "myhome": {
        "name": "My Home Group",
        "positives": ["Local Hyderabad brand", "Excellent track record", "On-time delivery", "Good amenities"],
        "pricing": "Mid-premium — competitive for what they offer",
        "commentary": "My Home has delivered some of the best projects in Hyderabad. Very reliable builder. Their Kokapet project is especially popular.",
    },
    "prestige": {
        "name": "Prestige Group",
        "positives": ["Bangalore-based, entering Hyderabad", "Premium brand", "Integrated townships"],
        "pricing": "Premium — positioning as luxury",
        "commentary": "Prestige is new to Hyderabad but they've done amazing work in Bangalore. Their Hyderabad projects are ambitious.",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# 4. QUESTION CLASSIFIER & KNOWLEDGE RETRIEVAL
# ══════════════════════════════════════════════════════════════════════════════

def classify_knowledge_question(text: str) -> tuple[str, str] | None:
    """
    Classify a question into a knowledge category.
    Returns (category, sub_topic) or None if not a knowledge question.
    """
    lowered = (text or "").lower().strip()
    if not lowered:
        return None

    # Company questions
    company_patterns = [
        (r"\b(who (?:founded|started|owns)|founder|owner|boss)\b", "company", "who_founded"),
        (r"\b(how many (?:people|employees|staff|team))\b", "company", "how_many_employees"),
        (r"\b(where (?:is|are) (?:your|the) (?:office|location))\b", "company", "office_location"),
        (r"\b(what (?:do|does) (?:your|the) (?:company|VK) (?:do|offer))\b", "company", "what_does_company_do"),
        (r"\b(why (?:should I|choose|pick|go with) (?:you|VK))\b", "company", "company_usp"),
        (r"\b((?:which|what) (?:areas?|locations?|places?) (?:do you|you) (?:cover|serve|work))\b", "company", "service_areas"),
        (r"\b((?:what are|what's) (?:your|the) (?:working|office) hours?)\b", "company", "working_hours"),
        (r"\b(open|close|timing|hours|kab tak)\b", "company", "working_hours"),
    ]

    for pattern, category, topic in company_patterns:
        if re.search(pattern, lowered):
            return (category, topic)

    # Market questions
    market_patterns = [
        (r"\b(market|real estate (?:market|trend|outlook))\b", "market", "hyderabad_market"),
        (r"\b(interest rate|loan rate|home loan rate|emi rate)\b", "market", "interest_rates"),
        (r"\b(kokapet|kokapet area)\b", "market", "kokapet"),
        (r"\b(gachibowli|gachi)\b", "market", "gachibowli"),
        (r"\b(financial district|FD|fin district)\b", "market", "financial_district"),
        (r"\b(invest|investment|return|appreciate|roi)\b", "market", "investment_advice"),
    ]

    for pattern, category, topic in market_patterns:
        if re.search(pattern, lowered):
            return (category, topic)

    return None


def get_knowledge_response(category: str, topic: str) -> str | None:
    """Get a knowledge-base response for a classified question."""
    if category == "company":
        options = _COMPANY_FAQ.get(topic, [])
    elif category == "market":
        options = _MARKET_INSIGHTS.get(topic, [])
    else:
        return None

    if not options:
        return None
    return random.choice(options)


def get_competitor_comparison(competitor_id: str) -> str | None:
    """Get competitive knowledge for a specific builder."""
    entry = _COMPETITOR_KNOWLEDGE.get(competitor_id.lower())
    if not entry:
        return None
    return entry.get("commentary")


# ══════════════════════════════════════════════════════════════════════════════
# 5. SYSTEM PROMPT: KNOWLEDGE INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def build_employee_knowledge_instruction(
    backstory: dict | None = None,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    """Build the knowledge-aware instruction block for the system prompt."""
    from knowledge_pack import get_store, resolve_preset_pack

    bs = backstory or {}
    company = bs.get("company", "VK Realty")
    manager = bs.get("manager", "Vikram Khanna")
    experience = bs.get("experience_years", 4)
    pack = resolve_preset_pack(lang_preset or (live_config or {}).get("lang_preset"))

    office = bs.get("office_location", "Gachibowli")
    if isinstance(office, dict):
        office = office.get(pack) or office.get("tinglish") or next(iter(office.values()), "")

    fav = bs.get("favorite_area", "")
    if isinstance(fav, dict):
        fav = fav.get(pack) or next(iter(fav.values()), "")

    hot_block = get_store().hot_facts_block(lang_preset or (live_config or {}).get("lang_preset"))

    base = (
        f"\n\n[EMPLOYEE KNOWLEDGE — YOU KNOW YOUR COMPANY]\n"
        f"You work at {company}. Manager: {manager}. Office: {office}.\n"
        f"You've been here {experience} years. Favorite corridor: {fav}.\n"
        f"{bs.get('properties_sold', '50+ deals closed')}.\n\n"
        "When asked company/industry questions:\n"
        "• Answer from KNOWLEDGE CONTEXT facts only — never invent RERA IDs or prices\n"
        "• For market questions: brief opinion + 1-2 verified facts\n"
        "• For competitor questions: balanced — acknowledge strengths, then your edge\n"
        "• If unsure: use escalation phrase — confirm with Vikram sir and call back\n"
        "  NEVER say 'I don't have access to that information' — that's AI talk.\n"
    )
    if hot_block:
        return base + "\n" + hot_block
    return base
