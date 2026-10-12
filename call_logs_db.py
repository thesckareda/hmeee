"""Filtered call_logs queries for the Call Logs dashboard (Step 7)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import db

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = (0.4, 1.0, 2.0)


@dataclass
class CallLogsQuery:
    agent_id: str | None = None
    bulk_call_id: str | None = None
    bulk_only: bool = False
    call_status: str | None = None
    call_direction: str | None = None
    channel_type: str | None = None
    call_transferred: bool | None = None
    duration_min: int | None = None
    duration_max: int | None = None
    call_id: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    phone: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    page: int = 1
    page_size: int = 25
    max_rows: int | None = None


def _is_retryable(err: str) -> bool:
    lowered = err.lower()
    return any(
        token in lowered
        for token in ("timeout", "connection", "ssl", "reset", "temporarily", "503", "502")
    )


def _date_start_iso(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if "T" in raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def _date_end_iso(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if "T" in raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        day = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        dt = datetime.combine(day, time(23, 59, 59, 999999), tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def _phone_digits(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def apply_call_log_filters(query: Any, params: CallLogsQuery) -> Any:
    """Apply Supabase/PostgREST filters to a call_logs select query."""
    if params.call_id:
        return query.eq("id", params.call_id)

    if params.agent_id:
        query = query.eq("agent_id", params.agent_id)
    if params.bulk_call_id:
        query = query.eq("bulk_call_id", params.bulk_call_id)
    elif params.bulk_only:
        query = query.not_.is_("bulk_call_id", "null")

    if params.call_status:
        query = query.eq("call_status", params.call_status.strip().lower())
    if params.call_direction:
        query = query.eq("call_direction", params.call_direction.strip().lower())
    if params.channel_type:
        query = query.eq("channel_type", params.channel_type.strip().lower())
    if params.call_transferred is not None:
        query = query.eq("call_transferred", bool(params.call_transferred))

    if params.duration_min is not None:
        query = query.gte("duration_seconds", int(params.duration_min))
    if params.duration_max is not None:
        query = query.lte("duration_seconds", int(params.duration_max))

    if params.from_number:
        digits = _phone_digits(params.from_number)
        if digits:
            query = query.ilike("from_number", f"%{digits}%")
    if params.to_number:
        digits = _phone_digits(params.to_number)
        if digits:
            query = query.ilike("to_number", f"%{digits}%")
    if params.phone:
        digits = _phone_digits(params.phone)
        if digits:
            pattern = f"%{digits}%"
            query = query.or_(
                f"from_number.ilike.{pattern},"
                f"to_number.ilike.{pattern},"
                f"phone_number.ilike.{pattern},"
                f"caller_phone.ilike.{pattern}"
            )

    start = _date_start_iso(params.date_from)
    end = _date_end_iso(params.date_to)
    if start:
        query = query.gte("created_at", start)
    if end:
        query = query.lte("created_at", end)

    return query


def pagination_bounds(params: CallLogsQuery) -> tuple[int, int]:
    page = max(1, int(params.page or 1))
    page_size = max(1, min(int(params.page_size or 25), 200))
    if params.max_rows is not None:
        page_size = min(page_size, int(params.max_rows))
    offset = (page - 1) * page_size
    return offset, page_size


async def fetch_agent_name_map() -> dict[str, str]:
    sb = await db.get_supabase()
    if not sb:
        return {}
    try:
        res = sb.table("agents").select("id,name").execute()
        return {str(row["id"]): str(row.get("name") or "") for row in (res.data or [])}
    except Exception as exc:
        logger.warning("fetch_agent_name_map failed: %s", exc)
        return {}


async def fetch_filtered_calls(
    params: CallLogsQuery,
    *,
    include_transcript: bool = False,
) -> tuple[list[dict], int]:
    """Return (rows, total_count) for call_logs matching filters."""
    sb = await db.get_supabase()
    if not sb:
        return [], 0

    offset, page_size = pagination_bounds(params)
    columns = "*" if include_transcript else db.CALL_LOG_LIST_COLUMNS

    for attempt in range(_MAX_RETRIES):
        try:
            query = sb.table("call_logs").select(columns, count="exact")
            query = apply_call_log_filters(query, params)
            query = query.order("created_at", desc=True)
            if params.max_rows is not None:
                query = query.limit(int(params.max_rows))
            else:
                query = query.range(offset, offset + page_size - 1)
            res = query.execute()
            rows = res.data or []
            total = int(res.count or len(rows))
            return rows, total
        except Exception as exc:
            err = str(exc)
            if _is_retryable(err) and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error("fetch_filtered_calls failed: %s", exc)
            return [], 0
    return [], 0


async def fetch_call_by_id(call_id: str) -> dict | None:
    sb = await db.get_supabase()
    if not sb or not call_id:
        return None

    for attempt in range(_MAX_RETRIES):
        try:
            res = (
                sb.table("call_logs")
                .select("*")
                .eq("id", call_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as exc:
            err = str(exc)
            if _is_retryable(err) and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error("fetch_call_by_id failed: %s", exc)
            return None
    return None


def attach_agent_names(rows: list[dict], agent_names: dict[str, str]) -> list[dict]:
    enriched: list[dict] = []
    for row in rows:
        item = dict(row)
        agent_id = str(item.get("agent_id") or "")
        item["agent_name"] = agent_names.get(agent_id) or (
            "Unassigned" if not agent_id else "Unknown Agent"
        )
        enriched.append(item)
    return enriched