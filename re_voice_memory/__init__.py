"""
Hermes RE Voice OS — Real estate voice agent memory system.

Sub-600ms startup · 180-day lead memory · Zero-I/O hot path.
"""
from pathlib import Path
import os

from re_voice_memory.constants import (
    MEMORY_DIR, KV_PREFIX, LEAD_MEMORY_TTL_DAYS, PROFILE_TTL_DAYS,
    LISTING_CACHE_TTL_SECONDS, INTENT_SCORE_MAX, TOKEN_BUDGET_SYSTEM,
    TOKEN_BUDGET_RE_SLOT, MAX_INJECTED_MEMORIES,
)

# ── Ensure memory directory tree exists on import ──
def _ensure_dirs():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    for sub in ["leads", "market", "listings", "calls"]:
        (MEMORY_DIR / sub).mkdir(exist_ok=True)

_ensure_dirs()

# ── Optional 3rd-party imports ──
try:
    import redis.asyncio as _aioredis
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False

_HAS_PROMETHEUS = False
try:
    import prometheus_client
    _HAS_PROMETHEUS = True
except ImportError:
    pass

# ── Exports ──
__all__ = [
    # Constants
    "MEMORY_DIR", "KV_PREFIX",
    "LEAD_MEMORY_TTL_DAYS", "PROFILE_TTL_DAYS",
    "LISTING_CACHE_TTL_SECONDS", "INTENT_SCORE_MAX",
    "TOKEN_BUDGET_SYSTEM", "TOKEN_BUDGET_RE_SLOT",
    "MAX_INJECTED_MEMORIES",
    # Core API
    "inject_lead_context",
    "generate_raw_md",
    "extract_call_memories",
    "score_lead_intent",
    "log_objection",
    "sync_to_crm",
    "drain_context_buffer",
]

# Lazy imports for public API
def inject_lead_context(*args, **kwargs):
    from re_voice_memory.inject import inject_lead_context as _fn
    return _fn(*args, **kwargs)

def generate_raw_md(*args, **kwargs):
    from re_voice_memory.raw_md import generate_raw_md as _fn
    return _fn(*args, **kwargs)

def extract_call_memories(*args, **kwargs):
    from re_voice_memory.extract import extract_call_memories as _fn
    return _fn(*args, **kwargs)

def score_lead_intent(*args, **kwargs):
    from re_voice_memory.intent_score import score_call_intent as _fn
    return _fn(*args, **kwargs)

def log_objection(*args, **kwargs):
    from re_voice_memory.objection_log import append_objection as _fn
    return _fn(*args, **kwargs)

def sync_to_crm(*args, **kwargs):
    from re_voice_memory.crm_sync import push_crm_update as _fn
    return _fn(*args, **kwargs)

def drain_context_buffer(*args, **kwargs):
    """Stub — actual drain happens in AgentTools."""
    return []
