"""
Conversational IQ — Human-level response quality and naturalness.

Makes every response sound like a top-performing sales rep, not a chatbot.
Provides:
  1. Response length controller — dynamic sizing based on context
  2. Natural disfluency system — self-corrections, thinking pauses
  3. Topic memory — never repeat information already discussed
  4. Proactive insight engine — volunteer useful next-step info
  5. Conversational mastery prompt builder
"""
from __future__ import annotations

import logging
import random
import re
from typing import Any

logger = logging.getLogger("inbound-voice-agent")


# ══════════════════════════════════════════════════════════════════════════════
# 1. RESPONSE LENGTH CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

class ResponseLengthPolicy:
    """
    Dynamically control response length based on conversational context.

    A real salesperson adapts response length to the situation:
    - Simple yes/no question → 5-8 words
    - Complex pricing question → 2 sentences max
    - Objection → 1-2 sentences of empathy + 1 sentence redirect
    - Greeting → warm but brief
    - Closing → efficient, action-oriented
    """

    # Question complexity → target word range
    _LENGTH_PROFILES = {
        "acknowledgement": (3, 8),     # "Sure", "Got it", "Haan"
        "yes_no": (5, 15),             # Direct answers
        "simple_fact": (10, 25),       # Price, location, size
        "explanation": (20, 45),       # How EMI works, area details
        "objection_response": (15, 35),# Empathy + redirect
        "site_visit_pitch": (20, 40),  # Persuasive but concise
        "greeting": (8, 20),           # Warm, welcoming
        "farewell": (8, 20),           # Quick, warm goodbye
        "default": (12, 30),           # Safe middle ground
    }

    @classmethod
    def classify_expected_length(cls, user_input: str, turn_count: int = 0) -> tuple[int, int]:
        """
        Classify user input and return (min_words, max_words) for the response.
        """
        lowered = (user_input or "").strip().lower()

        if not lowered or len(lowered.split()) < 2:
            return cls._LENGTH_PROFILES["acknowledgement"]

        # Yes/No questions
        if re.search(r"\b(kya|is|do|can|will|should|would|haan|nahi|available|possible)\b", lowered):
            if len(lowered.split()) < 8:
                return cls._LENGTH_PROFILES["yes_no"]

        # Simple facts
        if re.search(r"\b(price|rate|cost|kitna|kya hai|area|size|sqft|bhk|location|address|where)\b", lowered):
            return cls._LENGTH_PROFILES["simple_fact"]

        # Objections
        if re.search(r"\b(expensive|costly|far|small|budget|think|sochna|baad mein|later|not sure|confused)\b", lowered):
            return cls._LENGTH_PROFILES["objection_response"]

        # Explanations
        if re.search(r"\b(how|why|explain|tell me about|details|emi|loan|registration|process)\b", lowered):
            return cls._LENGTH_PROFILES["explanation"]

        # Greetings
        if re.search(r"\b(hello|hi|namaste|good morning|good evening|hey)\b", lowered) and turn_count < 3:
            return cls._LENGTH_PROFILES["greeting"]

        # Farewells
        if re.search(r"\b(bye|goodbye|thank you|thanks|okay bye|chal|chalo)\b", lowered):
            return cls._LENGTH_PROFILES["farewell"]

        # Default: adapt based on turn count
        if turn_count > 15:
            return (10, 25)  # Later turns → more efficient
        return cls._LENGTH_PROFILES["default"]

    @classmethod
    def build_length_instruction(cls, user_input: str, turn_count: int = 0) -> str:
        """Build a system instruction to control response length."""
        min_w, max_w = cls.classify_expected_length(user_input, turn_count)
        return (
            f"\n[RESPONSE LENGTH: {min_w}-{max_w} words. "
            "Be concise like a real person on a phone call. "
            "No one gives long speeches on the phone. "
            "Answer directly, then stop.]"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. NATURAL DISFLUENCY SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

class DisfluencyInjector:
    """
    Inject controlled human-like speech patterns into LLM output.

    A perfectly fluent speaker sounds robotic on the phone. Real people:
    - Self-correct: "The price is 1.2 Crore — wait, actually 1.15 for the 2BHK"
    - Pause to think: "Hmm, that's a good question..."
    - Use bridges: "So actually...", "You know what..."
    - Hedge: "I think...", "If I'm not wrong..."
    """

    # Probability of injecting disfluency (per response)
    _INJECTION_PROBABILITY = 0.35  # 35% of responses get a disfluency

    # Thinking/hesitation markers
    _THINKING_MARKERS = [
        "Hmm, ",
        "Actually, ",
        "So, ",
        "You know, ",
        "Waise, ",
        "Dekho, ",
        "Haan, toh ",
        "Accha, ",
    ]

    # Self-correction patterns (inject mid-sentence)
    _SELF_CORRECTIONS = [
        " — actually, ",
        " — wait, let me correct that — ",
        " — sorry, I mean ",
        " — no, actually ",
    ]

    # Conversational bridges (between topics)
    _TOPIC_BRIDGES = [
        "And actually, one more thing — ",
        "Oh, and also — ",
        "Waise, ek baat aur — ",
        "By the way, ",
        "Aur ek important thing — ",
    ]

    # Hedging phrases (soften absolute statements)
    _HEDGES = [
        "I think ",
        "If I'm not wrong, ",
        "Mostly, ",
        "Usually, ",
        "From what I know, ",
        "Agar mujhe sahi yaad hai toh, ",
    ]

    @classmethod
    def maybe_inject(cls, text: str, turn_count: int = 0) -> str:
        """
        Maybe inject a natural disfluency into the response text.
        Returns modified text or original if no injection.
        """
        if not text or not text.strip():
            return text

        # Don't inject on very short responses
        words = text.split()
        if len(words) < 6:
            return text

        # Don't inject on greetings
        if turn_count < 2:
            return text

        # Random injection based on probability
        if random.random() > cls._INJECTION_PROBABILITY:
            return text

        # Choose injection type
        injection_type = random.choice(["thinking", "hedge"])

        if injection_type == "thinking":
            marker = random.choice(cls._THINKING_MARKERS)
            # Add thinking marker at the start
            if not any(text.startswith(m) for m in cls._THINKING_MARKERS):
                return marker + text[0].lower() + text[1:]

        elif injection_type == "hedge":
            hedge = random.choice(cls._HEDGES)
            # Add hedge to start of factual statements
            if not any(text.startswith(h) for h in cls._HEDGES):
                return hedge + text[0].lower() + text[1:]

        return text


# ══════════════════════════════════════════════════════════════════════════════
# 3. TOPIC MEMORY — Track discussed topics to avoid repetition
# ══════════════════════════════════════════════════════════════════════════════

class TopicMemory:
    """
    Track topics already discussed in this conversation.
    Prevents the agent from repeating information the caller already heard.
    """

    def __init__(self):
        self._discussed_topics: set[str] = set()
        self._discussed_listings: set[str] = set()
        self._discussed_facts: dict[str, str] = {}  # fact_key → what was said
        self._questions_asked: list[str] = []  # track caller's questions
        self._info_provided: list[str] = []  # what info was already given

    def mark_discussed(self, topic: str, detail: str = "") -> None:
        """Mark a topic as discussed."""
        self._discussed_topics.add(topic.lower().strip())
        if detail:
            self._discussed_facts[topic.lower().strip()] = detail

    def mark_listing_discussed(self, listing_id: str) -> None:
        """Mark a specific listing as discussed."""
        self._discussed_listings.add(listing_id.lower().strip())

    def was_discussed(self, topic: str) -> bool:
        """Check if a topic was already discussed."""
        return topic.lower().strip() in self._discussed_topics

    def was_listing_discussed(self, listing_id: str) -> bool:
        """Check if a listing was already discussed."""
        return listing_id.lower().strip() in self._discussed_listings

    def add_question(self, question: str) -> None:
        """Record a question the caller asked."""
        self._questions_asked.append(question)
        if len(self._questions_asked) > 20:
            self._questions_asked = self._questions_asked[-20:]

    def add_info_provided(self, info: str) -> None:
        """Record information we've already given."""
        self._info_provided.append(info)
        if len(self._info_provided) > 30:
            self._info_provided = self._info_provided[-30:]

    def build_no_repeat_instruction(self) -> str:
        """Build a system instruction to prevent repetition."""
        if not self._discussed_topics and not self._info_provided:
            return ""

        parts = ["\n[NO REPETITION — Topics already discussed:"]
        if self._discussed_topics:
            topics = ", ".join(sorted(self._discussed_topics)[:10])
            parts.append(f"  Topics: {topics}")
        if self._discussed_listings:
            listings = ", ".join(sorted(self._discussed_listings)[:5])
            parts.append(f"  Listings discussed: {listings}")
        if self._info_provided:
            recent = "; ".join(self._info_provided[-5:])
            parts.append(f"  Info already given: {recent}")
        parts.append("  Do NOT repeat any of the above. Add NEW value or ask a NEW question.]")
        return "\n".join(parts)

    def extract_topics_from_transcript(self, role: str, text: str) -> None:
        """Auto-extract discussed topics from a transcript turn."""
        lowered = text.lower()

        # Common real estate topics
        topic_keywords = {
            "price": ["price", "rate", "cost", "crore", "lakh", "emi", "kitna"],
            "location": ["location", "area", "kokapet", "gachibowli", "financial district", "where"],
            "size": ["bhk", "sqft", "square feet", "carpet", "built-up", "size"],
            "amenities": ["amenity", "gym", "pool", "parking", "garden", "club"],
            "possession": ["possession", "ready", "under construction", "handover", "completion"],
            "loan": ["loan", "bank", "emi", "home loan", "interest rate", "pre-approved"],
            "rera": ["rera", "approved", "registration", "legal", "clear title"],
            "site_visit": ["visit", "see the flat", "dekh", "show me", "come and see"],
        }

        for topic, keywords in topic_keywords.items():
            if any(kw in lowered for kw in keywords):
                self.mark_discussed(topic, text[:80])

        # Track listing IDs mentioned
        listing_patterns = re.findall(r"\b(godrej|myhome|signature|housing|indroyal|tricolt|trilight)\b", lowered)
        for lid in listing_patterns:
            self.mark_listing_discussed(lid)


# ══════════════════════════════════════════════════════════════════════════════
# 4. PROACTIVE INSIGHT ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class ProactiveInsightEngine:
    """
    After answering a question, proactively offer the NEXT logical piece
    of information a sales rep would mention.

    Pattern: Answer → Proactive follow-up → Question to maintain control

    Example:
      Caller: "What's the price of 3BHK?"
      Normal AI: "The price is 1.15 Crore."
      Top sales rep: "The price is 1.15 Crore for the 3BHK. Actually, the
                     builder is offering a pre-launch discount right now.
                     Would you like to visit this weekend?"
    """

    _FOLLOW_UP_MAP = {
        "price": [
            "Actually, there's a payment plan option too — staggered payments during construction.",
            "And the good thing is, home loan is pre-approved from SBI and HDFC for this project.",
            "The price includes one covered parking. Second parking is separate.",
        ],
        "location": [
            "The area is developing really fast — metro line is expected by next year.",
            "Good connectivity to IT corridor — 15-20 minutes to Financial District.",
            "Schools and hospitals are all within 5 km radius.",
        ],
        "size": [
            "The carpet area is quite generous compared to similar projects in the area.",
            "They also have jodi flats if you need more space.",
        ],
        "possession": [
            "Construction is on schedule — I visited the site last week and the progress is good.",
            "RERA registered, so there's a penalty clause if they delay.",
        ],
        "site_visit": [
            "The sample flat is ready — it gives you a very good idea of the finished product.",
            "I can also show you 2-3 other projects in the same area if you'd like to compare.",
        ],
        "general": [
            "Should I send you the brochure on WhatsApp? It has all the floor plans and pricing.",
            "Many of our clients from the IT sector are looking at this project. Good investment.",
        ],
    }

    @classmethod
    def get_proactive_followup(cls, topic: str, topics_covered: set[str] | None = None) -> str | None:
        """
        Get a proactive follow-up line for a discussed topic.
        Returns None if no relevant follow-up available.
        """
        options = cls._FOLLOW_UP_MAP.get(topic, cls._FOLLOW_UP_MAP.get("general", []))
        if not options:
            return None

        # Filter out options related to already-covered topics
        if topics_covered:
            options = [o for o in options if not any(t in o.lower() for t in topics_covered)]

        if not options:
            return None

        return random.choice(options)


# ══════════════════════════════════════════════════════════════════════════════
# 5. CONVERSATIONAL MASTERY PROMPT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_conversational_mastery_prompt(
    lang_preset: str = "",
    turn_count: int = 0,
    emotion_state: str = "",
    deal_stage: str = "",
) -> str:
    """
    Build the conversational mastery instruction block for the system prompt.

    This replaces the existing _CONVERSATIONAL_MASTERY_COMMON in agent.py
    with a much richer, employee-focused version.
    """
    base = (
        "\n\n[CONVERSATIONAL MASTERY — SOUND LIKE A REAL PERSON]\n"
        "• Talk like a real person on a phone call — NOT like a written document.\n"
        "• Use SHORT sentences. Nobody gives speeches on the phone.\n"
        "• Match the caller's energy — if they're excited, be excited. If they're calm, be calm.\n"
        "• Use natural transitions: 'So...', 'Actually...', 'You know what...', 'Waise...'\n"
        "• Occasionally self-correct: 'The price is 1.2 — actually, 1.15 for the 2BHK'\n"
        "• Ask ONE question at a time. Never ask 3 questions in a row.\n"
        "• After answering, add ONE useful extra detail, then ask a follow-up question.\n"
        "  Pattern: Answer → Extra value → Question to maintain control.\n"
        "• NEVER use bullet points, numbered lists, or formatted text in speech.\n"
        "• NEVER say 'Great question!' or 'That's a great question!' — only AI says that.\n"
        "• NEVER say 'I understand your concern' robotically — instead: 'Haan, samajh mein aata hai'\n"
        "• NEVER start with 'Sure!' or 'Absolutely!' — real people don't talk like that.\n"
        "• Use filler words naturally: 'toh', 'actually', 'basically', 'you know', 'waise'\n"
        "• Sound slightly informal — like talking to a friendly colleague, not giving a presentation.\n"
    )

    # Turn-specific adjustments
    if turn_count < 3:
        base += (
            "• EARLY CALL: Be warm and welcoming. Establish rapport before jumping to business.\n"
            "• Ask about their requirement with genuine curiosity, not like a form.\n"
        )
    elif turn_count > 15:
        base += (
            "• LATE CALL: Be efficient. Summarize and drive toward action.\n"
            "• If no site visit booked yet, gently push for one.\n"
        )

    # Emotion-aware adjustments
    if emotion_state == "frustrated":
        base += (
            "• CALLER IS FRUSTRATED: Lead with empathy. 'Haan, main samajh sakta hu.'\n"
            "• Don't be defensive. Acknowledge their frustration, then redirect positively.\n"
        )
    elif emotion_state == "enthusiastic":
        base += (
            "• CALLER IS EXCITED: Match their energy! Use enthusiasm.\n"
            "• This is a hot lead — guide them toward booking immediately.\n"
        )
    elif emotion_state == "hesitant":
        base += (
            "• CALLER IS HESITANT: Be patient and reassuring. Don't push too hard.\n"
            "• Offer information, not pressure. 'No rush, but I can tell you more if you want.'\n"
        )
    elif emotion_state == "skeptical":
        base += (
            "• CALLER IS SKEPTICAL: Use facts and social proof. 'Actually, last month 4 families booked here.'\n"
            "• Don't be pushy. Let the facts speak.\n"
        )

    # Deal stage adjustments
    if deal_stage == "closing":
        base += (
            "• CLOSING STAGE: Summarize what they liked, address last concerns, propose next step.\n"
            "• Use: 'So, based on everything we discussed, should I block this unit for you?'\n"
        )

    return base


