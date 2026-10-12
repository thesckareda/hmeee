"""
KV hot store — sub-5ms structured key reads with Redis/SQLite dual backend.

Thread-safe SQLite via per-thread WAL connections. Async-safe Redis via connection caching.
"""
import asyncio
import json
import os
import sqlite3
import time
import threading
from datetime import datetime, timezone
from logging import getLogger
from pathlib import Path
from typing import Any

from re_voice_memory.constants import KV_PREFIX, LISTING_CACHE_TTL_SECONDS, MEMORY_DIR, PROFILE_TTL_DAYS

logger = getLogger(__name__)

_HAS_REDIS = False
_redis: Any = None
_redis_pool: Any = None
_redis_lock: asyncio.Lock | None = None
_redis_lock_guard = threading.Lock()  # guards lazy creation of the asyncio lock
_redis_retry_after = 0.0        # monotonic — cooldown before reconnect attempts
_redis_failures = 0             # consecutive op failures → drop connection
_REDIS_RETRY_COOLDOWN_S = 30.0
_REDIS_MAX_FAILURES = 3

try:
    import redis.asyncio as aioredis
    _HAS_REDIS = True
except ImportError:
    pass


def _get_redis_lock() -> asyncio.Lock:
    global _redis_lock
    if _redis_lock is None:
        with _redis_lock_guard:
            if _redis_lock is None:
                _redis_lock = asyncio.Lock()
    return _redis_lock


def _note_redis_failure() -> None:
    """Circuit breaker: after N consecutive failures, fall back for a cooldown."""
    global _redis, _redis_failures, _redis_retry_after
    _redis_failures += 1
    if _redis_failures >= _REDIS_MAX_FAILURES and _redis is not None:
        _redis = None
        _redis_retry_after = time.monotonic() + _REDIS_RETRY_COOLDOWN_S
        logger.warning(
            "Redis: %d consecutive failures — SQLite fallback for %.0fs",
            _redis_failures, _REDIS_RETRY_COOLDOWN_S,
        )
        try:
            from re_voice_memory.metrics import record_kv_fallback
            record_kv_fallback()
        except Exception:
            pass


def _note_redis_success() -> None:
    global _redis_failures
    _redis_failures = 0

_SQLITE_PATH = MEMORY_DIR / ".kv_store.db"
_sqlite_local = threading.local()

_LEAD_HOT_KEYS = [
    "profile", "intent_score", "deal_stage", "call_count",
    "last_seen", "active_listings", "last_objections",
]

_TTL_365D = PROFILE_TTL_DAYS * 86400
_TTL_90D = 90 * 86400
_TTL_7D = 7 * 86400
_TTL_24H = 86400
_TTL_1H = LISTING_CACHE_TTL_SECONDS


def _full_key(key: str) -> str:
    return f"{KV_PREFIX}:{key}"


async def _ensure_redis() -> Any | None:
    global _redis, _redis_pool, _HAS_REDIS, _redis_retry_after
    if _redis is not None:
        return _redis
    if not _HAS_REDIS or time.monotonic() < _redis_retry_after:
        return None  # cooling down — cheap check, no lock, no connection storm
    async with _get_redis_lock():
        if _redis is not None:
            return _redis
        if time.monotonic() < _redis_retry_after:
            return None
        redis_url = os.environ.get("REDIS_URL", "")
        if not redis_url:
            _HAS_REDIS = False
            return None
        try:
            _redis_pool = aioredis.ConnectionPool.from_url(
                redis_url,
                max_connections=10,
                socket_connect_timeout=0.25,  # fail over to SQLite in 250ms, not never
                socket_timeout=0.25,
                health_check_interval=30,
            )
            _redis = aioredis.Redis(connection_pool=_redis_pool)
            await _redis.ping()
            _note_redis_success()
            logger.info("Redis connected at %s", redis_url)
        except Exception:
            _redis = None
            _redis_retry_after = time.monotonic() + _REDIS_RETRY_COOLDOWN_S
            logger.warning(
                "Redis unavailable at %s — SQLite fallback, retry in %.0fs",
                redis_url, _REDIS_RETRY_COOLDOWN_S,
            )
            return None
    return _redis


