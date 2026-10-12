"""
INTENT CLASSIFIER — DISABLED

Disabled to avoid loading sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
and the associated transformer dependency. Intent classification is handled by the
keyword-only IntentRouter in knowledge_system/intent_router.py instead.

To re-enable:
  1. Uncomment this file
  2. Restore sentence-transformers in requirements.txt
  3. Restore the import in agent.py (line ~1671) and worker_prewarm.py

---

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("inbound-voice-agent")

INTENT_CLASSES = [
    "check_availability",
    "price_query",
    "booking_request",
    "property_details",
    "area_query",
    "general_question",
    "objection",
    "offer_intent",
]

TOOL_INTENT_MAP = {
    "check_availability": "check_calendar_slots",
    "price_query": "check_price_history",
    "booking_request": "schedule_viewing",
    "property_details": "get_listing_details",
    "area_query": "search_listings_by_area",
    "offer_intent": "capture_offer_intent",
}

KEYWORD_PATTERNS = {
    "price_query": [
        "price", "rate", "kitna", "cost", "charges", "fees", "rupees",
        "lakh", "cr", "\\u20b9", "rupee",
    ],
    "booking_request": [
        "book", "schedule", "viewing", "visit", "appointment",
        "slot", "fix", "confirm",
    ],
    "check_availability": [
        "available", "free", "open", "kab", "when", "time", "date",
    ],
    "offer_intent": [
        "offer", "negotiate", "deal", "finalise", "finalize",
        "buy", "purchase",
    ],
    "objection": [
        "expensive", "costly", "budget", "far", "small", "later",
        "think", "sochna",
    ],
}

_INTENT_PROTOTYPE_PHRASES = {
    "check_availability": "is there an available slot for this property",
    "price_query": "what is the price of this property",
    "booking_request": "I want to schedule a property viewing",
    "property_details": "tell me about the features of this property",
    "area_query": "what properties are available in this location",
    "offer_intent": "I want to make an offer on this property",
    "objection": "I think the price is too high for this property",
    "general_question": "I have a general question about real estate",
}


class IntentClassifier:
    def __init__(self, model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"):
        self.model_name = model_name
        self._model = None
        self._intent_embeddings = {}

    async def load(self):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning(
                "sentence_transformers not installed, running in keyword-only mode"
            )
            return

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as pool:
            self._model = await loop.run_in_executor(
                pool, lambda: SentenceTransformer(self.model_name)
            )

        phrases = list(_INTENT_PROTOTYPE_PHRASES.values())
        with ThreadPoolExecutor(max_workers=1) as pool:
            embeddings = await loop.run_in_executor(
                pool, lambda: self._model.encode(phrases)
            )

        self._intent_embeddings = {
            intent: embeddings[i]
            for i, intent in enumerate(_INTENT_PROTOTYPE_PHRASES.keys())
        }

    def classify(self, text, confidence=1.0):
        if text is None or len(text.split()) < 4:
            return None
        if confidence < 0.85:
            return None

        lowered = text.lower()

        for intent, keywords in KEYWORD_PATTERNS.items():
            for kw in keywords:
                if kw in lowered:
                    return intent

        if self._model is not None and self._intent_embeddings:
            from sklearn.metrics.pairwise import cosine_similarity

            query_embedding = self._model.encode([text])
            best_score = -1.0
            best_intent = "general_question"

            intent_keys = list(self._intent_embeddings.keys())
            for intent in intent_keys:
                sim = cosine_similarity(
                    query_embedding, [self._intent_embeddings[intent]]
                )[0][0]
                if sim > best_score:
                    best_score = sim
                    best_intent = intent

            if best_score > 0.65:
                return best_intent

        return "general_question"

    def get_tool_for_intent(self, intent):
        return TOOL_INTENT_MAP.get(intent)


_classifier = IntentClassifier()


async def load_classifier():
    await _classifier.load()


def classify_intent(text, confidence=1.0):
    return _classifier.classify(text, confidence)


def get_tool_for_intent(intent):
    return _classifier.get_tool_for_intent(intent)
"""
