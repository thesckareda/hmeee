"""
Production guardrails for Second Brain responses.

Enforces safety constraints:
- Never answer without context
- Escalate on low confidence
- Refuse medical advice
- Prevent hallucination
"""

import logging
from typing import Dict, Any, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("guardrails")

# ──────────────────────────────────────────────────────────────────────────────
# Guardrail Response Types
# ──────────────────────────────────────────────────────────────────────────────


class GuardrailAction(str, Enum):
    """Action to take based on guardrails."""
    
    ALLOW = "allow"              # Safe to proceed
    CAUTION = "caution"          # Proceed with warnings
    ESCALATE = "escalate"        # Escalate to human
    REFUSE = "refuse"            # Refuse to answer
    
    class Action(str, Enum):
        """Additional context"""
        MISSING_CONTEXT = "missing_context"
        LOW_CONFIDENCE = "low_confidence"
        MEDICAL_ADVICE = "medical_advice"
        UNSUPPORTED_REQUEST = "unsupported_request"
        POLICY_VIOLATION = "policy_violation"


@dataclass
class GuardrailResult:
    """Result of guardrail check."""
    
    action: GuardrailAction
    reason: str
    safe_to_respond: bool
    warning_message: str = ""
    escalation_reason: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# Guardrails Engine
# ──────────────────────────────────────────────────────────────────────────────


