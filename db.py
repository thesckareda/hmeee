import os
import asyncio
import logging
import json
import re
import uuid
from datetime import datetime
from datetime_utils import now_utc
from pathlib import Path
from supabase import create_client, Client
from phone_utils import normalize_phone_number, phone_match_variants
from secrets_manager import get_secret
from utils import is_booking_confirmed, sanitize_caller_name
from tenancy import apply_org_filter, inject_org_id

logger = logging.getLogger("db")

# ─── Columns added by supabase_migration_v2.sql ───────────────────────────────
# If the migration hasn't been run yet, these columns won't exist.
# We detect PGRST204 (schema cache miss) and retry with just base columns.
_ANALYTICS_COLUMNS = {
    "sentiment", "was_booked", "interrupt_count",
    "estimated_cost_usd", "call_date", "call_hour", "call_day_of_week",
    "call_purpose", "call_summary", "appointment_time",
}
_AGENT_COLUMNS = {
    "agent_id", "call_status", "call_direction", "channel_type", "call_transferred",
    "ended_by", "room_id", "from_number", "to_number", "bulk_call_id", "extracted_info",
}
_BASE_COLUMNS = {"phone_number", "duration_seconds", "transcript", "summary",
                 "recording_url", "caller_name"}
CALL_LOG_LIST_COLUMNS = ",".join(
    sorted({"id", "created_at"} | _BASE_COLUMNS | _ANALYTICS_COLUMNS | _AGENT_COLUMNS - {"transcript"})
)
_analytics_fallback_logged = False
_missing_credentials_logged = False


# ─── Retry helper ─────────────────────────────────────────────────────────────
_MAX_RETRIES = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]   # seconds — covers transient SSL 525 errors


def _is_retryable(err_str: str) -> bool:
    """True if the error is a transient network or SSL failure worth retrying."""
    transient = (
        "525", "ssl", "timeout", "connection", "network", "502", "503", "504",
        "getaddrinfo", "name or service not known", "temporary failure in name resolution",
    )
    el = err_str.lower()
    return any(k in el for k in transient)


def _is_schema_error(err_str: str) -> bool:
    """True if Supabase returned PGRST204 — column not found in schema cache."""
    return "PGRST204" in err_str or "schema cache" in err_str.lower()


# ─── Client ───────────────────────────────────────────────────────────────────

_supabase_client: Client | None = None
_supabase_admin_client: Client | None = None
_supabase_lock = asyncio.Lock()


def reset_supabase_clients() -> None:
    """Clear cached clients (e.g. after env keys change)."""
    global _supabase_client, _supabase_admin_client, _missing_credentials_logged
    _supabase_client = None
    _supabase_admin_client = None
    _missing_credentials_logged = False


