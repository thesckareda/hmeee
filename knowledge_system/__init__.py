"""
Knowledge System (Second Brain) - Unified interface for company knowledge retrieval.

Note: Embeddings and semantic search are DISABLED to avoid loading
sentence-transformers (all-MiniLM-L6-v2). Intent routing, guardrails,
confidence scoring, and response formatting remain active as they use
pure Python logic (no transformers).
"""

__version__ = "1.0.0"

# Embeddings disabled — requires sentence-transformers + all-MiniLM-L6-v2
# from .embeddings import initialize_embeddings, embed_text, embed_batch, embed_text_async, embed_batch_async

from .retrieval_engine import RetrievalEngine, initialize_retrieval_engine, get_retrieval_engine
from .confidence_scorer import ConfidenceScorer, score_confidence
from .intent_router import IntentRouter, classify_intent
from .guardrails import Guardrails, check_guardrails
from .response_formatter import format_response, ResponseType

__all__ = [
    # "initialize_embeddings",
    # "embed_text",
    # "embed_batch",
    # "embed_text_async",
    # "embed_batch_async",
    "RetrievalEngine",
    "initialize_retrieval_engine",
    "get_retrieval_engine",
    "ConfidenceScorer",
    "score_confidence",
    "IntentRouter",
    "classify_intent",
    "Guardrails",
    "check_guardrails",
    "format_response",
    "ResponseType",
]