def _get_sqlite_conn() -> sqlite3.Connection:
    conn = getattr(_sqlite_local, "conn", None)
    if conn is not None:
        return conn
    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_SQLITE_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv_store ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL,"
        "  expires_at REAL"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_expires ON kv_store(expires_at)"
    )
    conn.commit()
    _sqlite_local.conn = conn
    return conn


def _sqlite_get_sync(key: str) -> str | None:
    try:
        conn = _get_sqlite_conn()
        row = conn.execute(
            "SELECT value, expires_at FROM kv_store WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at is not None and time.time() > expires_at:
            conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            conn.commit()
            return None
        return value
    except Exception:
        return None


def _sqlite_set_sync(key: str, value: str, ttl_seconds: int | None) -> bool:
    try:
        conn = _get_sqlite_conn()
        expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
        conn.execute(
            "INSERT OR REPLACE INTO kv_store(key, value, expires_at) VALUES (?, ?, ?)",
            (key, value, expires_at),
        )
        conn.commit()
        return True
    except Exception:
        return False


def _sqlite_delete_sync(key: str) -> bool:
    try:
        conn = _get_sqlite_conn()
        conn.execute("DELETE FROM kv_store WHERE key = ?", (key,))
        conn.commit()
        return True
    except Exception:
        return False


async def _sqlite_get(key: str) -> str | None:
    return await asyncio.to_thread(_sqlite_get_sync, key)


async def _sqlite_set(key: str, value: str, ttl_seconds: int | None = None) -> bool:
    return await asyncio.to_thread(_sqlite_set_sync, key, value, ttl_seconds)


async def _sqlite_delete(key: str) -> bool:
    return await asyncio.to_thread(_sqlite_delete_sync, key)


async def kv_get(key: str) -> dict | list | str | None:
    full = _full_key(key)
    r = await _ensure_redis()
    if r:
        try:
            val = await r.get(full)
            _note_redis_success()
            if val is None:
                return None
            decoded = val.decode("utf-8") if isinstance(val, bytes) else val
            try:
                return json.loads(decoded)
            except (json.JSONDecodeError, TypeError):
                return decoded
        except Exception:
            _note_redis_failure()
    raw = await _sqlite_get(full)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


async def kv_set(key: str, value: Any, ttl_seconds: int | None = None) -> bool:
    full = _full_key(key)
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    r = await _ensure_redis()
    if r:
        try:
            if ttl_seconds is not None:
                await r.setex(full, ttl_seconds, serialized)
            else:
                await r.set(full, serialized)
            _note_redis_success()
            return True
        except Exception:
            _note_redis_failure()
    return await _sqlite_set(full, serialized, ttl_seconds)


async def kv_delete(key: str) -> bool:
    full = _full_key(key)
    r = await _ensure_redis()
    deleted_redis = False
    if r:
        try:
            await r.delete(full)
            _note_redis_success()
            deleted_redis = True
        except Exception:
            _note_redis_failure()
    deleted_sqlite = await _sqlite_delete(full)
    return deleted_redis or deleted_sqlite


async def kv_incr_with_ttl(key: str, ttl_seconds: int) -> int | None:
    """Atomic INCR + EXPIRE-if-new — race-free rate limiting (REDIS plan §3.3)."""
    full = _full_key(key)
    r = await _ensure_redis()
    if r:
        try:
            pipe = r.pipeline()
            pipe.incr(full)
            pipe.expire(full, ttl_seconds, nx=True)
            results = await pipe.execute()
            _note_redis_success()
            return int(results[0])
        except Exception:
            _note_redis_failure()

    def _sqlite_incr() -> int | None:
        try:
            conn = _get_sqlite_conn()
            now = time.time()
            with conn:  # single transaction — atomic under SQLite's write lock
                conn.execute(
                    "INSERT INTO kv_store(key, value, expires_at) VALUES (?, '0', ?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (full, now + ttl_seconds),
                )
                # Reset if the previous window expired
                conn.execute(
                    "UPDATE kv_store SET value = '0', expires_at = ? "
                    "WHERE key = ? AND expires_at IS NOT NULL AND expires_at < ?",
                    (now + ttl_seconds, full, now),
                )
                conn.execute(
                    "UPDATE kv_store SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
                    "WHERE key = ?",
                    (full,),
                )
                row = conn.execute(
                    "SELECT value FROM kv_store WHERE key = ?", (full,)
                ).fetchone()
            return int(row[0]) if row else None
        except Exception:
            return None

    return await asyncio.to_thread(_sqlite_incr)


# ── Lead hash — one HGETALL per call setup (REDIS plan §2/§3.2) ──────────────

def _lead_hash_key(phone: str) -> str:
    return _full_key(f"lead:{phone}")


def _maybe_json(val: Any) -> Any:
    decoded = val.decode("utf-8") if isinstance(val, bytes) else val
    try:
        return json.loads(decoded)
    except (json.JSONDecodeError, TypeError):
        return decoded


async def get_lead_hash(phone: str) -> dict[str, Any]:
    """All hot lead state in one round trip. Empty dict = cold lead (read files)."""
    key = _lead_hash_key(phone)
    r = await _ensure_redis()
    if r:
        try:
            raw = await r.hgetall(key)
            _note_redis_success()
            if raw:
                return {
                    (k.decode("utf-8") if isinstance(k, bytes) else k): _maybe_json(v)
                    for k, v in raw.items()
                }
        except Exception:
            _note_redis_failure()
    # SQLite fallback stores the whole hash as one JSON blob under the same key;
    # field values are serialized strings — decode each like the Redis path.
    blob = await _sqlite_get(key)
    if blob:
        try:
            parsed = json.loads(blob)
            if isinstance(parsed, dict):
                return {k: _maybe_json(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


async def set_lead_hash(
    phone: str,
    fields: dict[str, Any],
    ttl_seconds: int = _TTL_365D,
) -> bool:
    """Partial-update the lead hash (atomic field writes; TTL refreshed)."""
    if not phone or phone in ("unknown", "demo") or not fields:
        return False
    key = _lead_hash_key(phone)
    payload = {
        str(k): (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
        for k, v in fields.items()
    }
    r = await _ensure_redis()
    if r:
        try:
            pipe = r.pipeline()
            pipe.hset(key, mapping=payload)
            pipe.expire(key, ttl_seconds)
            await pipe.execute()
            _note_redis_success()
            return True
        except Exception:
            _note_redis_failure()

    # SQLite: read-merge-write the JSON blob (thread-safe via WAL connection)
    def _merge_write() -> bool:
        try:
            existing = _sqlite_get_sync(key)
            merged: dict[str, Any] = {}
            if existing:
                try:
                    parsed = json.loads(existing)
                    if isinstance(parsed, dict):
                        merged = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            merged.update(payload)
            return _sqlite_set_sync(
                key, json.dumps(merged, ensure_ascii=False), ttl_seconds
            )
        except Exception:
            return False

    return await asyncio.to_thread(_merge_write)


async def delete_lead_keys(phone: str) -> int:
    """Forget-caller support: remove every kv key for this phone. Returns count."""
    suffixes = [f"lead:{phone}:{k}" for k in _LEAD_HOT_KEYS]
    suffixes.append(f"lead:{phone}:live_name")
    count = 0
    for suffix in suffixes:
        if await kv_delete(suffix):
            count += 1
    # Hash key (full key, not suffixed)
    key = _lead_hash_key(phone)
    r = await _ensure_redis()
    if r:
        try:
            await r.delete(key)
            count += 1
        except Exception:
            _note_redis_failure()
    if await _sqlite_delete(key):
        count += 1
    return count


async def get_lead_hot_context(phone: str) -> dict:
    suffixes = [f"lead:{phone}:{k}" for k in _LEAD_HOT_KEYS]
    r = await _ensure_redis()
    if r:
        try:
            pipe = r.pipeline()
            for suffix in suffixes:
                pipe.get(_full_key(suffix))
            results = await pipe.execute()
        except Exception:
            results = [None] * len(_LEAD_HOT_KEYS)
    else:
        # Parallel SQLite reads via gather
        async def _read_one(suffix: str):
            return await asyncio.to_thread(_sqlite_get_sync, _full_key(suffix))
        results = await asyncio.gather(*(_read_one(s) for s in suffixes))

    context: dict = {}
    for key_name, val in zip(_LEAD_HOT_KEYS, results):
        if val is None:
            context[key_name] = None
            continue
        decoded = val.decode("utf-8") if isinstance(val, bytes) else val
        try:
            context[key_name] = json.loads(decoded)
        except (json.JSONDecodeError, TypeError):
            context[key_name] = decoded
    return context


async def set_lead_hot_context(phone: str, profile: Any) -> bool:
    from re_voice_memory.lead import LeadProfile

    if not isinstance(profile, LeadProfile):
        return False

    now = datetime.now(timezone.utc).isoformat()
    last_seen = profile.last_contact_date or now

    profile_json = json.dumps({
        "name": profile.stable_name,
        "role": profile.role,
        "budget_min": profile.budget_min,
        "budget_max": profile.budget_max,
        "preferred_areas": profile.preferred_areas,
        "deal_stage": profile.deal_stage,
        "intent_score": profile.intent_score,
        "language_pref": profile.language_pref,
    }, ensure_ascii=False)

    writes: list[tuple[str, str, int | None]] = [
        (f"lead:{phone}:profile", profile_json, _TTL_365D),
        (f"lead:{phone}:intent_score", str(profile.intent_score), _TTL_365D),
        (f"lead:{phone}:deal_stage", str(profile.deal_stage), _TTL_365D),
        (f"lead:{phone}:call_count", str(profile.call_count), None),
        (f"lead:{phone}:last_seen", last_seen, _TTL_365D),
    ]

    # Write-through to the lead hash (REDIS plan §2 migration) — one extra
    # pipeline, keeps the single-round-trip schema in sync with legacy keys.
    try:
        await set_lead_hash(phone, {
            "profile": profile_json,
            "name": profile.stable_name or "",
            "intent_score": str(profile.intent_score),
            "deal_stage": str(profile.deal_stage),
            "call_count": str(profile.call_count),
            "last_seen": last_seen,
            "relations": getattr(profile, "relation_names", []) or [],
            "slots": getattr(profile, "slot_values", {}) or {},
        })
    except Exception:
        pass

    r = await _ensure_redis()
    if r:
        try:
            pipe = r.pipeline()
            for suffix, val, ttl in writes:
                full = _full_key(suffix)
                if ttl is not None:
                    pipe.setex(full, ttl, val)
                else:
                    pipe.set(full, val)
            await pipe.execute()
            return True
        except Exception:
            pass

    # SQLite fallback — parallel writes
    async def _write_one(suffix: str, val: str, ttl: int | None):
        return await asyncio.to_thread(_sqlite_set_sync, _full_key(suffix), val, ttl)
    write_results = await asyncio.gather(*(
        _write_one(s, v, t) for s, v, t in writes
    ))
    return all(write_results)


async def get_listing_facts(listing_id: str) -> dict | None:
    result = await kv_get(f"listing:{listing_id}:facts")
    if isinstance(result, dict):
        return result
    return None


async def refresh_campaign_listings(campaign_id: str, listing_ids: list[str]) -> bool:
    return await kv_set(f"campaign:{campaign_id}:listings", listing_ids, _TTL_7D)
