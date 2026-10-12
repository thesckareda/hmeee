"""
Sales Playbook — Sales methodology engine for real estate.

Turns the agent from a passive question-answerer into an active
sales performer using proven sales techniques:
  1. SPIN Selling framework (Situation→Problem→Implication→Need-payoff)
  2. Deal stage awareness and automatic progression
  3. Micro-commitment ladder
  4. Ethical urgency creation
  5. Price anchoring techniques
  6. Top-20 objection playbook with scripted responses
"""
from __future__ import annotations

import logging
import re
import random
from typing import Any

logger = logging.getLogger("inbound-voice-agent")


# ══════════════════════════════════════════════════════════════════════════════
# 1. DEAL STAGE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

class DealStage:
    AWARENESS = "awareness"        # Just browsing, early inquiry
    INTEREST = "interest"          # Asking specific questions
    CONSIDERATION = "consideration"# Comparing options, asking about pricing
    INTENT = "intent"              # Wants to visit, asking about process
    PURCHASE = "purchase"          # Ready to book/pay
    BOOKED = "booked"              # Site visit or booking confirmed


class DealStageTracker:
    """
    Track where the caller is in the sales funnel and suggest
    appropriate conversation strategies.
    """

    def __init__(self):
        self._stage: str = DealStage.AWARENESS
        self._signals: list[str] = []
        self._stage_history: list[tuple[str, str]] = []  # (stage, reason)

    @property
    def stage(self) -> str:
        return self._stage

    def process_utterance(self, text: str) -> str:
        """Analyze user utterance and update deal stage. Returns current stage."""
        lowered = (text or "").lower().strip()
        if not lowered:
            return self._stage

        prev_stage = self._stage

        # Purchase signals (highest priority)
        if re.search(r"\b(book|reserve|block|finalize|token|advance|payment|pay|registration)\b", lowered):
            self._advance_to(DealStage.PURCHASE, "purchase_signal")

        # Intent signals
        elif re.search(r"\b(visit|site\s*visit|dekh|show me|come|see the flat|aana chahta|sample flat|model)\b", lowered):
            self._advance_to(DealStage.INTENT, "visit_intent")

        # Consideration signals (comparing, pricing deep-dive)
        elif re.search(r"\b(compare|vs|versus|better|option|alternative|emi|loan|which one|konsa|best)\b", lowered):
            self._advance_to(DealStage.CONSIDERATION, "comparison_signal")

        elif re.search(r"\b(price|rate|cost|budget|afford|kitna|crore|lakh|total cost|all inclusive)\b", lowered):
            self._advance_to(DealStage.CONSIDERATION, "pricing_inquiry")

        # Interest signals (specific questions about features)
        elif re.search(r"\b(sqft|bhk|bedroom|balcony|amenity|parking|floor plan|facing|vastu|carpet)\b", lowered):
            self._advance_to(DealStage.INTEREST, "feature_inquiry")

        elif re.search(r"\b(tell me|details|more about|kya hai|features|specification)\b", lowered):
            self._advance_to(DealStage.INTEREST, "detail_inquiry")

        if self._stage != prev_stage:
            logger.info(
                "[SALES-STAGE] %s → %s (signal: %s)",
                prev_stage, self._stage, self._signals[-1] if self._signals else "unknown",
            )

        return self._stage

    def _advance_to(self, new_stage: str, reason: str) -> None:
        """Only advance forward in the funnel, never backward."""
        stage_order = [
            DealStage.AWARENESS,
            DealStage.INTEREST,
            DealStage.CONSIDERATION,
            DealStage.INTENT,
            DealStage.PURCHASE,
            DealStage.BOOKED,
        ]
        current_idx = stage_order.index(self._stage) if self._stage in stage_order else 0
        new_idx = stage_order.index(new_stage) if new_stage in stage_order else 0

        if new_idx > current_idx:
            self._stage = new_stage
            self._signals.append(reason)
            self._stage_history.append((new_stage, reason))

    def mark_booked(self) -> None:
        """Mark deal as booked."""
        self._stage = DealStage.BOOKED
        self._signals.append("booked")
        self._stage_history.append((DealStage.BOOKED, "confirmed_booking"))

    def get_strategy_instruction(self) -> str:
        """Get a system prompt instruction based on current deal stage."""
        strategies = {
            DealStage.AWARENESS: (
                "[SALES STRATEGY: AWARENESS STAGE]\n"
                "Caller is just exploring. Your goal: spark INTEREST.\n"
                "• Ask about their requirement — family size, preferred area, timeline.\n"
                "• Don't push pricing yet. Focus on understanding needs.\n"
                "• Mention 1-2 popular projects that might fit."
            ),
            DealStage.INTEREST: (
                "[SALES STRATEGY: INTEREST STAGE]\n"
                "Caller is interested in specific details. Your goal: move to CONSIDERATION.\n"
                "• Share relevant details enthusiastically.\n"
                "• Mention how the property solves their specific needs.\n"
                "• Introduce pricing naturally after building value."
            ),
            DealStage.CONSIDERATION: (
                "[SALES STRATEGY: CONSIDERATION STAGE]\n"
                "Caller is evaluating options. Your goal: create INTENT.\n"
                "• Help them compare objectively — position your project favorably.\n"
                "• Address price concerns with value framing and EMI breakdown.\n"
                "• Start suggesting a site visit: 'Seeing it in person makes all the difference.'"
            ),
            DealStage.INTENT: (
                "[SALES STRATEGY: INTENT STAGE — HOT LEAD]\n"
                "Caller wants to visit/see the property. Your goal: BOOK THE VISIT.\n"
                "• Be action-oriented. Lock down a specific date and time.\n"
                "• Use: 'This weekend Saturday morning works best for visits. 10 AM or 11 AM?'\n"
                "• Create gentle urgency: 'I'll check if the sample flat is available.'\n"
                "• After booking, suggest they bring family members."
            ),
            DealStage.PURCHASE: (
                "[SALES STRATEGY: PURCHASE STAGE — CLOSING]\n"
                "Caller is ready to commit. Your goal: CLOSE THE DEAL.\n"
                "• Summarize what they liked from the conversation.\n"
                "• Confirm the specific unit/configuration.\n"
                "• Explain next steps clearly: token amount, documents needed.\n"
                "• Create urgency: 'This specific unit has interest from another family.'"
            ),
            DealStage.BOOKED: (
                "[SALES STRATEGY: BOOKED — POST-COMMITMENT]\n"
                "Visit/booking is confirmed. Your goal: REINFORCE the decision.\n"
                "• Congratulate them warmly.\n"
                "• Confirm all details (date, time, location).\n"
                "• Offer to send location on WhatsApp.\n"
                "• Mention what to expect during the visit."
            ),
        }
        return strategies.get(self._stage, strategies[DealStage.AWARENESS])


