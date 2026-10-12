"""
Site-visit conversion tracking — baseline vs current rate per campaign/period.

Events are persisted to data/conversion_events.json for lightweight health/metrics
without requiring a live Supabase query on every scrape.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("conversion-metrics")

IST = timezone(timedelta(hours=5, minutes=30))
DATA_DIR = Path(__file__).resolve().parent / "data"
EVENTS_PATH = DATA_DIR / "conversion_events.json"

_lock = threading.Lock()


def _ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not EVENTS_PATH.exists():
        EVENTS_PATH.write_text("[]", encoding="utf-8")


def _load_events() -> list[dict]:
    _ensure_store()
    try:
        raw = EVENTS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("[CONVERSION] Failed to load events: %s", e)
        return []


def _save_events(events: list[dict]) -> None:
    _ensure_store()
    EVENTS_PATH.write_text(json.dumps(events, indent=2), encoding="utf-8")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except Exception:
        return None


def _campaign_key(campaign_id: str | None) -> str:
    return (campaign_id or "").strip() or "_direct"


def record_call_completed(campaign_id: str | None = None, phone: str = "") -> None:
    """Record a completed call for conversion denominator."""
    event = {
        "type": "call",
        "campaign_id": _campaign_key(campaign_id),
        "phone": phone,
        "ts": datetime.now(IST).isoformat(),
    }
    with _lock:
        events = _load_events()
        events.append(event)
        if len(events) > 50_000:
            events = events[-50_000:]
        _save_events(events)


def record_site_visit_booking(
    campaign_id: str | None = None,
    phone: str = "",
    listing_id: str = "",
) -> None:
    """Record a site visit booking for conversion numerator."""
    event = {
        "type": "site_visit",
        "campaign_id": _campaign_key(campaign_id),
        "phone": phone,
        "listing_id": listing_id,
        "ts": datetime.now(IST).isoformat(),
    }
    with _lock:
        events = _load_events()
        events.append(event)
        if len(events) > 50_000:
            events = events[-50_000:]
        _save_events(events)
    try:
        from re_voice_memory.metrics import record_viewing

        record_viewing()
    except Exception:
        pass


def _events_in_window(
    events: list[dict],
    *,
    campaign_id: str | None,
    start: datetime,
    end: datetime,
) -> tuple[int, int]:
    key = _campaign_key(campaign_id)
    calls = 0
    visits = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if campaign_id is not None and ev.get("campaign_id") != key:
            continue
        ts = _parse_ts(ev.get("ts"))
        if not ts or not (start <= ts < end):
            continue
        if ev.get("type") == "call":
            calls += 1
        elif ev.get("type") == "site_visit":
            visits += 1
    return calls, visits


def _rate(visits: int, calls: int) -> float:
    if not calls:
        return 0.0
    return round((visits / calls) * 100, 2)


def get_conversion_delta(
    campaign_id: str | None = None,
    period_days: int = 7,
    baseline_days: int = 7,
    ref: datetime | None = None,
) -> dict[str, Any]:
    """
    Compare site-visit booking rate in the current window vs the prior baseline window.
    """
    ref = (ref or datetime.now(IST)).astimezone(IST)
    current_end = ref
    current_start = ref - timedelta(days=period_days)
    baseline_end = current_start
    baseline_start = baseline_end - timedelta(days=baseline_days)

    events = _load_events()
    cur_calls, cur_visits = _events_in_window(
        events, campaign_id=campaign_id, start=current_start, end=current_end
    )
    base_calls, base_visits = _events_in_window(
        events, campaign_id=campaign_id, start=baseline_start, end=baseline_end
    )

    current_rate = _rate(cur_visits, cur_calls)
    baseline_rate = _rate(base_visits, base_calls)
    delta = round(current_rate - baseline_rate, 2)

    return {
        "campaign_id": _campaign_key(campaign_id),
        "period_days": period_days,
        "baseline_days": baseline_days,
        "current_calls": cur_calls,
        "current_site_visits": cur_visits,
        "current_rate_pct": current_rate,
        "baseline_calls": base_calls,
        "baseline_site_visits": base_visits,
        "baseline_rate_pct": baseline_rate,
        "delta_pct": delta,
        "improved": delta > 0,
    }


def get_conversion_health() -> dict[str, Any]:
    """Compact summary for /health and Prometheus-adjacent dashboards."""
    overall = get_conversion_delta(campaign_id=None)
    return {
        "site_visit_conversion": overall,
        "events_total": len(_load_events()),
        "store_path": str(EVENTS_PATH),
    }


def reset_events_for_tests() -> None:
    """Clear persisted events — test helper only."""
    with _lock:
        _save_events([])