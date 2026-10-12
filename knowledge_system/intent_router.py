"""
Intent classification for Second Brain routing.

Determines what type of query the caller is making so we can route
appropriately (retrieval vs deterministic workflow).
"""

import logging
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("intent-router")

# ──────────────────────────────────────────────────────────────────────────────
# Intent Types
# ──────────────────────────────────────────────────────────────────────────────


class Intent(str, Enum):
    """Supported intents for routing."""
    
    # Knowledge retrieval
    PRICING_QUERY = "pricing_query"
    SERVICE_INFO = "service_info"
    # Real estate-specific emergency keywords
    CLINIC_INFO = "office_info"
    FAQ = "faq"
    POLICY_QUERY = "policy_query"
    LOCATION_QUERY = "location_query"
    HOURS_QUERY = "hours_query"
    
    # Deterministic workflows
    APPOINTMENT_BOOKING = "appointment_booking"
    APPOINTMENT_RESCHEDULE = "appointment_reschedule"
    APPOINTMENT_CANCEL = "appointment_cancel"
    APPOINTMENT_CHECK = "appointment_check"
    
    # Emergency/escalation
    EMERGENCY = "emergency"
    ESCALATION_REQUEST = "escalation_request"
    
    # General
    GREETING = "greeting"
    UNKNOWN = "unknown"


@dataclass
class ClassificationResult:
    """Result of intent classification."""
    
    intent: Intent
    confidence: float  # 0.0 - 1.0
    keywords_matched: List[str]
    explanation: str


# ──────────────────────────────────────────────────────────────────────────────
# Intent Classifier
# ──────────────────────────────────────────────────────────────────────────────


