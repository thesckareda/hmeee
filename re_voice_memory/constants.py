"""
Hermes RE Voice OS — Constants and configuration.
"""
from pathlib import Path
import os

MEMORY_DIR = Path(os.environ.get("RE_MEMORY_DIR", Path.home() / ".hermes" / "re-voice-memory"))
KV_PREFIX = "re_voice"
LEAD_MEMORY_TTL_DAYS = 180
PROFILE_TTL_DAYS = 365
LISTING_CACHE_TTL_SECONDS = 3600
INTENT_SCORE_MAX = 10
TOKEN_BUDGET_SYSTEM = 900
TOKEN_BUDGET_RE_SLOT = 300
TOKEN_BUDGET_COMPANY_SLOT = 200
TOKEN_BUDGET_PROPERTY_SLOT = 120
HOT_INTENT_SCORE_THRESHOLD = 7.0
MAX_INJECTED_MEMORIES = 5

# Token counting strategy (zero-API local estimator via token_counter.py)
TOKEN_COUNTER_CACHE_SIZE = 8192

# Default per-language multipliers (chars → tokens); runtime-updated via ingest_calibration
TOKEN_MULT_HINGLISH = 0.28
TOKEN_MULT_MARATHI = 0.26
TOKEN_MULT_TELUGISH = 0.27
TOKEN_MULT_ENGLISH = 0.25
TOKEN_MULT_MIXED = 0.27