async def get_supabase(url: str | None = None, key: str | None = None) -> Client | None:
    global _supabase_client, _missing_credentials_logged

    if _supabase_client is not None:
        return _supabase_client

    async with _supabase_lock:
        if _supabase_client is not None:
            return _supabase_client

        resolved_url = (url or "").strip() or (get_secret("SUPABASE_URL") or "").strip()
        resolved_key = (
            (key or "").strip()
            or (get_secret("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
            or (get_secret("SUPABASE_KEY") or "").strip()
        )

        if not resolved_url or not resolved_key:
            if not _missing_credentials_logged:
                logger.error("Supabase credentials missing. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/SUPABASE_KEY in the environment.")
                _missing_credentials_logged = True
            return None
        try:
            _supabase_client = create_client(resolved_url, resolved_key)
            return _supabase_client
        except Exception as e:
            logger.error(f"Failed to init Supabase client: {e}")
            return None


async def get_supabase_admin(url: str | None = None, key: str | None = None) -> Client | None:
    """Service-role client for trusted server-side writes (agents CRUD, etc.)."""
    global _supabase_admin_client

    if _supabase_admin_client is not None:
        return _supabase_admin_client

    async with _supabase_lock:
        if _supabase_admin_client is not None:
            return _supabase_admin_client

        resolved_url = (url or "").strip() or (get_secret("SUPABASE_URL") or "").strip()
        resolved_key = (key or "").strip() or (get_secret("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

        if not resolved_url or not resolved_key:
            return None
        try:
            _supabase_admin_client = create_client(resolved_url, resolved_key)
            return _supabase_admin_client
        except Exception as e:
            logger.error(f"Failed to init Supabase admin client: {e}")
            return None


async def fetch_stable_caller_name(phone: str | None) -> str:
    """Return the first non-placeholder name ever saved for this phone number."""
    normalized_phone = normalize_phone_number(phone)
    if not normalized_phone or normalized_phone in ("unknown", "demo"):
        return ""

    supabase = await get_supabase()
    if not supabase:
        return ""

    variants = phone_match_variants(normalized_phone) or [normalized_phone]
    try:
        result = (
            supabase.table("call_logs")
            .select("caller_name, created_at")
            .in_("phone_number", variants)
            .order("created_at", desc=False)
            .limit(500)
            .execute()
        )
        for row in result.data or []:
            stable_name = sanitize_caller_name(row.get("caller_name"))
            if stable_name:
                return stable_name
    except Exception as e:
        logger.debug(f"Failed to resolve stable caller name for {normalized_phone}: {e}")

    return ""


async def fetch_recent_phone_for_caller_name(caller_name: str | None) -> str:
    """Return most recent valid phone for a non-placeholder caller name."""
    name = sanitize_caller_name(caller_name)
    if not name:
        return ""

    supabase = await get_supabase()
    if not supabase:
        return ""

    try:
        result = (
            supabase.table("call_logs")
            .select("phone_number, created_at")
            .eq("caller_name", name)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        for row in result.data or []:
            normalized = normalize_phone_number(row.get("phone_number"))
            if normalized and normalized not in ("unknown", "demo"):
                return normalized
    except Exception as e:
        logger.debug(f"Failed to resolve phone by caller name '{name}': {e}")

    return ""


# ─── save_call_log ────────────────────────────────────────────────────────────

async def save_call_log(
    phone: str,
    duration: int,
    transcript: str,
    summary: str = "",
    appointment_time: str | None = None,
    call_purpose: str = "",
    call_summary: str = "",
    recording_url: str = "",
    caller_name: str = "",
    sentiment: str = "unknown",
    estimated_cost_usd: float | None = None,
    call_date: str | None = None,
    call_hour: int | None = None,
    call_day_of_week: str | None = None,
    was_booked: bool = False,
    interrupt_count: int = 0,
    agent_id: str | None = None,
    call_status: str | None = None,
    call_direction: str | None = None,
    channel_type: str | None = None,
    call_transferred: bool = False,
    ended_by: str | None = None,
    room_id: str | None = None,
    from_number: str | None = None,
    to_number: str | None = None,
    extracted_info: dict | None = None,
    ai_summary: dict | None = None,
    bulk_call_id: str | None = None,
) -> dict:
    """
    Insert a call log into Supabase.

    Strategy:
    1. Try with all columns (including analytics columns from migration_v2).
    2. If PGRST204 (column not in schema cache — migration not yet run),
       retry with only the base columns so the call is never silently lost.
    3. Retry up to 3× on transient SSL/network errors with exponential backoff.
    """
    supabase = await get_supabase()
    if not supabase:
        logger.info(f"Supabase not configured. Local log → {phone} {duration}s")
        return {"success": False, "message": "Supabase client failed"}

    normalized_phone = normalize_phone_number(phone)

    if not normalized_phone or normalized_phone in ("unknown", "demo"):
        recovered_phone = await fetch_recent_phone_for_caller_name(caller_name)
        if recovered_phone:
            normalized_phone = recovered_phone
            logger.info(f"Recovered phone for caller '{caller_name}': {normalized_phone}")

    stable_caller_name = await fetch_stable_caller_name(normalized_phone or phone)
    resolved_caller_name = stable_caller_name or sanitize_caller_name(caller_name)

    # Build full payload
    full_data: dict = {
        "phone_number":    normalized_phone or phone,
        "duration_seconds": duration,
        "transcript":      transcript,
        "summary":         summary,
        "appointment_time": appointment_time,
        "call_purpose":    call_purpose,
        "call_summary":    call_summary,
        "sentiment":       sentiment,
        "was_booked":      was_booked,
        "interrupt_count": interrupt_count,
    }
    if recording_url:               full_data["recording_url"]      = recording_url
    if resolved_caller_name:        full_data["caller_name"]         = resolved_caller_name
    if estimated_cost_usd is not None: full_data["estimated_cost_usd"] = estimated_cost_usd
    if call_date:                   full_data["call_date"]           = call_date
    if call_hour is not None:       full_data["call_hour"]           = call_hour
    if call_day_of_week:            full_data["call_day_of_week"]    = call_day_of_week
    if agent_id:                    full_data["agent_id"]            = agent_id
    if call_status:                 full_data["call_status"]         = call_status
    if call_direction:              full_data["call_direction"]      = call_direction
    if channel_type:                full_data["channel_type"]        = channel_type
    if call_transferred:            full_data["call_transferred"]    = call_transferred
    if ended_by:                    full_data["ended_by"]            = ended_by
    if room_id:                     full_data["room_id"]             = room_id
    if from_number:                 full_data["from_number"]         = from_number
    if to_number:                   full_data["to_number"]           = to_number
    if extracted_info:              full_data["extracted_info"]      = extracted_info
    if ai_summary:                  full_data["ai_summary"]          = ai_summary
    if bulk_call_id:                full_data["bulk_call_id"]        = bulk_call_id

    # Base-only payload (fallback if migration not run)
    base_data: dict = {
        k: v for k, v in full_data.items()
        if k not in _ANALYTICS_COLUMNS and k not in _AGENT_COLUMNS
    }

    async def _try_insert(data: dict, label: str) -> dict:
        data = inject_org_id(data)
        for attempt in range(_MAX_RETRIES):
            try:
                res = supabase.table("call_logs").insert(data).execute()
                logger.info(f"Saved call log for {phone} ({label})")
                return {"success": True, "data": res.data}
            except Exception as e:
                err = str(e)
                if _is_schema_error(err):
                    # Column missing — propagate so caller can retry with base
                    raise RuntimeError("SCHEMA_ERROR:" + err)
                if _is_retryable(err) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(f"Transient error (attempt {attempt+1}), retrying in {delay}s: {err[:80]}")
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"Failed to save call log ({label}): {e}")
                return {"success": False, "message": err}
        return {"success": False, "message": "Max retries exceeded"}

    # Attempt 1: full payload
    global _analytics_fallback_logged
    try:
        return await _try_insert(full_data, "full")
    except RuntimeError as e:
        err = str(e)
        if "SCHEMA_ERROR" in err:
            # Migration not run yet — fall back to base columns only
            if not _analytics_fallback_logged:
                logger.info(
                    "Analytics/agent columns not present yet; using base call_logs columns. "
                    "Run supabase migrations to enable extended fields."
                )
                _analytics_fallback_logged = True
            analytics_fallback = {k: v for k, v in full_data.items() if k not in _AGENT_COLUMNS}
            agent_fallback = {k: v for k, v in analytics_fallback.items() if k not in _ANALYTICS_COLUMNS}
            return await _try_insert(agent_fallback, "base-fallback")
        raise


# ─── fetch_call_logs ──────────────────────────────────────────────────────────

async def fetch_call_logs(limit: int = 50, *, include_transcript: bool = False) -> list:
    supabase = await get_supabase()
    if not supabase:
        return []
    columns = "*" if include_transcript else CALL_LOG_LIST_COLUMNS
    for attempt in range(_MAX_RETRIES):
        try:
            query = (
                supabase.table("call_logs")
                .select(columns)
                .order("created_at", desc=True)
                .limit(limit)
            )
            query = apply_org_filter(query)
            res = query.execute()
            return res.data
        except Exception as e:
            err = str(e)
            if _is_schema_error(err) and not include_transcript:
                base_columns = ",".join(sorted({"id", "created_at"} | _BASE_COLUMNS - {"transcript"}))
                try:
                    query = (
                        supabase.table("call_logs")
                        .select(base_columns)
                        .order("created_at", desc=True)
                        .limit(limit)
                    )
                    query = apply_org_filter(query)
                    res = query.execute()
                    return res.data
                except Exception as fallback_err:
                    logger.error(f"Failed to fetch call logs (base fallback): {fallback_err}")
                    return []
            if _is_retryable(err) and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error(f"Failed to fetch call logs: {e}")
            return []
    return []


async def fetch_call_logs_or_transcripts(limit: int = 50, *, include_transcript: bool = False) -> list:
    """
    Primary source: call_logs.
    Fallback source: call_transcripts (when call_logs is empty due interrupted shutdowns).
    """
    logs = await fetch_call_logs(limit=limit, include_transcript=include_transcript)
    if logs:
        return logs

    supabase = await get_supabase()
    if not supabase:
        return []

    try:
        # Pull enough rows to reconstruct recent conversations by room.
        transcript_limit = max(500, limit * 40)
        res = (
            supabase.table("call_transcripts")
            .select("call_room_id, phone, role, content, created_at")
            .order("created_at", desc=True)
            .limit(transcript_limit)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return []

        by_room: dict[str, dict] = {}
        for row in rows:
            room_id = str(row.get("call_room_id") or "").strip()
            if not room_id:
                continue
            bucket = by_room.setdefault(
                room_id,
                {
                    "room_id": room_id,
                    "phone": "",
                    "first_created": None,
                    "last_created": None,
                    "items": [],
                },
            )
            created_at = row.get("created_at")
            if created_at:
                if not bucket["first_created"] or created_at < bucket["first_created"]:
                    bucket["first_created"] = created_at
                if not bucket["last_created"] or created_at > bucket["last_created"]:
                    bucket["last_created"] = created_at
            if not bucket["phone"]:
                bucket["phone"] = normalize_phone_number(row.get("phone"))
            bucket["items"].append(row)

        recovered = []

        def _extract_phone_from_room_id(room_id: str) -> str:
            raw = str(room_id or "")
            for match in re.findall(r"\+?\d{10,15}", raw):
                normalized = normalize_phone_number(match)
                if normalized and normalized not in ("unknown", "demo"):
                    return normalized
            return ""

        for room_data in by_room.values():
            items = sorted(room_data["items"], key=lambda r: str(r.get("created_at") or ""))
            lines = []
            for item in items:
                role = str(item.get("role") or "assistant").strip().upper()
                content = str(item.get("content") or "").strip()
                if not content:
                    continue
                lines.append(f"[{role}] {content}")
            if not lines:
                continue

            first_ts = room_data.get("first_created")
            last_ts = room_data.get("last_created") or first_ts
            duration_seconds = 0
            try:
                if first_ts and last_ts:
                    first_dt = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
                    last_dt = datetime.fromisoformat(str(last_ts).replace("Z", "+00:00"))
                    duration_seconds = max(1, int((last_dt - first_dt).total_seconds()))
            except Exception:
                duration_seconds = 0

            normalized_phone = normalize_phone_number(room_data.get("phone"))
            if not normalized_phone or normalized_phone in ("unknown", "demo"):
                normalized_phone = _extract_phone_from_room_id(room_data["room_id"])
            stable_name = fetch_stable_caller_name(normalized_phone)
            resolved_name = stable_name or ""

            recovered.append(
                {
                    "id": f"recovered-{room_data['room_id']}",
                    "created_at": last_ts,
                    "phone_number": normalized_phone or room_data.get("phone") or "unknown",
                    "duration_seconds": duration_seconds,
                    "transcript": "\n".join(lines),
                    "summary": "Recovered from transcript history",
                    "call_summary": "Recovered from transcript history",
                    "call_purpose": "general_inquiry",
                    "appointment_time": None,
                    "recording_url": "",
                    "caller_name": resolved_name,
                    "room_id": room_data["room_id"],
                    "sentiment": "unknown",
                    "estimated_cost_usd": None,
                    "call_date": None,
                    "call_hour": None,
                    "call_day_of_week": None,
                    "was_booked": False,
                    "interrupt_count": 0,
                    "_source": "call_transcripts_fallback",
                }
            )

        recovered.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return recovered[:limit]
    except Exception as e:
        logger.error(f"Failed transcript fallback for call logs: {e}")
        return []


# ─── fetch_bookings ───────────────────────────────────────────────────────────

async def fetch_bookings() -> list:
    supabase = await get_supabase()
    if not supabase:
        return []
    try:
        res = (
            supabase.table("call_logs")
            .select("id, phone_number, summary, created_at, appointment_time, caller_name, was_booked, recording_url")
            .eq("was_booked", True)
            .not_.is_("appointment_time", "null")
            .neq("phone_number", "demo")
            .order("appointment_time", desc=True)
            .limit(500)
            .execute()
        )
        bookings = res.data or []
        bookings.sort(key=lambda x: (str(x.get("appointment_time") or ""), str(x.get("created_at") or "")), reverse=True)
        return bookings
    except Exception as e:
        err = str(e)
        if _is_schema_error(err):
            try:
                res = (
                    supabase.table("call_logs")
                    .select("id, phone_number, summary, created_at, appointment_time, recording_url")
                    .not_.is_("appointment_time", "null")
                    .neq("phone_number", "demo")
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                )
                rows = res.data or []
                bookings = []
                for r in rows:
                    summary = str(r.get("summary") or "").lower()
                    if (("confirm" in summary) or ("already_signed_up" in summary) or ("already signed up" in summary)) and r.get("appointment_time"):
                        bookings.append(r)
                bookings.sort(key=lambda x: (str(x.get("appointment_time") or ""), str(x.get("created_at") or "")), reverse=True)
                return bookings
            except Exception as e2:
                logger.error(f"Failed to fetch bookings (fallback): {e2}")
                return []
        logger.error(f"Failed to fetch bookings: {e}")
        return []


async def fetch_call_transcripts_for_ids(call_ids: list[str]) -> list:
    if not call_ids:
        return []
    supabase = await get_supabase()
    if not supabase:
        return []
    try:
        res = (
            supabase.table("call_transcripts")
            .select("*")
            .in_("call_id", call_ids)
            .order("created_at", desc=True)
            .limit(10000)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch call transcripts for IDs: {e}")
        return []


# ─── fetch_stats ──────────────────────────────────────────────────────────────

async def fetch_stats() -> dict:
    _empty = {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}
    supabase = await get_supabase()
    if not supabase:
        return _empty
    try:
        rows = (
            supabase.table("call_logs")
            .select("duration_seconds, summary, phone_number, was_booked")
            .neq("phone_number", "demo")
            .execute()
        ).data or []
        total = len(rows)
        bookings = 0
        for r in rows:
            summary = str(r.get("summary") or "").lower()
            if bool(r.get("was_booked")) or ("confirm" in summary) or ("already_signed_up" in summary) or ("already signed up" in summary):
                bookings += 1
        durations = [r["duration_seconds"] for r in rows if r.get("duration_seconds")]
        avg_dur = round(sum(durations) / len(durations)) if durations else 0
        rate = round((bookings / total) * 100) if total else 0
        return {"total_calls": total, "total_bookings": bookings, "avg_duration": avg_dur, "booking_rate": rate}
    except Exception as e:
        logger.error(f"Failed to fetch stats: {e}")
        return _empty


# ─── Local appointments (no Cal.com) ───────────────────────────────────────────

_APPOINTMENTS_FILE = Path(__file__).resolve().parent / "appointments.json"


def _load_local_appointments() -> list[dict]:
    try:
        if _APPOINTMENTS_FILE.exists():
            data = json.loads(_APPOINTMENTS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Failed to read local appointments: %s", exc)
    return []


def _save_local_appointments(records: list[dict]) -> None:
    try:
        _APPOINTMENTS_FILE.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write local appointments: %s", exc)


def _appointment_dashboard_row(record: dict) -> dict:
    scheduled_at = record.get("scheduled_at") or record.get("appointment_time")
    return {
        "id": record.get("id"),
        "phone_number": record.get("phone") or record.get("phone_number"),
        "caller_name": record.get("caller_name") or "",
        "appointment_time": scheduled_at,
        "summary": record.get("summary") or f"Appointment: {record.get('caller_name') or 'Caller'}",
        "booking_type": "appointment",
        "created_at": record.get("created_at"),
        "was_booked": True,
        "whatsapp_sent": bool(record.get("whatsapp_sent")),
        "notes": record.get("notes") or "",
    }


async def save_local_appointment(
    phone: str,
    caller_name: str,
    scheduled_at: str,
    notes: str = "",
    whatsapp_sent: bool = False,
) -> dict:
    normalized_phone = normalize_phone_number(phone)
    booking_id = str(uuid.uuid4())
    created_at = now_utc().isoformat()
    record = {
        "id": booking_id,
        "phone": normalized_phone,
        "caller_name": sanitize_caller_name(caller_name) or normalized_phone,
        "scheduled_at": scheduled_at,
        "notes": notes,
        "whatsapp_sent": whatsapp_sent,
        "summary": f"Appointment: {sanitize_caller_name(caller_name) or normalized_phone}",
        "created_at": created_at,
        "status": "confirmed",
    }

    local_records = _load_local_appointments()
    local_records.append(record)
    _save_local_appointments(local_records)

    supabase = None
    try:
        supabase = await get_supabase()
    except Exception as exc:
        logger.debug("save_local_appointment: supabase unavailable: %s", exc)
    if supabase:
        try:
            payload = {
                "phone": normalized_phone,
                "scheduled_at": scheduled_at,
                "provider": "manual",
                "status": "confirmed",
                "notes": notes or f"Appointment: {caller_name}",
                "metadata": {
                    "type": "appointment",
                    "caller_name": record["caller_name"],
                    "whatsapp_sent": whatsapp_sent,
                },
            }
            res = supabase.table("calendar_bookings").insert(payload).execute()
            if res.data:
                row = res.data[0] if isinstance(res.data, list) else res.data
                if row.get("id"):
                    record["id"] = str(row["id"])
        except Exception as exc:
            logger.debug("save_local_appointment supabase skipped: %s", exc)

    return {"success": True, "booking_id": record["id"], "record": _appointment_dashboard_row(record)}


async def fetch_local_appointments(limit: int = 500) -> list[dict]:
    rows = [_appointment_dashboard_row(r) for r in _load_local_appointments()]
    rows.sort(key=lambda x: str(x.get("appointment_time") or ""), reverse=True)
    return rows[:limit]


async def fetch_reserved_slot_times() -> list[str]:
    """ISO slot keys already booked (appointments + site visits)."""
    keys: list[str] = []
    for record in _load_local_appointments() + _load_local_site_visits():
        raw = record.get("scheduled_at") or ""
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            keys.append(dt.strftime("%Y-%m-%dT%H:%M"))
        except Exception:
            continue
    return keys


# ─── Site visits ───────────────────────────────────────────────────────────────

_SITE_VISITS_FILE = Path(__file__).resolve().parent / "site_visits.json"


def _load_local_site_visits() -> list[dict]:
    try:
        if _SITE_VISITS_FILE.exists():
            data = json.loads(_SITE_VISITS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Failed to read local site visits: %s", exc)
    return []


def _save_local_site_visits(records: list[dict]) -> None:
    try:
        _SITE_VISITS_FILE.write_text(
            json.dumps(records, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write local site visits: %s", exc)


def _site_visit_dashboard_row(record: dict) -> dict:
    scheduled_at = record.get("scheduled_at") or record.get("appointment_time")
    property_name = record.get("property_name") or record.get("listing_id") or "Property"
    location = record.get("location") or ""
    status = str(record.get("status") or "confirmed").lower()
    summary = f"Site visit: {property_name}"
    if location:
        summary += f" at {location}"
    return {
        "id": record.get("id"),
        "phone_number": record.get("phone") or record.get("phone_number"),
        "caller_name": record.get("caller_name") or "",
        "appointment_time": scheduled_at,
        "summary": summary,
        "booking_type": "site_visit",
        "property_name": property_name,
        "location": location,
        "listing_id": record.get("listing_id") or "",
        "created_at": record.get("created_at"),
        "status": status,
        "visit_status": status,
        "was_booked": status not in {"cancelled", "no_show"},
        "whatsapp_sent": bool(record.get("whatsapp_sent")),
        "brochure_sent": bool(record.get("brochure_sent")),
        "maps_url": record.get("maps_url") or "",
        "notes": record.get("notes") or "",
    }


async def save_site_visit(
    phone: str,
    caller_name: str,
    listing_id: str,
    property_name: str,
    location: str,
    scheduled_at: str,
    room_id: str | None = None,
    notes: str = "",
    whatsapp_sent: bool = False,
    maps_url: str = "",
    brochure_sent: bool = False,
) -> dict:
    normalized_phone = normalize_phone_number(phone)
    visit_id = str(uuid.uuid4())
    created_at = now_utc().isoformat()
    record = {
        "id": visit_id,
        "phone": normalized_phone,
        "caller_name": sanitize_caller_name(caller_name) or normalized_phone,
        "listing_id": listing_id or "",
        "property_name": property_name or listing_id or "Property",
        "location": location or "",
        "scheduled_at": scheduled_at,
        "room_id": room_id,
        "notes": notes,
        "whatsapp_sent": whatsapp_sent,
        "maps_url": maps_url,
        "brochure_sent": brochure_sent,
        "created_at": created_at,
        "status": "confirmed",
    }

    local_records = _load_local_site_visits()
    local_records.append(record)
    _save_local_site_visits(local_records)

    supabase = None
    try:
        supabase = await get_supabase()
    except Exception as exc:
        logger.debug("save_site_visit: supabase unavailable: %s", exc)
    if supabase:
        try:
            payload = {
                "phone": normalized_phone,
                "scheduled_at": scheduled_at,
                "provider": "manual",
                "status": "confirmed",
                "notes": notes or f"Site visit: {property_name}",
                "metadata": {
                    "type": "site_visit",
                    "property_name": property_name,
                    "location": location,
                    "caller_name": record["caller_name"],
                    "room_id": room_id,
                    "whatsapp_sent": whatsapp_sent,
                    "brochure_sent": brochure_sent,
                    "maps_url": maps_url or None,
                    "listing_id": listing_id or None,
                },
            }
            res = supabase.table("calendar_bookings").insert(payload).execute()
            if res.data:
                row = res.data[0] if isinstance(res.data, list) else res.data
                if row.get("id"):
                    record["id"] = str(row["id"])
        except Exception as exc:
            err = str(exc).lower()
            if "calendar_bookings" in err or "pgrst" in err:
                logger.info("calendar_bookings unavailable; using local site_visits.json")
            else:
                logger.warning("Failed to save site visit to Supabase: %s", exc)

    dashboard_row = _site_visit_dashboard_row(record)
    return {"success": True, "record": dashboard_row, "message": "Site visit saved"}


async def fetch_site_visits(limit: int = 500) -> list[dict]:
    visits: list[dict] = []
    seen: set[str] = set()

    supabase = await get_supabase()
    if supabase:
        try:
            res = (
                supabase.table("calendar_bookings")
                .select("id, phone, listing_id, scheduled_at, status, notes, metadata, created_at")
                .order("scheduled_at", desc=True)
                .limit(limit)
                .execute()
            )
            for row in res.data or []:
                metadata = row.get("metadata") or {}
                if metadata.get("type") != "site_visit":
                    continue
                record = {
                    "id": str(row.get("id")),
                    "phone": row.get("phone"),
                    "caller_name": metadata.get("caller_name") or "",
                    "listing_id": row.get("listing_id") or metadata.get("listing_id") or "",
                    "property_name": metadata.get("property_name") or "",
                    "location": metadata.get("location") or "",
                    "scheduled_at": row.get("scheduled_at"),
                    "notes": row.get("notes") or "",
                    "whatsapp_sent": bool(metadata.get("whatsapp_sent")),
                    "brochure_sent": bool(metadata.get("brochure_sent")),
                    "maps_url": metadata.get("maps_url") or "",
                    "created_at": row.get("created_at"),
                    "status": row.get("status") or "confirmed",
                }
                key = f"{record['phone']}:{record['scheduled_at']}"
                if key in seen:
                    continue
                seen.add(key)
                visits.append(_site_visit_dashboard_row(record))
        except Exception as exc:
            logger.debug("fetch_site_visits supabase failed: %s", exc)

    for record in _load_local_site_visits():
        key = f"{record.get('phone')}:{record.get('scheduled_at')}"
        if key in seen:
            continue
        seen.add(key)
        visits.append(_site_visit_dashboard_row(record))

    visits.sort(key=lambda x: str(x.get("appointment_time") or ""), reverse=True)
    return visits[:limit]


_VALID_SITE_VISIT_STATUSES = frozenset({"confirmed", "completed", "cancelled", "no_show"})


async def update_site_visit_status(visit_id: str, status: str) -> dict:
    """Update site visit status in local JSON and Supabase calendar_bookings."""
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _VALID_SITE_VISIT_STATUSES:
        return {
            "success": False,
            "message": f"Invalid status. Use one of: {', '.join(sorted(_VALID_SITE_VISIT_STATUSES))}",
        }

    visit_key = str(visit_id or "").strip()
    if not visit_key:
        return {"success": False, "message": "Visit id is required"}

    updated_record: dict | None = None
    local_records = _load_local_site_visits()
    for record in local_records:
        if str(record.get("id")) == visit_key:
            record["status"] = normalized_status
            updated_record = dict(record)
            break
    if updated_record:
        _save_local_site_visits(local_records)

    supabase = await get_supabase()
    if supabase:
        try:
            res = (
                supabase.table("calendar_bookings")
                .update({"status": normalized_status})
                .eq("id", visit_key)
                .execute()
            )
            row = (res.data or [None])[0]
            if row:
                metadata = row.get("metadata") or {}
                if metadata.get("type") == "site_visit":
                    updated_record = {
                        "id": str(row.get("id")),
                        "phone": row.get("phone"),
                        "caller_name": metadata.get("caller_name") or "",
                        "listing_id": row.get("listing_id") or metadata.get("listing_id") or "",
                        "property_name": metadata.get("property_name") or "",
                        "location": metadata.get("location") or "",
                        "scheduled_at": row.get("scheduled_at"),
                        "notes": row.get("notes") or "",
                        "whatsapp_sent": bool(metadata.get("whatsapp_sent")),
                        "brochure_sent": bool(metadata.get("brochure_sent")),
                        "maps_url": metadata.get("maps_url") or "",
                        "created_at": row.get("created_at"),
                        "status": normalized_status,
                    }
        except Exception as exc:
            logger.warning("update_site_visit_status supabase failed: %s", exc)

    if not updated_record:
        return {"success": False, "message": "Site visit not found"}

    dashboard_row = _site_visit_dashboard_row(updated_record)
    return {"success": True, "record": dashboard_row, "message": f"Status updated to {normalized_status}"}


# ─── Dashboard mutations ───────────────────────────────────────────────────────

async def get_call_log(call_id: str) -> dict | None:
    """Fetch a single call_logs row by id."""
    supabase = await get_supabase()
    if not supabase or not call_id:
        return None
    for attempt in range(_MAX_RETRIES):
        try:
            res = (
                supabase.table("call_logs")
                .select("*")
                .eq("id", str(call_id))
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0] if rows else None
        except Exception as e:
            if _is_retryable(str(e)) and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error("get_call_log failed: %s", e)
            return None
    return None


async def update_call_log(call_id: str, updates: dict) -> bool:
    """Update a call_logs row by id."""
    supabase = await get_supabase()
    if not supabase or not call_id:
        return False
    for attempt in range(_MAX_RETRIES):
        try:
            res = (
                supabase.table("call_logs")
                .update(updates)
                .eq("id", str(call_id))
                .execute()
            )
            return bool(res.data)
        except Exception as e:
            if _is_retryable(str(e)) and attempt < _MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            logger.error("update_call_log failed: %s", e)
            return False
    return False


async def insert_lead_action(
    *,
    call_id: str,
    action: str,
    performed_by: str,
    notes: str = "",
) -> bool:
    """Record a lead CTA action (best-effort if table missing)."""
    supabase = await get_supabase()
    if not supabase:
        return False
    payload = {
        "call_id": str(call_id),
        "action": action,
        "performed_at": now_utc().isoformat(),
        "performed_by": performed_by,
        "notes": notes,
    }
    try:
        supabase.table("lead_actions").insert(payload).execute()
        return True
    except Exception as e:
        logger.warning("insert_lead_action failed (table may not exist): %s", e)
        return False