class IntentRouter:
    """
    Classify user intents from spoken input.
    
    Uses keyword matching and heuristics (lightweight, no LLM).
    """
    
    # Intent keywords (grouped by language)
    INTENT_KEYWORDS = {
        Intent.PRICING_QUERY: {
            "en": ["cost", "price", "fees", "charge", "how much", "expensive", "affordable", "rates"],
            "hi": ["kimat", "daam", "kitna", "kharcha", "paisa"],
        },
        Intent.SERVICE_INFO: {
            "en": ["service", "property", "listing", "amenity", "villa", "apartment", "flat", "plot", "house", "commercial", "office"],
            "hi": ["property", "flat", "ghar", "makaan", "plot", "villa", "office"],
        },
        Intent.APPOINTMENT_BOOKING: {
            "en": ["book", "visit", "site visit", "site", "slot", "schedule", "available", "when", "want to see", "view"],
            "hi": ["book", "visit", "slot", "time", "kab", "dekhna", "aa sakta"],
        },
        Intent.APPOINTMENT_CANCEL: {
            "en": ["cancel", "delete", "remove", "visit", "cancel visit"],
            "hi": ["cancel", "booking", "nahi", "cancel karna"],
        },
        Intent.APPOINTMENT_RESCHEDULE: {
            "en": ["reschedule", "change", "different time", "move", "shift", "visit"],
            "hi": ["reschedule", "badalna", "dusra time", "change"],
        },
        Intent.EMERGENCY: {
            "en": ["urgent", "immediate", "asap", "today", "now"],
            "hi": ["urgent", "jaldi", "aaj hi", "abhi"],
        },
        Intent.CLINIC_INFO: {
            "en": ["office", "address", "location", "directions", "where", "vk realty", "vk", "consultant"],
            "hi": ["office", "address", "kahan", "vk realty"],
        },
        Intent.HOURS_QUERY: {
            "en": ["hours", "timing", "open", "closed", "when open", "working hours"],
            "hi": ["timing", "khule", "kab khulta", "time"],
        },
        Intent.ESCALATION_REQUEST: {
            "en": ["talk to", "human", "representative", "manager", "speak to", "call back", "call me"],
            "hi": ["representative", "manager", "kisi se baat", "callback"],
        },
    }
    
    def __init__(self):
        """Initialize intent router."""
        self.logger = logger
    
    def classify(
        self,
        text: str,
        language: str = "en"
    ) -> ClassificationResult:
        """
        Classify user intent from text.
        
        Args:
            text: User input text
            language: Language code (en, hi, etc.)
            
        Returns:
            Classification result with intent and confidence
        """
        if not text or len(text.strip()) < 2:
            return ClassificationResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                keywords_matched=[],
                explanation="Input too short"
            )
        
        text_lower = text.lower()
        
        # Check for emergency first (high priority)
        if self._check_emergency(text_lower):
            return ClassificationResult(
                intent=Intent.EMERGENCY,
                confidence=0.95,
                keywords_matched=["emergency", "urgent"],
                explanation="Emergency detected"
            )
        
        # Score each intent
        scores: Dict[Intent, Tuple[float, List[str]]] = {}
        
        for intent, keywords_by_lang in self.INTENT_KEYWORDS.items():
            lang_keywords = keywords_by_lang.get(language, keywords_by_lang.get("en", []))
            
            matched = []
            for keyword in lang_keywords:
                if keyword in text_lower:
                    matched.append(keyword)
            
            if matched:
                # Confidence based on number of matches and match quality
                confidence = min(len(matched) / len(lang_keywords), 1.0) * 0.9 + 0.1
                scores[intent] = (confidence, matched)
        
        # If no matches, return unknown
        if not scores:
            return ClassificationResult(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                keywords_matched=[],
                explanation="No intent keywords matched"
            )
        
        # Return highest-scoring intent
        best_intent, (best_confidence, best_keywords) = max(
            scores.items(),
            key=lambda x: x[1][0]
        )
        
        return ClassificationResult(
            intent=best_intent,
            confidence=best_confidence,
            keywords_matched=best_keywords,
            explanation=f"Matched keywords: {', '.join(best_keywords)}"
        )
    
    @staticmethod
    def _check_emergency(text_lower: str) -> bool:
        """Check if text contains emergency indicators."""
        emergency_phrases = [
            "urgent",
            "asap",
            "immediately",
            "right away",
            "today itself",
            "now only",
            "this week",
        ]
        return any(phrase in text_lower for phrase in emergency_phrases)
    
    def is_deterministic_workflow(self, intent: Intent) -> bool:
        """
        Check if this intent requires a deterministic workflow.
        
        If True, we should not use free-form LLM.
        If False, use retrieval-based knowledge.
        """
        deterministic_intents = {
            Intent.APPOINTMENT_BOOKING,
            Intent.APPOINTMENT_CANCEL,
            Intent.APPOINTMENT_RESCHEDULE,
            Intent.APPOINTMENT_CHECK,
            Intent.EMERGENCY,
        }
        return intent in deterministic_intents
    
    def should_use_retrieval(self, intent: Intent) -> bool:
        """Check if this intent should use knowledge retrieval."""
        retrieval_intents = {
            Intent.PRICING_QUERY,
            Intent.SERVICE_INFO,
            Intent.CLINIC_INFO,
            Intent.FAQ,
            Intent.POLICY_QUERY,
            Intent.LOCATION_QUERY,
            Intent.HOURS_QUERY,
            Intent.UNKNOWN,  # Fall back to retrieval
        }
        return intent in retrieval_intents
    
    def get_intent_metadata(self, intent: Intent) -> Dict[str, Any]:
        """Get metadata about an intent."""
        return {
            "intent": intent.value,
            "is_deterministic": self.is_deterministic_workflow(intent),
            "should_retrieve": self.should_use_retrieval(intent),
            "priority": self._get_intent_priority(intent),
            "description": self._get_intent_description(intent),
        }
    
    @staticmethod
    def _get_intent_priority(intent: Intent) -> int:
        """Get priority level for intent (higher = more urgent)."""
        priorities = {
            Intent.EMERGENCY: 100,
            Intent.ESCALATION_REQUEST: 80,
            Intent.APPOINTMENT_BOOKING: 70,
            Intent.APPOINTMENT_CANCEL: 60,
            Intent.APPOINTMENT_RESCHEDULE: 60,
            Intent.PRICING_QUERY: 40,
            Intent.SERVICE_INFO: 40,
            Intent.CLINIC_INFO: 30,
            Intent.HOURS_QUERY: 30,
            Intent.FAQ: 30,
            Intent.UNKNOWN: 10,
            Intent.GREETING: 10,
        }
        return priorities.get(intent, 0)
    
    @staticmethod
    def _get_intent_description(intent: Intent) -> str:
        """Get human-readable description."""
        descriptions = {
            Intent.PRICING_QUERY: "Caller asking about pricing",
            Intent.SERVICE_INFO: "Caller asking about services/treatments",
            Intent.APPOINTMENT_BOOKING: "Caller wants to book appointment",
            Intent.APPOINTMENT_CANCEL: "Caller wants to cancel appointment",
            Intent.APPOINTMENT_RESCHEDULE: "Caller wants to reschedule",
            Intent.EMERGENCY: "Emergency situation detected",
            Intent.CLINIC_INFO: "Caller asking about office info",
            Intent.HOURS_QUERY: "Caller asking about hours/timings",
            Intent.ESCALATION_REQUEST: "Caller requests escalation to human",
            Intent.UNKNOWN: "Intent unclear",
        }
        return descriptions.get(intent, "Unknown intent")


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_router: IntentRouter = IntentRouter()


def get_router() -> IntentRouter:
    """Get global intent router."""
    return _router


def classify_intent(
    text: str,
    language: str = "en"
) -> ClassificationResult:
    """Classify intent (convenience function)."""
    return _router.classify(text, language)


def is_deterministic_workflow(intent: Intent) -> bool:
    """Check if intent is deterministic workflow."""
    return _router.is_deterministic_workflow(intent)


def should_use_retrieval(intent: Intent) -> bool:
    """Check if intent should use retrieval."""
    return _router.should_use_retrieval(intent)
