"""
Hybrid retrieval engine for Second Brain knowledge system.

Combines semantic search (vector DB) with keyword/metadata filtering.
"""

import logging
import asyncio
import os
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import json

logger = logging.getLogger("retrieval-engine")

# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class RetrievalResult:
    """Result from retrieval engine."""
    
    chunks: List[Dict[str, Any]]          # Retrieved chunks
    query: str                             # Original query
    total_chunks: int                      # Number of chunks retrieved
    retrieval_time_ms: float              # Time taken
    success: bool                          # Whether retrieval succeeded
    error_message: str = ""                # Error if any


# ──────────────────────────────────────────────────────────────────────────────
# Retrieval Engine
# ──────────────────────────────────────────────────────────────────────────────


class RetrievalEngine:
    """
    Hybrid retrieval engine for company knowledge.
    
    Features:
    - Semantic search (Qdrant)
    - Keyword search (BM25/full-text)
    - Metadata filtering
    - Result deduplication
    - Caching
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        Initialize retrieval engine.
        
        Args:
            config: Configuration dict with:
                - qdrant_url: Qdrant server URL
                - qdrant_api_key: API key (if needed)
                - top_k: Number of top results (default 5)
                - similarity_threshold: Min similarity (default 0.7)
                - cache_enabled: Enable response caching (default True)
        """
        self.config = config or {}
        self.logger = logger
        
        # Configuration
        self.top_k = self.config.get("top_k", 5)
        self.similarity_threshold = self.config.get("similarity_threshold", 0.70)
        self.cache_enabled = self.config.get("cache_enabled", True)
        
        # State
        self.qdrant_client = None
        self.collection_name = self.config.get(
            "collection_name",
            os.getenv("QDRANT_COLLECTION", "company_knowledge"),
        )
        self._embedding_model = None
        self._cache: Dict[str, RetrievalResult] = {}
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize retrieval engine and connect to vector DB."""
        if self._initialized:
            return
        
        try:
            self.logger.info("[RETRIEVAL] Initializing retrieval engine...")
            qdrant_url = (
                os.getenv("QDRANT_URL", "").strip()
                or str(self.config.get("qdrant_url") or "").strip()
            )
            if qdrant_url:
                try:
                    from qdrant_client import QdrantClient

                    api_key = os.getenv("QDRANT_API_KEY", "") or self.config.get("qdrant_api_key")
                    self.qdrant_client = QdrantClient(
                        url=qdrant_url,
                        api_key=api_key if api_key else None,
                        timeout=10,
                    )
                    self.qdrant_client.get_collections()
                    self.logger.info("[RETRIEVAL] Qdrant connected at %s", qdrant_url)
                except ImportError:
                    self.logger.warning("[RETRIEVAL] qdrant-client not installed")
                except Exception as exc:
                    self.logger.warning("[RETRIEVAL] Qdrant not available: %s", exc)
            
            self._initialized = True
            self.logger.info("[RETRIEVAL] Initialized successfully")
        except Exception as e:
            self.logger.error(f"[RETRIEVAL] Initialization failed: {e}")
            raise
    
    async def retrieve(
        self,
        query: str,
        top_k: int = None,
        metadata_filters: Dict[str, Any] = None,
        language: str = "en"
    ) -> RetrievalResult:
        """
        Retrieve relevant chunks for a query.
        
        Args:
            query: User query
            top_k: Number of results to retrieve
            metadata_filters: Optional metadata filters
            language: Query language
            
        Returns:
            RetrievalResult with chunks and metadata
        """
        import time
        start_time = time.time()
        top_k = top_k or self.top_k

        if not self._initialized:
            await self.initialize()
        
        try:
            # Check cache
            cache_key = f"{query}#{top_k}#{json.dumps(metadata_filters or {})}"
            if self.cache_enabled and cache_key in self._cache:
                self.logger.debug(f"[RETRIEVAL] Cache hit for: {query[:50]}")
                return self._cache[cache_key]
            
            # For now, return empty results (will integrate Qdrant later)
            # TODO: Implement actual semantic search
            chunks = await self._semantic_search(query, top_k, language)
            
            # Filter by metadata
            if metadata_filters:
                chunks = self._apply_metadata_filters(chunks, metadata_filters)
            
            # Deduplicate
            chunks = self._deduplicate_chunks(chunks)
            
            # Trim to top_k
            chunks = chunks[:top_k]
            
            # Build result
            elapsed_ms = (time.time() - start_time) * 1000
            result = RetrievalResult(
                chunks=chunks,
                query=query,
                total_chunks=len(chunks),
                retrieval_time_ms=elapsed_ms,
                success=True
            )
            
            # Cache
            if self.cache_enabled:
                self._cache[cache_key] = result
            
            self.logger.debug(f"[RETRIEVAL] Retrieved {len(chunks)} chunks in {elapsed_ms:.0f}ms")
            return result
            
        except Exception as e:
            self.logger.error(f"[RETRIEVAL] Retrieval failed: {e}")
            return RetrievalResult(
                chunks=[],
                query=query,
                total_chunks=0,
                retrieval_time_ms=(time.time() - start_time) * 1000,
                success=False,
                error_message=str(e)
            )
    
    async def _embed_query(self, query: str) -> Optional[List[float]]:
        try:
            from knowledge_system.embeddings import EmbeddingModel

            if self._embedding_model is None:
                model_name = self.config.get("embeddings_model", "all-MiniLM-L6-v2")
                self._embedding_model = EmbeddingModel(model_name=model_name)
            await self._embedding_model.load()
            return await self._embedding_model.embed_text_async(query)
        except Exception as exc:
            self.logger.debug("[RETRIEVAL] Embedding unavailable: %s", exc)
            return None

    async def _semantic_search(
        self,
        query: str,
        top_k: int,
        language: str
    ) -> List[Dict[str, Any]]:
        """Perform semantic search in Qdrant when configured."""
        if self.qdrant_client is None:
            if not self._initialized:
                await self.initialize()
            if self.qdrant_client is None:
                return []

        vector = await self._embed_query(query)
        if not vector:
            return []

        try:
            hits = self.qdrant_client.search(
                collection_name=self.collection_name,
                query_vector=vector,
                limit=top_k,
            )
        except Exception as exc:
            self.logger.debug("[RETRIEVAL] Qdrant search failed: %s", exc)
            return []

        chunks: List[Dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            chunks.append({
                "chunk_id": payload.get("chunk_id") or payload.get("doc_id"),
                "doc_id": payload.get("doc_id"),
                "text": payload.get("text") or payload.get("content") or "",
                "score": float(hit.score or 0.0),
                "metadata": {
                    k: v for k, v in payload.items()
                    if k not in ("text", "content", "chunk_id", "doc_id")
                },
            })
        return chunks
    
    @staticmethod
    def _apply_metadata_filters(
        chunks: List[Dict[str, Any]],
        filters: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Apply metadata filters to chunks."""
        filtered = []
        for chunk in chunks:
            metadata = chunk.get("metadata", {})
            match = True
            for key, value in filters.items():
                if metadata.get(key) != value:
                    match = False
                    break
            if match:
                filtered.append(chunk)
        return filtered
    
    @staticmethod
    def _deduplicate_chunks(
        chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Remove duplicate chunks."""
        seen = set()
        unique = []
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if chunk_id and chunk_id not in seen:
                seen.add(chunk_id)
                unique.append(chunk)
        return unique
    
    def clear_cache(self) -> None:
        """Clear the result cache."""
        self._cache.clear()
        self.logger.info("[RETRIEVAL] Cache cleared")


# ──────────────────────────────────────────────────────────────────────────────
# Global Instance
# ──────────────────────────────────────────────────────────────────────────────

_retrieval_engine: Optional[RetrievalEngine] = None


async def initialize_retrieval_engine(config: Dict[str, Any] = None) -> RetrievalEngine:
    """Initialize the global retrieval engine."""
    global _retrieval_engine
    _retrieval_engine = RetrievalEngine(config)
    await _retrieval_engine.initialize()
    return _retrieval_engine


def get_retrieval_engine() -> RetrievalEngine:
    """Get global retrieval engine."""
    global _retrieval_engine
    if _retrieval_engine is None:
        _retrieval_engine = RetrievalEngine()
    return _retrieval_engine


# ──────────────────────────────────────────────────────────────────────────────
# Convenience Functions
# ──────────────────────────────────────────────────────────────────────────────


async def retrieve(
    query: str,
    top_k: int = 5,
    metadata_filters: Dict[str, Any] = None,
    language: str = "en"
) -> RetrievalResult:
    """Retrieve chunks for a query."""
    engine = get_retrieval_engine()
    return await engine.retrieve(query, top_k, metadata_filters, language)
