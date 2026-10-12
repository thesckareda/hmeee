"""
EMBEDDINGS — DISABLED

Disabled to avoid loading sentence-transformers (all-MiniLM-L6-v2) and the
associated transformer dependency.

To re-enable:
  1. Uncomment this file
  2. Restore sentence-transformers in requirements.txt
  3. Restore the import in knowledge_system/__init__.py

---

Embedding generation for Second Brain knowledge system.

Provides unified interface for generating embeddings using sentence-transformers.
Optimized for semantic search and low latency.
"""

import logging
import asyncio
from typing import List, Optional

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    raise ImportError(
        "sentence-transformers not installed. Install with: pip install sentence-transformers"
    )

logger = logging.getLogger("embeddings")

# ──────────────────────────────────────────────────────────────────────────────
# Model Management
# ──────────────────────────────────────────────────────────────────────────────


class EmbeddingModel:
    """
    Wrapper for sentence-transformers embedding model.
    
    Uses efficient pooling and batch processing for production use.
    """
    
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        """
        Initialize embedding model.
        
        Args:
            model_name: HuggingFace model name
                - "all-MiniLM-L6-v2" (Recommended) - Fast, good quality (384-dim)
                - "all-mpnet-base-v2" - Better quality but slower (768-dim)
        """
        self.model_name = model_name
        self.model: Optional[SentenceTransformer] = None
        self._loading = False
    
    async def load(self) -> None:
        """Lazy-load model on first use."""
        if self.model is not None or self._loading:
            return
        
        self._loading = True
        try:
            logger.info(f"[EMBEDDINGS] Loading model: {self.model_name}")
            loop = asyncio.get_event_loop()
            self.model = await loop.run_in_executor(
                None,
                lambda: SentenceTransformer(self.model_name)
            )
            logger.info(f"[EMBEDDINGS] Model loaded. Dimension: {self.model.get_sentence_embedding_dimension()}")
        except Exception as e:
            logger.error(f"[EMBEDDINGS] Failed to load model: {e}")
            self._loading = False
            raise
        finally:
            self._loading = False
    
    def get_dimension(self) -> int:
        """Get embedding dimension."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        return self.model.get_sentence_embedding_dimension()
    
    def embed_text(self, text: str) -> List[float]:
        """Embed single text."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        embedding = self.model.encode(text, convert_to_tensor=False)
        return embedding.tolist()
    
    def embed_batch(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Embed multiple texts efficiently."""
        if self.model is None:
            raise RuntimeError("Model not loaded. Call load() first.")
        if not texts:
            return []
        embeddings = self.model.encode(texts, batch_size=batch_size, convert_to_tensor=False)
        return embeddings.tolist()
    
    async def embed_text_async(self, text: str) -> List[float]:
        """Asynchronously embed single text."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_text, text)
    
    async def embed_batch_async(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """Asynchronously embed multiple texts."""
        if not texts:
            return []
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_batch, texts, batch_size)


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_embedding_model: Optional[EmbeddingModel] = None


def get_embedding_model() -> EmbeddingModel:
    """Get or create global embedding model."""
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel()
    return _embedding_model


async def initialize_embeddings(model_name: str = "all-MiniLM-L6-v2") -> EmbeddingModel:
    """Initialize the global embedding model."""
    global _embedding_model
    _embedding_model = EmbeddingModel(model_name)
    await _embedding_model.load()
    return _embedding_model


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ──────────────────────────────────────────────────────────────────────────────


def embed_text(text: str) -> List[float]:
    """Embed a single text."""
    model = get_embedding_model()
    if model.model is None:
        raise RuntimeError("Embeddings not initialized. Call initialize_embeddings() during startup.")
    return model.embed_text(text)


def embed_batch(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """Embed multiple texts."""
    model = get_embedding_model()
    if model.model is None:
        raise RuntimeError("Embeddings not initialized.")
    return model.embed_batch(texts, batch_size)


async def embed_text_async(text: str) -> List[float]:
    """Asynchronously embed text."""
    model = get_embedding_model()
    await model.load()
    return await model.embed_text_async(text)


async def embed_batch_async(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """Asynchronously embed multiple texts."""
    model = get_embedding_model()
    await model.load()
    return await model.embed_batch_async(texts, batch_size)


def get_embedding_dimension() -> int:
    """Get embedding vector dimension."""
    model = get_embedding_model()
    if model.model is None:
        raise RuntimeError("Embeddings not initialized.")
    return model.get_dimension()
