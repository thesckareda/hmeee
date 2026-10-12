"""
Response formatting for Second Brain answers.

Converts LLM outputs and retrieval results into safe, concise, voice-friendly responses.
"""

import logging
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger("response-formatter")

# ──────────────────────────────────────────────────────────────────────────────
# Response Types
# ──────────────────────────────────────────────────────────────────────────────


class ResponseType(str, Enum):
    """Type of response to generate."""
    
    CONFIDENT = "confident"           # High confidence - direct answer
    CAUTIOUS = "cautious"             # Medium confidence - qualified answer
    ESCALATE = "escalate"             # Low confidence - escalate to human
    REFUSE = "refuse"                 # Cannot answer
    MEDICAL_ESCALATE = "medical_escalate"  # Defer to doctor
    EMERGENCY = "emergency"           # Emergency response


@dataclass
class FormattedResponse:
    """Formatted response ready for TTS."""
    
    response_type: ResponseType
    text: str
    is_voice_safe: bool
    confidence: float
    sources: List[str]
    requires_followup: bool = False
    followup_action: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Response Templates
# ──────────────────────────────────────────────────────────────────────────────

RESPONSE_TEMPLATES = {
    ResponseType.CONFIDENT: {
        "header": "",  # No header needed for confident responses
        "footer": "Is there anything else I can help with?",
        "source_note": "",  # No source note for confident answers
    },
    ResponseType.CAUTIOUS: {
        "header": "I believe",
        "footer": "However, let me have our team confirm this for you.",
        "source_note": "",
    },
    ResponseType.ESCALATE: {
        "header": "I want to get you the most accurate information.",
        "footer": "Let me have our team call you back with details.",
        "source_note": "",
    },
    ResponseType.REFUSE: {
        "header": "I don't have verified information about that.",
        "footer": "Our team can help. May I have them reach out?",
        "source_note": "",
    },
    ResponseType.MEDICAL_ESCALATE: {
        "header": "This is something our doctor should discuss with you directly.",
        "footer": "Let me arrange a callback from our medical team.",
        "source_note": "",
    },
    ResponseType.EMERGENCY: {
        "header": "This sounds like an emergency.",
        "footer": "Please describe your exact location. We're here to help.",
        "source_note": "",
    },
}

# Call-to-action templates
CTA_TEMPLATES = {
    "booking": "Would you like to book an appointment?",
    "info_request": "Would you like to know anything else about our services?",
    "callback": "Can I have our team call you back?",
    "escalation": "Would you like me to connect you with someone on our team?",
}

# ──────────────────────────────────────────────────────────────────────────────
# Response Formatter
# ──────────────────────────────────────────────────────────────────────────────