def build_anti_ai_response_patterns() -> str:
    """
    Build instruction that eliminates common AI response patterns.
    These patterns make it obvious the response is from a chatbot.
    """
    return (
        "\n\n[ANTI-AI PATTERNS — NEVER USE THESE]\n"
        "NEVER use any of these AI-sounding patterns:\n"
        "• 'Sure! I'd be happy to help you with that.' — robot phrase\n"
        "• 'That's a great question!' — only AI says this\n"
        "• 'I understand your concern.' — scripted, not genuine\n"
        "• 'Let me help you with that.' — overly formal\n"
        "• 'Based on the information provided...' — report language\n"
        "• 'I hope this helps!' — AI sign-off\n"
        "• 'Is there anything else I can help you with?' — call center script\n"
        "• 'Thank you for your patience.' — robotic\n"
        "• 'I apologize for any inconvenience.' — customer service bot\n"
        "• Starting every sentence with 'I' — vary your sentence structure\n"
        "• Using emojis or special characters in speech\n"
        "• Perfect grammar and punctuation — real speech has fragments\n"
        "\nINSTEAD, use real human patterns:\n"
        "• 'Haan, so basically...' instead of 'Sure! Let me explain.'\n"
        "• 'Hmm, accha point hai' instead of 'That's a great question!'\n"
        "• 'Dekho, actually...' instead of 'Based on the information...'\n"
        "• 'Aur kuch chahiye?' instead of 'Is there anything else I can help with?'\n"
        "• 'Theek hai, toh...' instead of 'Thank you for your patience.'\n"
    )