# ══════════════════════════════════════════════════════════════════════════════
# 2. OBJECTION PLAYBOOK — Top 20 Objections with Scripted Responses
# ══════════════════════════════════════════════════════════════════════════════

# Each objection has multiple response variants (selected randomly)
OBJECTION_PLAYBOOK: dict[str, dict[str, Any]] = {
    "too_expensive": {
        "triggers": ["expensive", "costly", "too much", "budget", "mehnga", "zyada", "can't afford", "out of budget"],
        "responses": [
            "Haan, I understand. But if you look at the per-square-foot rate, it's actually quite competitive for this area. Plus, the builder offers staggered payments during construction. Want me to break down the EMI for you?",
            "Hmm, I get it. Actually, compared to similar projects in this area — like {competitor} — this is about 10-15% lower. And the location advantage is huge. Should I explain the payment plan?",
            "Budget is important, I agree. What if I told you the EMI comes to about {emi_estimate} per month? That's less than most people expect. And possession is in {possession_timeline}.",
        ],
        "strategy": "Reframe as value, break down into EMI, compare with competitors",
    },
    "location_far": {
        "triggers": ["far", "too far", "commute", "distance", "dur hai", "bahut dur", "travel time"],
        "responses": [
            "I hear that a lot actually, but have you seen the new flyover? It's cut the commute to Financial District to just 15-20 minutes. And with the metro coming, it'll be even better.",
            "Actually, that's what many people think initially. But once you visit, you'll see — the connectivity is much better than Google Maps shows. The outer ring road exit is right there.",
            "You know, 2 years ago Gachibowli was also considered 'far.' Now look at the prices there! Kokapet is the next Gachibowli. Getting in now is smart.",
        ],
        "strategy": "Counter with infrastructure development and future value",
    },
    "not_ready": {
        "triggers": ["not ready", "later", "next year", "thinking", "sochna hai", "abhi nahi", "baad mein", "not now"],
        "responses": [
            "Totally understand. No pressure at all. But you know what — prices in this area have been going up about 10-15% per year. Even if you're buying next year, it might be worth locking in today's price. Just something to think about.",
            "Of course, take your time. But let me suggest this — why not visit once? No commitment. At least you'll know the product, and if prices go up, you'll have a reference point.",
            "That's fine andi. But actually, many of our clients started exactly where you are. They came for a visit, liked it, and then came back when they were ready. I can arrange a no-pressure visit if you want?",
        ],
        "strategy": "No pressure, but plant seeds about price appreciation and opportunity cost",
    },
    "need_to_discuss": {
        "triggers": ["discuss with", "wife", "husband", "family", "spouse", "partner", "parents", "biwi", "pati"],
        "responses": [
            "Absolutely, that's important! Actually, why don't you both visit together? The sample flat experience is much better in person. Weekends work best for family visits. Saturday or Sunday?",
            "Of course! Family decision is very important. Tell you what — I'll send the brochure on WhatsApp. You can review it together and then we can plan a visit when everyone is free.",
            "Haan, that makes complete sense. Most of our clients bring the family for the site visit. Should I arrange for a weekend visit? It's quite an experience.",
        ],
        "strategy": "Turn the objection into a site visit opportunity for the whole family",
    },
    "comparing_other": {
        "triggers": ["compare", "other projects", "alternative", "better option", "looking at others", "dusra", "competitor"],
        "responses": [
            "Smart approach! Which other projects are you looking at? I can give you an honest comparison — I know most projects in this area well.",
            "Good that you're comparing. In this price range, the main competitors are {competitors}. Want me to give you a quick comparison? I'll be honest about where they're better too.",
            "That's the right way to do it. I always tell my clients — visit 3-4 projects, then decide. But make sure you include this one in your shortlist. The value difference is significant.",
        ],
        "strategy": "Position as an advisor, not a salesman. Offer honest comparison.",
    },
    "construction_delay": {
        "triggers": ["delay", "on time", "possession", "when ready", "kab milega", "late", "deadline"],
        "responses": [
            "Good question. This project is RERA registered, which means if the builder delays, there's a penalty clause protecting you. And I visited the site last week — construction is right on schedule.",
            "I completely understand that concern. Actually, {builder_name} has a really good track record — their last 3 projects were delivered on time. I can show you the RERA timeline when you visit.",
            "That's a valid concern. Tell you what — when you visit, I'll show you the actual construction progress. You can see for yourself. The structure is already at the {current_floor} floor.",
        ],
        "strategy": "Use RERA, builder track record, and physical evidence to reassure",
    },
    "resale_value": {
        "triggers": ["resale", "appreciation", "return", "investment", "rental", "roi", "value increase"],
        "responses": [
            "Good thinking! This area has seen about 15-20% appreciation in the last 2 years. And with the metro line coming and IT parks expanding, the growth story is strong.",
            "As an investment, this area is one of the best in Hyderabad right now. Rental yields are already at 3-3.5% and prices are only going up.",
            "I'll be honest — this is one of the best investment corridors right now. I've personally seen clients who bought 2 years ago sitting at 25-30% appreciation already.",
        ],
        "strategy": "Data-driven appreciation story, rental yield facts",
    },
    "no_interest": {
        "triggers": ["not interested", "don't want", "no thanks", "nahi chahiye", "no need"],
        "responses": [
            "No problem at all andi. If you change your mind or need any property advice in the future, feel free to call me anytime. Arjun from VK Realty. Take care!",
            "That's perfectly fine. I'll be here whenever you need. Should I save your number so I can share any good deals that come up in the future?",
        ],
        "strategy": "Graceful exit, leave the door open for future contact",
    },
    "vastu_concern": {
        "triggers": ["vastu", "direction", "facing", "north", "east", "south", "west", "entrance"],
        "responses": [
            "Vastu is important, I agree. This project actually has Vastu-compliant units — east-facing and north-facing options are available. Want me to check which specific units are Vastu-friendly?",
            "Good that you're checking vastu. Many of our clients ask about this. The {unit_type} units on the east side are very popular for exactly this reason.",
        ],
        "strategy": "Acknowledge importance, show Vastu-compliant options",
    },
    "loan_concern": {
        "triggers": ["loan", "emi", "bank approval", "home loan", "eligibility", "interest rate", "pre-approved"],
        "responses": [
            "This project has pre-approved home loans from SBI, HDFC, and ICICI. So the loan process is very smooth. I can connect you with a bank relationship manager if you want — they can give you an exact EMI calculation.",
            "Don't worry about the loan andi. The project is pre-approved, so approval takes about 7-10 days only. And currently interest rates are quite favorable.",
        ],
        "strategy": "Reassure with pre-approved status, offer to connect with bank RM",
    },
}


