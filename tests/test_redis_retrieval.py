"""REDIS_RETRIEVAL_OPTIMIZATION — hash store, atomic INCR, FAQ precompute, remember button.

All kv tests run on the SQLite fallback backend (no REDIS_URL in CI) — the
same code paths production uses when Redis is down.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _phone() -> str:
    return "+919" + uuid.uuid4().hex[:9]


# ── Lead hash round-trip (REDIS plan §2/§3.2) ────────────────────────────────

def test_lead_hash_round_trip_and_partial_update():
    from re_voice_memory.kv_store import get_lead_hash, set_lead_hash, delete_lead_keys

    phone = _phone()

    async def run():
        assert await get_lead_hash(phone) == {}
        ok = await set_lead_hash(phone, {"name": "Ravi", "slots": {"budget": "1.2 Cr"}})
        assert ok
        data = await get_lead_hash(phone)
        assert data["name"] == "Ravi"
        assert data["slots"] == {"budget": "1.2 Cr"}

        # Partial update must merge, not clobber
        await set_lead_hash(phone, {"deal_stage": "hot"})
        data = await get_lead_hash(phone)
        assert data["name"] == "Ravi"
        assert data["deal_stage"] == "hot"

        await delete_lead_keys(phone)
        assert await get_lead_hash(phone) == {}

    asyncio.run(run())


def test_lead_hash_rejects_unknown_phone():
    from re_voice_memory.kv_store import set_lead_hash

    async def run():
        assert not await set_lead_hash("unknown", {"name": "X"})
        assert not await set_lead_hash("", {"name": "X"})

    asyncio.run(run())


# ── Atomic INCR — no lost updates under concurrency (§3.3) ───────────────────

def test_incr_with_ttl_atomic_under_concurrency():
    from re_voice_memory.kv_store import kv_delete, kv_incr_with_ttl

    key = f"test_rate:{uuid.uuid4().hex[:8]}"

    async def run():
        results = await asyncio.gather(*(kv_incr_with_ttl(key, 60) for _ in range(50)))
        assert None not in results
        assert max(results) == 50  # every increment counted — no race
        assert sorted(results) == list(range(1, 51))
        await kv_delete(key)

    asyncio.run(run())


def test_rate_limiter_uses_atomic_path():
    from phone_utils import is_rate_limited, RATE_LIMIT_MAX

    phone = _phone()

    async def run():
        for _ in range(RATE_LIMIT_MAX):
            assert not await is_rate_limited(phone)
        assert await is_rate_limited(phone)  # 101st call blocked

    asyncio.run(run())


# ── FAQ precompute equivalence (§3.5) ────────────────────────────────────────

def test_faq_lookup_unchanged_after_precompute():
    from knowledge_pack import get_store

    store = get_store()
    store.ensure_loaded()
    # Index entries now carry precomputed token sets
    for entries in store._faq_index.values():
        for entry in entries:
            assert len(entry) == 5
            assert isinstance(entry[4], frozenset)

    hit = store.lookup_faq("who founded the company", "tinglish")
    assert hit is not None and hit.confidence >= 0.45


def test_match_score_backward_compatible():
    from knowledge_pack import _match_score, _normalize, _tokens

    nt = _normalize("what is the office address")
    tt = _tokens(nt)
    nq = _normalize("office address location")
    # legacy 3-arg call (tokenizes on the fly) == precomputed call
    assert _match_score(nt, tt, nq) == _match_score(nt, tt, nq, frozenset(_tokens(nq)))


# ── Remember-caller button ───────────────────────────────────────────────────

def test_remember_off_skips_memory_read():
    from re_voice_memory.inject import inject_lead_context

    async def run():
        base = "BASE INSTRUCTIONS"
        out = await inject_lead_context(
            phone="+919000000001", base_instructions=base, remember_enabled=False
        )
        assert out == base  # untouched — no memory injected

    asyncio.run(run())


def test_remember_off_skips_name_write_through(monkeypatch):
    import name_capture

    writes: list = []
    monkeypatch.setattr(name_capture, "_write_through_name", lambda p, n: writes.append(n))

    live_off = {"remember_caller_enabled": False, "_re_lead_phone": "+919000000002"}
    name_capture.capture_from_transcript("na peru Ravi andi", live_off)
    assert live_off["_caller_name"] == "Ravi"  # in-call use still works
    assert writes == []  # but nothing persisted

    live_on = {"remember_caller_enabled": True, "_re_lead_phone": "+919000000003"}
    name_capture.capture_from_transcript("na peru Suresh andi", live_on)
    assert writes == ["Suresh"]


def test_forget_caller_wipes_container(monkeypatch, tmp_path):
    from re_voice_memory import memory_admin
    from re_voice_memory.kv_store import get_lead_hash, set_lead_hash

    phone = _phone()
    monkeypatch.setattr(memory_admin, "MEMORY_DIR", tmp_path)
    lead_dir = tmp_path / "leads" / phone
    lead_dir.mkdir(parents=True)
    (lead_dir / "lead_profile.md").write_text("test", encoding="utf-8")

    async def run():
        await set_lead_hash(phone, {"name": "Ravi"})
        result = await memory_admin.forget_caller(phone)
        assert result["files_deleted"]
        assert not lead_dir.exists()
        assert await get_lead_hash(phone) == {}

    asyncio.run(run())

    summary = memory_admin.memory_container_summary(phone)
    assert not summary["exists"]


def test_forget_rejects_invalid_phone():
    from re_voice_memory.memory_admin import forget_caller

    async def run():
        result = await forget_caller("unknown")
        assert result.get("error")

    asyncio.run(run())


# ── Redis hardening: cooldown means no reconnect storm ───────────────────────

def test_redis_cooldown_prevents_reconnect_storm(monkeypatch):
    from re_voice_memory import kv_store
    import time as _time

    # Simulate a recent failure — _ensure_redis must return None instantly
    monkeypatch.setattr(kv_store, "_redis", None)
    monkeypatch.setattr(kv_store, "_redis_retry_after", _time.monotonic() + 30)

    async def run():
        t0 = _time.perf_counter()
        r = await kv_store._ensure_redis()
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        assert r is None
        assert elapsed_ms < 5  # no blocking connection attempt during cooldown

    asyncio.run(run())
