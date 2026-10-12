"""Per-phone call history for fast pre-call context injection."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from phone_utils import is_valid_phone_number, normalize_phone_number, sanitize_caller_name
from re_voice_memory.constants import MEMORY_DIR
from voice_memory.fs import atomic_write_text

logger = logging.getLogger(__name__)

_MAX_STORED_ENTRIES = 10
_DEFAULT_INJECT_LIMIT = 3
_MAX_SUMMARY_CHARS = 400

_phone_locks: dict[str, threading.Lock] = {}
_phone_locks_meta = threading.Lock()


def _get_phone_lock(phone: str) -> threading.Lock:
    lock = _phone_locks.get(phone)
    if lock is not None:
        return lock
    with _phone_locks_meta:
        lock = _phone_locks.get(phone)
        if lock is None:
            lock = threading.Lock()
            _phone_locks[phone] = lock
        return lock


@dataclass
class CallerHistoryEntry:
    caller_name: str
    phone: str
    summary: str
    call_at: str


def _history_path(phone: str) -> Path:
    return MEMORY_DIR / "leads" / phone / "caller_history.json"


def _normalize_key(phone: str) -> str:
    return normalize_phone_number(phone)


def _parse_call_at(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_call_at(value: str) -> str:
    dt = _parse_call_at(value)
    if dt is None:
        return value or "unknown time"
    try:
        from datetime_utils import to_ist

        ist = to_ist(dt)
        return ist.strftime("%d %b %Y, %I:%M %p IST")
    except Exception:
        return dt.isoformat()


def _human_delta(value: str) -> str:
    """IQ #17 — render a call timestamp as a human time anchor ('2 days back')."""
    dt = _parse_call_at(value)
    if dt is None:
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    seconds = max(0.0, (now - dt).total_seconds())
    if now.date() == dt.date():
        if seconds < 300:
            return "just now"
        if dt.hour < 12:
            return "this morning"
        return "earlier today"
    days = (now.date() - dt.date()).days
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days back"
    if days < 30:
        weeks = days // 7
        return "last week" if weeks == 1 else f"{weeks} weeks back"
    months = days // 30
    return "last month" if months == 1 else f"{months} months back"


def _trim_summary(summary: str) -> str:
    text = " ".join((summary or "").split())
    if len(text) <= _MAX_SUMMARY_CHARS:
        return text
    return text[: _MAX_SUMMARY_CHARS - 3].rstrip() + "..."


def _read_history_file(phone: str) -> list[dict[str, Any]]:
    path = _history_path(phone)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[CALLER-HISTORY] Corrupt history file for %s", phone)
        return []
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict)]


def load_caller_history(phone: str, *, limit: int = _DEFAULT_INJECT_LIMIT) -> list[CallerHistoryEntry]:
    """Load recent call history for a phone number (newest first)."""
    key = _normalize_key(phone)
    if not key or key in ("unknown", "demo") or not is_valid_phone_number(key):
        return []

    rows = _read_history_file(key)
    entries: list[CallerHistoryEntry] = []
    for row in reversed(rows):
        summary = _trim_summary(str(row.get("summary", "")))
        if not summary:
            continue
        entries.append(
            CallerHistoryEntry(
                caller_name=str(row.get("caller_name", "")).strip(),
                phone=str(row.get("phone", key)).strip() or key,
                summary=summary,
                call_at=str(row.get("call_at", "")).strip(),
            )
        )
        if len(entries) >= max(1, limit):
            break
    return entries


def append_call_history(
    phone: str,
    *,
    caller_name: str,
    summary: str,
    call_at: str,
) -> None:
    """Append one post-call record for this phone (sync, atomic write)."""
    key = _normalize_key(phone)
    if not key or key in ("unknown", "demo") or not is_valid_phone_number(key):
        return

    clean_summary = _trim_summary(summary)
    if not clean_summary:
        logger.debug("[CALLER-HISTORY] Skipping save — empty summary for %s", key)
        return

    clean_name = sanitize_caller_name(caller_name) or "Unknown"
    clean_at = (call_at or "").strip()
    if not clean_at:
        clean_at = datetime.now().astimezone().isoformat()

    entry = {
        "caller_name": clean_name,
        "phone": key,
        "summary": clean_summary,
        "call_at": clean_at,
    }

    lock = _get_phone_lock(key)
    with lock:
        rows = _read_history_file(key)
        rows.append(entry)
        rows = rows[-_MAX_STORED_ENTRIES:]
        payload = {"phone": key, "entries": rows}
        atomic_write_text(_history_path(key), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    logger.info(
        "[CALLER-HISTORY] Saved call for %s (%s) — %s",
        key,
        clean_name,
        clean_summary[:80],
    )


def format_history_for_injection(
    phone: str,
    caller_name: str,
    entries: list[CallerHistoryEntry],
) -> str:
    """Compact system block for prewarm / first-turn context."""
    if not entries:
        return ""

    key = _normalize_key(phone) or phone
    name = sanitize_caller_name(caller_name)
    if not name:
        name = sanitize_caller_name(entries[0].caller_name) or "the caller"

    lines = [
        "[PRIOR CALL HISTORY — same phone number]",
        f"Caller name: {name}",
        f"Phone (lookup key): {key}",
    ]
    for entry in entries:
        when = _format_call_at(entry.call_at)
        delta = _human_delta(entry.call_at)
        anchor = f"{delta} ({when})" if delta else when
        lines.append(f"• {anchor} — {entry.summary}")

    lines.append(
        "Use this history naturally. Do not say you are reading logs or prior call records."
    )
    lines.append(
        "When referencing a prior call, anchor it in TIME using the delta above — "
        "e.g. '2 din pehle wali call mein aapne … bola tha' — never vaguely ('as discussed')."
    )
    return "\n".join(lines)