def detect_objection(text: str) -> tuple[str | None, dict | None]:
    """
    Detect if user text contains an objection.
    Returns (objection_type, playbook_entry) or (None, None).
    """
    if not text:
        return None, None
    lowered = text.lower()

    for obj_type, entry in OBJECTION_PLAYBOOK.items():
        if any(trigger in lowered for trigger in entry["triggers"]):
            return obj_type, entry

    return None, None


def get_objection_response(
    objection_type: str,
    *,
    variables: dict[str, str] | None = None,
    preset: str | None = None,
) -> str | None:
    """
    Get a scripted response for a detected objection.
    Variables like {competitor}, {emi_estimate} are replaced if provided.
    """
    entry = OBJECTION_PLAYBOOK.get(objection_type)
    if not entry:
        return None

    response = random.choice(entry["responses"])

    # Replace template variables
    if variables:
        for key, value in variables.items():
            response = response.replace(f"{{{key}}}", value)

    # Clean up any unreplaced variables
    response = re.sub(r"\{[a-z_]+\}", "the project", response)

    return response


# ══════════════════════════════════════════════════════════════════════════════
# 3. MICRO-COMMITMENT LADDER
# ══════════════════════════════════════════════════════════════════════════════

class MicroCommitmentTracker:
    """
    Guide caller through a series of small "yes" answers
    that build toward a larger commitment.

    Ladder:
    1. "Are you looking for residential?" → Yes
    2. "Is [area] convenient?" → Yes
    3. "Would a 3BHK work?" → Yes
    4. "Should I check site visit slots?" → Yes → BOOKED
    """

    def __init__(self):
        self._commitments: list[dict[str, Any]] = []
        self._next_question_idx: int = 0

    _COMMITMENT_QUESTIONS = [
        {
            "question": "residential or commercial",
            "trigger": "requirement_type",
            "affirm_keywords": ["residential", "flat", "apartment", "home", "house", "ghar"],
        },
        {
            "question": "preferred area",
            "trigger": "area_preference",
            "affirm_keywords": ["yes", "haan", "ha", "wahi", "nearby", "that area"],
        },
        {
            "question": "configuration",
            "trigger": "unit_config",
            "affirm_keywords": ["3bhk", "2bhk", "yes", "haan", "that size"],
        },
        {
            "question": "timeline",
            "trigger": "timeline",
            "affirm_keywords": ["soon", "jaldi", "this year", "ready", "immediate"],
        },
        {
            "question": "site visit",
            "trigger": "visit_commitment",
            "affirm_keywords": ["yes", "haan", "sure", "weekend", "saturday", "sunday", "ok"],
        },
    ]

    def record_commitment(self, topic: str, response: str, is_positive: bool) -> None:
        """Record a micro-commitment response."""
        self._commitments.append({
            "topic": topic,
            "response": response,
            "positive": is_positive,
        })
        if is_positive:
            self._next_question_idx += 1

    def get_next_commitment_question(self) -> str | None:
        """Get the next micro-commitment question to ask."""
        if self._next_question_idx >= len(self._COMMITMENT_QUESTIONS):
            return None
        return self._COMMITMENT_QUESTIONS[self._next_question_idx]["trigger"]

    @property
    def commitment_count(self) -> int:
        return sum(1 for c in self._commitments if c["positive"])

    @property
    def ready_for_close(self) -> bool:
        return self.commitment_count >= 3