class ResponseFormatter:
    """
    Format responses for voice assistant.
    
    Ensures responses are:
    - Concise (< 150 words for voice)
    - Clear and natural
    - Grounded in retrieved information
    - Safe and guardrail-compliant
    """
    
    # Maximum words for voice response
    MAX_WORDS = 120
    
    def __init__(self):
        """Initialize formatter."""
        self.logger = logger
    
    def format(
        self,
        response_type: ResponseType,
        content: str,
        confidence: float = 1.0,
        sources: List[str] = None,
        add_cta: str = None
    ) -> FormattedResponse:
        """
        Format a response for voice delivery.
        
        Args:
            response_type: Type of response
            content: Main response content
            confidence: Confidence score
            sources: List of source documents
            add_cta: Call-to-action type (booking, info_request, etc.)
            
        Returns:
            FormattedResponse ready for TTS
        """
        sources = sources or []
        
        # Get template
        template = RESPONSE_TEMPLATES.get(response_type, {})
        
        # Build response
        parts = []
        
        if template.get("header"):
            parts.append(template["header"])
        
        parts.append(content)
        
        if template.get("footer"):
            parts.append(template["footer"])
        
        if add_cta and add_cta in CTA_TEMPLATES:
            parts.append(CTA_TEMPLATES[add_cta])
        
        # Join and clean
        full_response = " ".join(parts)
        full_response = self._clean_for_speech(full_response)
        
        # Truncate if too long
        if len(full_response.split()) > self.MAX_WORDS:
            full_response = self._truncate_intelligently(full_response, self.MAX_WORDS)
        
        return FormattedResponse(
            response_type=response_type,
            text=full_response,
            is_voice_safe=self._is_voice_safe(full_response),
            confidence=confidence,
            sources=sources,
            requires_followup=response_type in (
                ResponseType.ESCALATE,
                ResponseType.MEDICAL_ESCALATE,
                ResponseType.EMERGENCY
            ),
            followup_action=self._get_followup_action(response_type)
        )
    
    def format_confident_answer(
        self,
        answer: str,
        sources: List[str] = None,
        add_cta: str = None
    ) -> FormattedResponse:
        """Format a high-confidence answer."""
        return self.format(
            ResponseType.CONFIDENT,
            answer,
            confidence=0.95,
            sources=sources,
            add_cta=add_cta
        )
    
    def format_cautious_answer(
        self,
        answer: str,
        sources: List[str] = None
    ) -> FormattedResponse:
        """Format a medium-confidence answer with qualifier."""
        return self.format(
            ResponseType.CAUTIOUS,
            answer,
            confidence=0.75,
            sources=sources
        )
    
    def format_escalation(
        self,
        reason: str = ""
    ) -> FormattedResponse:
        """Format an escalation response."""
        content = reason or "This requires attention from our team."
        return self.format(
            ResponseType.ESCALATE,
            content,
            confidence=0.0
        )
    
    def format_refusal(self) -> FormattedResponse:
        """Format a refusal response."""
        return self.format(
            ResponseType.REFUSE,
            "Let me connect you with our team who can help.",
            confidence=0.0
        )
    
    def format_medical_escalation(self) -> FormattedResponse:
        """Format a medical consultation request."""
        return self.format(
            ResponseType.MEDICAL_ESCALATE,
            "Please hold while I arrange that.",
            confidence=0.0
        )
    
    @staticmethod
    def _clean_for_speech(text: str) -> str:
        """
        Clean text for TTS.
        
        - Remove abbreviations that TTS might mispronounce
        - Expand common abbreviations
        - Remove special characters
        """
        # Expansions
        expansions = {
            "rs.": "rupees",
            "rs": "rupees",
            "dr.": "doctor",
            "dr": "doctor",
            "etc.": "and so on",
            "mr.": "mister",
            "mrs.": "misses",
            "ms.": "miss",
            "rct": "root canal treatment",
            "sms": "message",
            "call": "telephone",
        }
        
        text_lower = text.lower()
        for abbr, expansion in expansions.items():
            text_lower = text_lower.replace(abbr, expansion)
        
        # Remove special characters that might confuse TTS
        text_lower = text_lower.replace("&", "and")
        text_lower = text_lower.replace("@", "at")
        text_lower = text_lower.replace("#", "number")
        
        # Remove multiple spaces
        text_lower = " ".join(text_lower.split())
        
        return text_lower
    
    @staticmethod
    def _truncate_intelligently(text: str, max_words: int) -> str:
        """Truncate text while preserving sentences."""
        words = text.split()
        if len(words) <= max_words:
            return text
        
        # Take up to max_words
        truncated = " ".join(words[:max_words])
        
        # Find last complete sentence
        sentences = truncated.split(". ")
        if len(sentences) > 1:
            # Keep all but last sentence (which might be incomplete)
            truncated = ". ".join(sentences[:-1]) + "."
        
        return truncated
    
    @staticmethod
    def _is_voice_safe(text: str) -> bool:
        """Check if text is safe for voice delivery."""
        # Check for problematic patterns
        unsafe_patterns = [
            ">>>", "<<<", "{{", "}}", "[[", "]]",  # Code-like syntax
            "```",  # Markdown code blocks
            "<script>", "</script>",  # Script tags
            "://",  # URLs (should be converted to natural language)
        ]
        
        text_lower = text.lower()
        for pattern in unsafe_patterns:
            if pattern in text_lower:
                return False
        
        # Check if too long
        if len(text) > 1000:
            return False
        
        return True
    
    @staticmethod
    def _get_followup_action(response_type: ResponseType) -> str:
        """Get followup action for response type."""
        actions = {
            ResponseType.ESCALATE: "escalate_to_human",
            ResponseType.MEDICAL_ESCALATE: "arrange_doctor_callback",
            ResponseType.EMERGENCY: "emergency_response",
            ResponseType.REFUSE: "offer_escalation",
        }
        return actions.get(response_type, "")


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_formatter: ResponseFormatter = ResponseFormatter()


def get_formatter() -> ResponseFormatter:
    """Get global formatter."""
    return _formatter


def format_response(
    response_type: ResponseType,
    content: str,
    confidence: float = 1.0,
    sources: List[str] = None,
    add_cta: str = None
) -> FormattedResponse:
    """Format response (convenience function)."""
    return _formatter.format(response_type, content, confidence, sources, add_cta)


def format_confident_answer(
    answer: str,
    sources: List[str] = None,
    add_cta: str = None
) -> FormattedResponse:
    """Format confident answer."""
    return _formatter.format_confident_answer(answer, sources, add_cta)


def format_cautious_answer(
    answer: str,
    sources: List[str] = None
) -> FormattedResponse:
    """Format cautious answer."""
    return _formatter.format_cautious_answer(answer, sources)


def format_escalation(reason: str = "") -> FormattedResponse:
    """Format escalation."""
    return _formatter.format_escalation(reason)


def format_refusal() -> FormattedResponse:
    """Format refusal."""
    return _formatter.format_refusal()