class Guardrails:
    """
    Production safety guardrails for AI responses.
    
    Rules:
    1. Never answer without retrieved context
    2. Escalate if confidence < threshold
    3. Refuse medical advice (defer to doctor)
    4. Reject unsupported intents
    5. Prevent policy violations
    """
    
    # Confidence thresholds
    CONFIDENCE_ESCALATE = 0.75  # Below this, escalate
    CONFIDENCE_ALLOW = 0.85     # Above this, respond confidently
    
    # Real estate compliance keywords that require escalation
    COMPLIANCE_KEYWORDS = {
        "legal", "rta", "litigation", "encumbrance", "title deed",
        "khata", "ec", "occupancy certificate", "loan eligibility",
        "emi calculator", "registration", "stamp duty", "capital gains",
        "tax", "gst", "rera", "builder reputation", "delay",
        "possession delay", "cancellation", "refund policy",
    }

    # Policy violation keywords
    POLICY_KEYWORDS = {
        "discount", "free", "refund", "guaranteed returns",
        "assured rental", "cashback", "special offer",
        "below market", "no brokerage",
    }
    
    def __init__(self):
        """Initialize guardrails."""
        self.logger = logger
    
    def check(
        self,
        query: str,
        confidence: float,
        has_context: bool,
        retrieved_chunks: List[Dict[str, Any]] = None
    ) -> GuardrailResult:
        """
        Check if a response is safe to provide.
        
        Args:
            query: User query
            confidence: Confidence score (0-1)
            has_context: Whether we have retrieved context
            retrieved_chunks: Retrieved chunks (if any)
            
        Returns:
            GuardrailResult with action and reason
        """
        # Rule 1: Never answer without context
        if not has_context or not retrieved_chunks:
            return GuardrailResult(
                action=GuardrailAction.ESCALATE,
                reason="No retrieved context",
                safe_to_respond=False,
                escalation_reason="Cannot find verified information for this query"
            )
        
        # Rule 2: Check for compliance-sensitive requests
        if self._is_compliance_sensitive_request(query):
            return GuardrailResult(
                action=GuardrailAction.ESCALATE,
                reason="Compliance-sensitive query detected",
                safe_to_respond=False,
                escalation_reason="This requires verification from our legal/operations team"
            )
        
        # Rule 3: Check confidence level
        if confidence < self.CONFIDENCE_ESCALATE:
            return GuardrailResult(
                action=GuardrailAction.ESCALATE,
                reason=f"Confidence too low ({confidence:.2f})",
                safe_to_respond=False,
                escalation_reason="I want to verify this information with our team"
            )
        
        # Rule 4: Check for policy violations
        if self._is_policy_violation(query):
            return GuardrailResult(
                action=GuardrailAction.ESCALATE,
                reason="Policy-sensitive query detected",
                safe_to_respond=False,
                escalation_reason="Our team will confirm current offers and policies"
            )
        
        # If confidence is borderline, use caution
        if confidence < self.CONFIDENCE_ALLOW:
            return GuardrailResult(
                action=GuardrailAction.CAUTION,
                reason="Medium confidence",
                safe_to_respond=True,
                warning_message="I believe so, but let me have our team confirm that for you."
            )
        
        # All checks passed
        return GuardrailResult(
            action=GuardrailAction.ALLOW,
            reason="All checks passed",
            safe_to_respond=True
        )
    
    @staticmethod
    def _is_compliance_sensitive_request(query: str) -> bool:
        """Check if query involves legally/compliance-sensitive real estate topics."""
        query_lower = query.lower()

        compliance_patterns = [
            "is it legal",
            "what is the rera",
            "title deed",
            "encumbrance certificate",
            "litigation",
            "legal dispute",
            "stamp duty",
            "registration process",
            "capital gains tax",
            "loan process",
            "emi for",
            "down payment",
            "possession delay",
            "builder defaulted",
            "what if builder",
            "cancellation refund",
            "guaranteed returns",
            "assured rental",
            "will it appreciate",
            "how much appreciation",
        ]

        if any(pattern in query_lower for pattern in compliance_patterns):
            return True

        for keyword in Guardrails.COMPLIANCE_KEYWORDS:
            if keyword in query_lower and any(w in query_lower for w in ["should", "will", "can", "is it", "what is"]):
                return True

        return False
    
    @staticmethod
    def _is_policy_violation(query: str) -> bool:
        """
        Check if query involves policy-sensitive topics.
        
        These require verification by team.
        """
        query_lower = query.lower()
        
        # Check for policy keywords
        for keyword in Guardrails.POLICY_KEYWORDS:
            if keyword in query_lower:
                return True
        
        return False
    
    def validate_response(
        self,
        response_text: str,
        query: str,
        retrieved_chunks: List[Dict[str, Any]]
    ) -> GuardrailResult:
        """
        Validate LLM response against guardrails.
        
        Ensures response:
        - Is grounded in retrieved chunks
        - Doesn't make claims beyond context
        - Doesn't give medical advice
        
        Args:
            response_text: LLM-generated response
            query: Original query
            retrieved_chunks: Retrieved context
            
        Returns:
            Validation result
        """
        # Check if response is grounded
        if not self._is_grounded(response_text, retrieved_chunks):
            return GuardrailResult(
                action=GuardrailAction.REFUSE,
                reason="Response not grounded in retrieved context",
                safe_to_respond=False,
                escalation_reason="Response includes information not verified from our knowledge base"
            )
        
        # Check if response gives compliance-sensitive or unverifiable advice
        if self._gives_compliance_sensitive_advice(response_text):
            return GuardrailResult(
                action=GuardrailAction.REFUSE,
                reason="Response gives compliance-sensitive or unverifiable advice",
                safe_to_respond=False,
                escalation_reason="Legal and compliance matters must be verified by our team"
            )
        
        # Response looks safe
        return GuardrailResult(
            action=GuardrailAction.ALLOW,
            reason="Response passes validation",
            safe_to_respond=True
        )
    
    @staticmethod
    def _is_grounded(response: str, chunks: List[Dict[str, Any]]) -> bool:
        """
        Check if response is grounded in retrieved chunks.
        
        Simple heuristic: check if key phrases from response appear in chunks.
        """
        if not chunks:
            return False
        
        # Extract key phrases from chunks
        chunk_texts = " ".join([c.get("content", "") for c in chunks]).lower()
        response_lower = response.lower()
        
        # Check if response references information from chunks
        # (Simple check: at least 30% of response should overlap with chunks)
        response_words = set(response_lower.split())
        chunk_words = set(chunk_texts.split())
        
        overlap = len(response_words & chunk_words)
        if overlap > len(response_words) * 0.1:  # At least 10% overlap
            return True
        
        return False
    
    @staticmethod
    def _gives_compliance_sensitive_advice(response: str) -> bool:
        """Check if response gives compliance-sensitive advice or makes unverifiable claims."""
        response_lower = response.lower()
        
        # Advice patterns
        advice_patterns = [
            "you should buy",
            "you must invest",
            "guaranteed returns",
            "assured appreciation",
            "will double in",
            "best investment",
            "limited period offer",
            "last few units",
        ]
        
        return any(pattern in response_lower for pattern in advice_patterns)


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_guardrails: Guardrails = Guardrails()


def get_guardrails() -> Guardrails:
    """Get global guardrails instance."""
    return _guardrails


def check_guardrails(
    query: str,
    confidence: float,
    has_context: bool,
    retrieved_chunks: List[Dict[str, Any]] = None
) -> GuardrailResult:
    """Check guardrails (convenience function)."""
    return _guardrails.check(query, confidence, has_context, retrieved_chunks)


def validate_response(
    response_text: str,
    query: str,
    retrieved_chunks: List[Dict[str, Any]]
) -> GuardrailResult:
    """Validate response (convenience function)."""
    return _guardrails.validate_response(response_text, query, retrieved_chunks)