# ══════════════════════════════════════════════════════════════════════════════
# 4. URGENCY CREATION (Ethical)
# ══════════════════════════════════════════════════════════════════════════════

# IQ #24 — scarcity WITHOUT theater. Scripted urgency is banned at the rule
# level: a phrase is produced ONLY from a real inventory fact
# (knowledge_pack.scarcity_fact → units_left / offer_expiry). No fact → "".

def get_urgency_phrase(
    variables: dict[str, str] | None = None,
    *,
    scarcity_fact: dict | None = None,
) -> str:
    """Grounded urgency phrase. Returns "" when no real scarcity fact exists."""
    fact = scarcity_fact or {}
    units = fact.get("units_left")
    expiry = fact.get("offer_expiry")
    name = str(fact.get("name") or "this project")

    if units:
        return (
            f"{name} lo actually {units} units ae migilayi — "
            "fake urgency create cheyanu, idi actual availability."
        )
    if expiry:
        return (
            f"{name} current offer {expiry} varaku ae valid — "
            "idi real deadline, pressure kadu, meeku telisi undali ani cheptunnanu."
        )
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# 5. SYSTEM PROMPT: SALES PLAYBOOK INJECTION
# ══════════════════════════════════════════════════════════════════════════════

def build_sales_playbook_instruction(deal_stage: str = DealStage.AWARENESS) -> str:
    """Build sales methodology instruction for the system prompt."""
    tracker = DealStageTracker()
    tracker._stage = deal_stage
    stage_strategy = tracker.get_strategy_instruction()

    return (
        "\n\n[SALES METHODOLOGY — ACT LIKE A TOP PERFORMER]\n"
        "You are not just answering questions — you are SELLING. Every response should:\n"
        "1. ANSWER the question directly\n"
        "2. ADD value (one extra useful fact)\n"
        "3. ASK a question to maintain control\n\n"
        "Pattern: Answer → Value Add → Question\n"
        "Example:\n"
        "  Caller: 'What's the price?'\n"
        "  You: 'The 3BHK starts at 1.15 Crore. Actually, the builder is offering a 5% early-bird discount right now. Have you visited the area before?'\n\n"
        f"{stage_strategy}\n\n"
        "HANDLING OBJECTIONS:\n"
        "• Never agree with negative framing. Reframe positively.\n"
        "• 'Too expensive' → 'Let me show you the EMI breakdown — it's lower than you'd think'\n"
        "• 'Too far' → 'With the new flyover, it's only 15 minutes to Financial District'\n"
        "• 'Not ready' → 'No rush, but prices went up 15% last year. Even a visit helps you benchmark'\n"
        "• 'Need to discuss with family' → 'Absolutely! Why not bring them for a visit this weekend?'\n\n"
        "CLOSING MOVES:\n"
        "• After 3+ positive signals, propose a specific action.\n"
        "• Use either/or: 'Saturday or Sunday?', '10 AM or 11 AM?'\n"
        "• After booking, reinforce: 'Great choice! You'll love the sample flat.'\n\n"
        "NEGOTIATION MICRO-SKILLS:\n"
        "• Anchor high on value before price — location, builder track record, possession timeline.\n"
        "• After quoting price, pause. Do not immediately offer a discount.\n"
        "• Alternative close: 'Shall I block Saturday 10 AM, or would Sunday work better?'\n"
        "• If they push on price: reframe to EMI/per-sqft vs area average, not a straight discount.\n"
    )
