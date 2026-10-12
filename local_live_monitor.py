"""
Local live-call monitor for laptop testing — no Supabase.

Writes to data/live_test/:
  active.json          — currently active calls
  calls/<room>.jsonl   — per-call transcript + event stream

Enable: LOCAL_LIVE_MONITOR=1 (default on). Set LOCAL_LIVE_MONITOR=0 to disable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

_ROOT = Path(__file__).resolve().parent / "data" / "live_test"
_CALLS_DIR = _ROOT / "calls"
_ACTIVE_FILE = _ROOT / "active.json"
_AGENT_LOG = Path(__file__).resolve().parent / "data" / "agent_logs" / "agent_dev.log"

_LOCK = threading.Lock()
_LOG_FILTER = re.compile(
    r"\[(ROOM|TRANSCRIPT|IDENTITY|KNOWLEDGE-PACK|ACTIVE-CALL|TTS|LLM|DIAL|SHUTDOWN|CALL-ENDING)\]"
)


def is_enabled() -> bool:
    return os.getenv("LOCAL_LIVE_MONITOR", "1").strip().lower() not in ("0", "false", "no")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_room(room_id: str) -> str:
    return re.sub(r"[^\w\-.]", "_", (room_id or "unknown").strip())[:120] or "unknown"


def _ensure_dirs() -> None:
    _ROOT.mkdir(parents=True, exist_ok=True)
    _CALLS_DIR.mkdir(parents=True, exist_ok=True)


def _read_active() -> dict[str, Any]:
    if not _ACTIVE_FILE.is_file():
        return {}
    try:
        data = json.loads(_ACTIVE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_active(data: dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = _ACTIVE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(_ACTIVE_FILE)


def call_started(
    room_id: str,
    *,
    phone: str = "",
    caller_name: str = "",
    preset: str = "",
    direction: str = "inbound",
    extra: dict[str, Any] | None = None,
) -> None:
    if not is_enabled():
        return
    key = _safe_room(room_id)
    payload = {
        "room_id": room_id,
        "phone": phone,
        "caller_name": caller_name,
        "preset": preset,
        "direction": direction,
        "status": "active",
        "started_at": _now_iso(),
        "last_event_at": _now_iso(),
        **(extra or {}),
    }
    with _LOCK:
        active = _read_active()
        active[key] = payload
        _write_active(active)
    log_event(room_id, "system", "Call started", event="call_started", meta=payload)


def call_ended(room_id: str, *, reason: str = "completed", extra: dict[str, Any] | None = None) -> None:
    if not is_enabled():
        return
    key = _safe_room(room_id)
    log_event(
        room_id,
        "system",
        f"Call ended ({reason})",
        event="call_ended",
        meta={"reason": reason, **(extra or {})},
    )
    with _LOCK:
        active = _read_active()
        active.pop(key, None)
        _write_active(active)


def log_event(
    room_id: str,
    role: str,
    content: str,
    *,
    event: str = "transcript",
    meta: dict[str, Any] | None = None,
) -> None:
    """Append one line to the room JSONL. Sync, ~sub-ms — safe on the voice hot path."""
    if not is_enabled() or not room_id:
        return
    text = (content or "").strip()
    if not text and event == "transcript":
        return

    row = {
        "ts": _now_iso(),
        "room_id": room_id,
        "role": role,
        "event": event,
        "content": text,
        "meta": meta or {},
    }
    path = _CALLS_DIR / f"{_safe_room(room_id)}.jsonl"
    line = json.dumps(row, ensure_ascii=False) + "\n"

    with _LOCK:
        _ensure_dirs()
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        active = _read_active()
        key = _safe_room(room_id)
        if key in active:
            active[key]["last_event_at"] = row["ts"]
            if role == "user" and text:
                active[key]["last_user_line"] = text[:200]
            elif role == "assistant" and text:
                active[key]["last_assistant_line"] = text[:200]
            _write_active(active)


def list_active_calls() -> list[dict[str, Any]]:
    if not is_enabled():
        return []
    with _LOCK:
        active = _read_active()
    rows = list(active.values())
    rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return rows


def get_call_events(room_id: str, *, after: int = 0, limit: int = 500) -> dict[str, Any]:
    if not is_enabled():
        return {"room_id": room_id, "events": [], "total": 0, "offset": after}
    path = _CALLS_DIR / f"{_safe_room(room_id)}.jsonl"
    if not path.is_file():
        return {"room_id": room_id, "events": [], "total": 0, "offset": after}

    events: list[dict[str, Any]] = []
    with _LOCK:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if i < after:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break

    total = 0
    with _LOCK:
        total = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())

    return {
        "room_id": room_id,
        "events": events,
        "total": total,
        "offset": after,
        "next_offset": after + len(events),
    }


def tail_agent_log(*, lines: int = 80, room_id: str | None = None) -> list[str]:
    """Tail agent_dev.log, optionally filtered to a room and monitor-relevant tags."""
    if not _AGENT_LOG.is_file():
        return []
    n = max(10, min(int(lines), 500))
    try:
        raw_lines = _AGENT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    picked: list[str] = []
    for line in reversed(raw_lines):
        if room_id and room_id not in line:
            continue
        if not _LOG_FILTER.search(line):
            continue
        picked.append(line)
        if len(picked) >= n:
            break
    picked.reverse()
    return picked


def list_recent_call_files(*, limit: int = 20) -> list[dict[str, Any]]:
    if not _CALLS_DIR.is_dir():
        return []
    files = sorted(_CALLS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[: max(1, limit)]:
        out.append({
            "room_id": path.stem,
            "file": str(path.name),
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
        })
    return out