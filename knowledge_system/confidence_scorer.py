"""
Confidence scoring for Second Brain retrieval results.

Determines how confident we are in retrieved information.
Scores: 0.0 (no confidence) → 1.0 (high confidence)
"""

import logging
from typing import List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger("confidence-scorer")

# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ScoringFactors:
    """Factors that contribute to confidence score."""
    
    # Similarity score from vector DB (0-1)
    semantic_similarity: float
    
    # Number of matching keywords
    keyword_matches: int
    
    # Whether document is from "official" source
    is_official_source: bool
    
    # Recency (how recently updated, 0-1, 1=fresh)
    recency_score: float
    
    # Number of supporting chunks (more = higher confidence)
    supporting_chunks: int
    
    # Metadata confidence score (if provided)
    metadata_confidence: float = 1.0


class ConfidenceScorer:
    """
    Compute confidence scores for retrieved information.
    
    Factors considered:
    - Semantic similarity (primary)
    - Keyword matching
    - Document authority
    - Recency
    - Supporting evidence count
    """
    
    # Confidence thresholds for decision-making
    HIGH_CONFIDENCE = 0.85      # Safe to answer directly
    MEDIUM_CONFIDENCE = 0.70    # Answer with caution
    LOW_CONFIDENCE = 0.50       # Consider escalating
    REFUSE_CONFIDENCE = 0.40    # Do not answer
    
    def __init__(self):
        """Initialize confidence scorer."""
        self.logger = logger
    
    def score_retrieval(
        self,
        factors: ScoringFactors,
        num_chunks: int = 1,
        max_chunks: int = 5
    ) -> float:
        """
        Compute confidence score for retrieved results.
        
        Args:
            factors: Scoring factors
            num_chunks: Number of relevant chunks retrieved
            max_chunks: Max chunks to consider
            
        Returns:
            Confidence score (0.0 - 1.0)
        """
        # Base score from semantic similarity
        base_score = factors.semantic_similarity * 0.5
        
        # Official source multiplier
        official_multiplier = 1.1 if factors.is_official_source else 0.9
        
        # Keyword matching bonus (up to 0.15)
        keyword_bonus = min(factors.keyword_matches * 0.05, 0.15)
        
        # Recency bonus (up to 0.10)
        recency_bonus = factors.recency_score * 0.10
        
        # Supporting chunks multiplier
        chunk_multiplier = min(num_chunks / max_chunks, 1.0)
        
        # Combine factors
        score = (base_score + keyword_bonus + recency_bonus) * official_multiplier
        score *= (0.7 + chunk_multiplier * 0.3)  # Chunk count gives 0.7-1.0 multiplier
        
        # Apply metadata confidence cap
        score *= factors.metadata_confidence
        
        # Clamp to [0, 1]
        return max(0.0, min(score, 1.0))
    
    def score_chunks(self, chunks: List[Dict[str, Any]]) -> float:
        """
        Score a list of retrieved chunks.
        
        Higher: more chunks with similar meaning = higher confidence.
        
        Args:
            chunks: List of retrieved chunks with similarity scores
            
        Returns:
            Confidence score
        """
        if not chunks:
            return 0.0
        
        # Average similarity of top chunks
        similarities = [c.get("similarity", 0.0) for c in chunks[:5]]
        avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
        
        # More chunks supporting the same topic = higher confidence
        num_supporting = len([c for c in chunks if c.get("similarity", 0.0) > 0.7])
        support_bonus = min(num_supporting * 0.05, 0.20)
        
        score = avg_similarity + support_bonus
        return min(score, 1.0)
    
    def decision(self, confidence: float) -> str:
        """
        Make a decision based on confidence score.
        
        Args:
            confidence: Confidence score (0-1)
            
        Returns:
            Decision: "answer", "cautious_answer", "escalate", or "refuse"
        """
        if confidence >= self.HIGH_CONFIDENCE:
            return "answer"
        elif confidence >= self.MEDIUM_CONFIDENCE:
            return "cautious_answer"
        elif confidence >= self.LOW_CONFIDENCE:
            return "escalate"
        else:
            return "refuse"
    
    def get_decision_details(self, confidence: float) -> Dict[str, Any]:
        """
        Get detailed decision information.
        
        Args:
            confidence: Confidence score
            
        Returns:
            Decision details
        """
        decision = self.decision(confidence)
        
        details = {
            "confidence": round(confidence, 3),
            "decision": decision,
            "should_answer": decision in ("answer", "cautious_answer"),
            "should_escalate": decision in ("escalate", "refuse"),
            "explanation": self._get_explanation(confidence, decision)
        }
        
        return details
    
    @staticmethod
    def _get_explanation(confidence: float, decision: str) -> str:
        """Get human-readable explanation."""
        explanations = {
            "answer": "High confidence - answer directly from retrieved knowledge",
            "cautious_answer": "Medium confidence - answer with caution or qualifier",
            "escalate": "Low confidence - consider escalating to human",
            "refuse": "Very low confidence - refuse and escalate to human"
        }
        return explanations.get(decision, "Unknown")


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_scorer: ConfidenceScorer = ConfidenceScorer()


def get_scorer() -> ConfidenceScorer:
    """Get global confidence scorer."""
    return _scorer


def score_confidence(
    factors: ScoringFactors,
    num_chunks: int = 1,
    max_chunks: int = 5
) -> float:
    """Score confidence (convenience function)."""
    return _scorer.score_retrieval(factors, num_chunks, max_chunks)


def score_chunks(chunks: List[Dict[str, Any]]) -> float:
    """Score list of chunks (convenience function)."""
    return _scorer.score_chunks(chunks)


def make_confidence_decision(confidence: float) -> str:
    """Make decision based on confidence (convenience function)."""
    return _scorer.decision(confidence)


def get_confidence_details(confidence: float) -> Dict[str, Any]:
    """Get detailed confidence decision."""
    return _scorer.get_decision_details(confidence)


# ──────────────────────────────────────────────────────────────────────────────
# Confidence Thresholds (exported for config)
# ──────────────────────────────────────────────────────────────────────────────

HIGH_CONFIDENCE = ConfidenceScorer.HIGH_CONFIDENCE
MEDIUM_CONFIDENCE = ConfidenceScorer.MEDIUM_CONFIDENCE
LOW_CONFIDENCE = ConfidenceScorer.LOW_CONFIDENCE
REFUSE_CONFIDENCE = ConfidenceScorer.REFUSE_CONFIDENCE
