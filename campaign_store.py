"""Persist outbound campaign dispatches and track per-contact outcomes."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phone_utils import normalize_phone_number
from tenancy import DEFAULT_ORG_ID, get_request_org_id


def _campaign_matches_org(data: dict) -> bool:
    org_id = get_request_org_id()
    if not org_id:
        return True
    campaign_org = data.get("org_id") or DEFAULT_ORG_ID
    return campaign_org == org_id

logger = logging.getLogger("campaign_store")

CAMPAIGNS_DIR = Path("data/campaigns")

NOT_LIFTED_STATUSES = frozenset({"not_lifted", "no_answer", "busy", "failed", "cancelled", "error"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _campaign_path(campaign_id: str) -> Path:
    return CAMPAIGNS_DIR / f"{campaign_id}.json"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Failed to read campaign %s: %s", path, exc)
        return None


def _save(data: dict) -> None:
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)
    path = _campaign_path(data["campaign_id"])
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _contact_index(contacts: list[dict], phone: str) -> int | None:
    target = normalize_phone_number(phone)
    for i, row in enumerate(contacts):
        if normalize_phone_number(row.get("phone", "")) == target:
            return i
    return None


def create_campaign(
    *,
    campaign_id: str,
    campaign_name: str,
    agent_id: str | None,
    phones: list[str],
    dispatch_results: list[dict] | None = None,
    window: dict | None = None,
) -> dict:
    """Create or replace a campaign record from a bulk dispatch."""
    dispatch_results = dispatch_results or []
    result_by_phone = {
        normalize_phone_number(r.get("phone", "")): r for r in dispatch_results if r.get("phone")
    }

    contacts: list[dict] = []
    for raw in phones:
        phone = normalize_phone_number(raw)
        if not phone:
            continue
        dispatch = result_by_phone.get(phone, {})
        status = "dispatched" if dispatch.get("status") == "ok" else "error"
        contacts.append(
            {
                "phone": phone,
                "status": status,
                "dispatch_id": dispatch.get("dispatch_id"),
                "message": dispatch.get("message"),
                "updated_at": _utc_now(),
            }
        )

    data = {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name or "",
        "agent_id": agent_id,
        "org_id": get_request_org_id(),
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "window": window or {},
        "contacts": contacts,
        "stats": _compute_stats(contacts),
    }
    _save(data)
    return data


def list_campaigns(limit: int = 50) -> list[dict]:
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(CAMPAIGNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows: list[dict] = []
    for path in paths[:limit]:
        data = _load(path)
        if not data:
            continue
        if not _campaign_matches_org(data):
            continue
        stats = data.get("stats") or _compute_stats(data.get("contacts") or [])
        rows.append(
            {
                "campaign_id": data.get("campaign_id"),
                "campaign_name": data.get("campaign_name") or "",
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "agent_id": data.get("agent_id"),
                "total": stats.get("total", 0),
                "dispatched": stats.get("dispatched", 0),
                "answered": stats.get("answered", 0),
                "not_lifted": stats.get("not_lifted", 0),
                "failed": stats.get("failed", 0),
            }
        )
    return rows


def get_campaign(campaign_id: str) -> dict | None:
    data = _load(_campaign_path(campaign_id))
    if not data or not _campaign_matches_org(data):
        return None
    return data


def update_contact_status(
    campaign_id: str,
    phone: str,
    status: str,
    *,
    dispatch_id: str | None = None,
    message: str | None = None,
    call_log_id: str | None = None,
    duration_seconds: int | None = None,
) -> dict | None:
    data = get_campaign(campaign_id)
    if not data:
        return None

    contacts = data.get("contacts") or []
    idx = _contact_index(contacts, phone)
    if idx is None:
        contacts.append(
            {
                "phone": normalize_phone_number(phone),
                "status": status,
                "dispatch_id": dispatch_id,
                "message": message,
                "call_log_id": call_log_id,
                "duration_seconds": duration_seconds,
                "updated_at": _utc_now(),
            }
        )
    else:
        row = contacts[idx]
        row["status"] = status
        row["updated_at"] = _utc_now()
        if dispatch_id:
            row["dispatch_id"] = dispatch_id
        if message:
            row["message"] = message
        if call_log_id:
            row["call_log_id"] = call_log_id
        if duration_seconds is not None:
            row["duration_seconds"] = duration_seconds
        contacts[idx] = row

    data["contacts"] = contacts
    data["updated_at"] = _utc_now()
    data["stats"] = _compute_stats(contacts)
    _save(data)
    return data


def get_not_lifted_contacts(campaign_id: str) -> list[dict]:
    data = get_campaign(campaign_id)
    if not data:
        return []
    return [c for c in (data.get("contacts") or []) if c.get("status") in NOT_LIFTED_STATUSES]


def export_not_lifted_csv(campaign_id: str) -> str:
    data = get_campaign(campaign_id)
    if not data:
        return ""

    rows = get_not_lifted_contacts(campaign_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "phone",
            "status",
            "message",
            "dispatch_id",
            "call_log_id",
            "duration_seconds",
            "updated_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("phone", ""),
                row.get("status", ""),
                row.get("message", ""),
                row.get("dispatch_id", ""),
                row.get("call_log_id", ""),
                row.get("duration_seconds", ""),
                row.get("updated_at", ""),
            ]
        )
    return buf.getvalue()


def _compute_stats(contacts: list[dict]) -> dict[str, int]:
    stats = {
        "total": len(contacts),
        "dispatched": 0,
        "answered": 0,
        "not_lifted": 0,
        "failed": 0,
        "pending": 0,
    }
    for row in contacts:
        status = row.get("status") or "pending"
        if status == "answered":
            stats["answered"] += 1
        elif status in NOT_LIFTED_STATUSES:
            stats["not_lifted"] += 1
            if status in {"failed", "error"}:
                stats["failed"] += 1
        elif status == "dispatched":
            stats["dispatched"] += 1
        else:
            stats["pending"] += 1
    return stats