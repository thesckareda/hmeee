import re
from datetime_utils import IST, parse_iso

BOOKING_CONFIRMED_SIGNALS = frozenset({
    "confirm", "already_signed_up", "booked", "appointment_confirmed",
    "booking_success", "scheduled", "appointment_set",
})

def is_booking_confirmed(row: dict) -> bool:
    if row.get("was_booked"):
        return True
    summary = str(row.get("summary") or "").lower()
    for signal in BOOKING_CONFIRMED_SIGNALS:
        if signal in summary:
            return True
    return False

_NAME_PLACEHOLDERS = frozenset({
    "unknown caller", "unknown", "anonymous", "caller", "user",
    "test", "guest", "na", "n/a", "none", "null",
})

def sanitize_caller_name(name: str) -> str:
    cleaned = str(name or "").strip()
    if cleaned.lower() in _NAME_PLACEHOLDERS:
        return ""
    cleaned = re.sub(r"[^\w\s\-]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:64]

def normalize_appointment_time_ist(dt_str: str) -> str:
    raw = str(dt_str or "").strip()
    if not raw:
        return ""
    try:
        dt = parse_iso(raw)
        ist_dt = dt.astimezone(IST)
        return ist_dt.isoformat(timespec="seconds")
    except Exception:
        return raw

def extract_transcript(messages) -> list[dict]:
    entries = []
    for msg in messages:
        role = str(getattr(msg, "role", "")).lower()
        if role not in ("user", "assistant"):
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(str(c) for c in content if isinstance(c, str))
        tool_calls = []
        if role == "assistant":
            for tc in getattr(msg, "tool_calls", []) or []:
                tool_calls.append({
                    "name": str(getattr(tc, "name", "unknown")),
                    "result": str(getattr(tc, "result", "")),
                })
        entries.append({
            "role": role,
            "content": content,
            "timestamp": str(getattr(msg, "timestamp", "")),
            "tool_calls": tool_calls,
        })
    return entries

CALLER_NAME_PLACEHOLDERS = _NAME_PLACEHOLDERS
