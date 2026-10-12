"""
Caller-memory administration — the "Remember the caller" button.

One config key controls the whole memory system:

    remember_caller_enabled: true   → engine remembers callers across calls
                                      ("meeru yesterday call chesaru kada…")
    remember_caller_enabled: false  → nothing is read OR written; every call
                                      is treated as a fresh caller

The memory container is fully separable:
  • Files:  RE_MEMORY_DIR/leads/<phone>/  (profile, history, objections, graph)
  • Redis:  re_voice:lead:<phone>* keys + the lead hash
forget_caller() wipes both — the "remove" button.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from re_voice_memory.constants import MEMORY_DIR

logger = logging.getLogger(__name__)


def remember_caller_enabled(config: dict | None) -> bool:
    """The button. Default ON — memory is the product's differentiator."""
    return bool((config or {}).get("remember_caller_enabled", True))


async def forget_caller(phone: str) -> dict[str, Any]:
    """Erase EVERYTHING the engine remembers about one phone number.

    Removes the file container (profile, call history, objection log,
    property graph, call summaries) and all Redis/SQLite hot keys.
    Irreversible. Returns a summary of what was deleted.
    """
    result: dict[str, Any] = {"phone": phone, "files_deleted": False, "kv_keys_deleted": 0}
    if not phone or phone in ("unknown", "demo"):
        result["error"] = "invalid phone"
        return result

    lead_dir = MEMORY_DIR / "leads" / phone
    if lead_dir.is_dir():
        try:
            await asyncio.to_thread(shutil.rmtree, lead_dir)
            result["files_deleted"] = True
        except OSError as exc:
            result["error"] = f"file container: {exc}"

    try:
        from re_voice_memory.kv_store import delete_lead_keys

        result["kv_keys_deleted"] = await delete_lead_keys(phone)
    except Exception as exc:
        result.setdefault("error", f"kv: {exc}")

    logger.info("[MEMORY-ADMIN] Forgot caller %s: %s", phone, result)
    return result


def memory_container_summary(phone: str) -> dict[str, Any]:
    """What do we currently remember about this caller? (dashboard inspection)"""
    lead_dir = MEMORY_DIR / "leads" / phone
    files = []
    if lead_dir.is_dir():
        files = sorted(str(p.relative_to(lead_dir)) for p in lead_dir.rglob("*") if p.is_file())
    return {
        "phone": phone,
        "container": str(lead_dir),
        "exists": lead_dir.is_dir(),
        "files": files,
    }
