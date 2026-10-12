"""Persistent version history for agent configuration snapshots."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
VERSIONS_DIR = BASE_DIR / "data"
VERSIONS_FILE = VERSIONS_DIR / "agent_config_versions.json"
MAX_VERSIONS_PER_AGENT = 50


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_store() -> dict[str, list[dict[str, Any]]]:
    if not VERSIONS_FILE.exists():
        return {}
    try:
        raw = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): list(v or []) for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _save_store(store: dict[str, list[dict[str, Any]]]) -> None:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    VERSIONS_FILE.write_text(
        json.dumps(store, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _strip_runtime_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(snapshot or {})
    cleaned.pop("merged_system_prompt", None)
    return cleaned


def list_versions(agent_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    store = _load_store()
    rows = list(store.get(str(agent_id), []))
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    out: list[dict[str, Any]] = []
    for row in rows[: max(limit, 1)]:
        out.append(
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "source": row.get("source"),
                "prompt": row.get("prompt"),
                "summary": row.get("summary"),
                "edited_by": row.get("edited_by"),
            }
        )
    return out


def get_version(agent_id: str, version_id: str) -> dict[str, Any] | None:
    store = _load_store()
    for row in store.get(str(agent_id), []):
        if row.get("id") == version_id:
            return row
    return None


def save_version(
    agent_id: str,
    snapshot: dict[str, Any],
    *,
    source: str = "manual",
    prompt: str | None = None,
    summary: str | None = None,
    edited_by: str | None = None,
) -> dict[str, Any]:
    store = _load_store()
    key = str(agent_id)
    entry = {
        "id": f"v_{uuid.uuid4().hex[:12]}",
        "created_at": _utc_now(),
        "source": source,
        "prompt": (prompt or "").strip() or None,
        "summary": (summary or "").strip() or None,
        "edited_by": edited_by,
        "snapshot": _strip_runtime_fields(snapshot),
    }
    rows = store.setdefault(key, [])
    rows.append(entry)
    if len(rows) > MAX_VERSIONS_PER_AGENT:
        store[key] = rows[-MAX_VERSIONS_PER_AGENT:]
    _save_store(store)
    return entry


def prune_agent_versions(agent_id: str, keep: int = MAX_VERSIONS_PER_AGENT) -> int:
    store = _load_store()
    key = str(agent_id)
    rows = store.get(key, [])
    if len(rows) <= keep:
        return 0
    removed = len(rows) - keep
    store[key] = rows[-keep:]
    _save_store(store)
    return removed