"""Postgres access for WhatsApp chat history (conversations + messages)."""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from db import get_supabase_admin
from phone_utils import normalize_phone_number
from tenancy import DEFAULT_ORG_ID, apply_org_filter, get_request_org_id

logger = logging.getLogger("whatsapp_db")

PAGE_SIZE_CONVERSATIONS = 30
PAGE_SIZE_MESSAGES = 40

CHAT_MIGRATION_HINT = (
    "Chat history tables are missing — run sql/supabase_migration_v8_whatsapp_chat.sql "
    "in the Supabase SQL Editor, then retry."
)


def _resolve_org_id(org_id: str | None = None) -> str:
    return org_id or get_request_org_id() or DEFAULT_ORG_ID


def _is_chat_schema_missing(exc: Exception) -> bool:
    text = str(exc).lower()
    return "pgrst205" in text or "whatsapp_conversations" in text or "whatsapp_messages" in text


def _chat_schema_error_result() -> dict[str, Any]:
    return {
        "items": [],
        "next_cursor": None,
        "migration_required": True,
        "detail": CHAT_MIGRATION_HINT,
    }


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def get_whatsapp_number_for_org(number_id: str, org_id: str | None = None) -> dict | None:
    """Resolve whatsapp_sessions row — must belong to org."""
    sb = await get_supabase_admin()
    if not sb:
        return None
    oid = _resolve_org_id(org_id)
    try:
        res = (
            sb.table("whatsapp_sessions")
            .select("id, org_id, phone_number, status, connected_at")
            .eq("id", number_id)
            .eq("org_id", oid)
            .maybe_single()
            .execute()
        )
        return res.data
    except APIError as exc:
        if _is_chat_schema_missing(exc):
            return None
        raise


async def list_whatsapp_numbers(org_id: str | None = None) -> list[dict]:
    sb = await get_supabase_admin()
    if not sb:
        return []
    query = sb.table("whatsapp_sessions").select(
        "id, org_id, phone_number, status, connected_at, updated_at"
    )
    query = apply_org_filter(query)
    res = query.order("updated_at", desc=True).execute()
    return res.data or []


async def list_conversations(
    number_id: str,
    *,
    search: str | None = None,
    cursor: str | None = None,
    limit: int = PAGE_SIZE_CONVERSATIONS,
) -> dict[str, Any]:
    sb = await get_supabase_admin()
    if not sb:
        return {"items": [], "next_cursor": None}

    session = await get_whatsapp_number_for_org(number_id)
    if not session:
        return {"items": [], "next_cursor": None, "not_found": True}

    query = (
        sb.table("whatsapp_conversations")
        .select(
            "id, org_id, whatsapp_number_id, contact_number, contact_name, "
            "last_message_preview, last_message_at, unread_count, created_at"
        )
        .eq("whatsapp_number_id", number_id)
    )
    query = apply_org_filter(query)

    if search:
        q = search.strip()
        if q:
            query = query.ilike("contact_number", f"%{q}%")

    cur = _decode_cursor(cursor)
    if cur and cur.get("last_message_at"):
        query = query.lt("last_message_at", cur["last_message_at"])

    try:
        res = query.order("last_message_at", desc=True).limit(limit + 1).execute()
    except APIError as exc:
        if _is_chat_schema_missing(exc):
            out = _chat_schema_error_result()
            out["whatsapp_number"] = session.get("phone_number")
            out["session_status"] = session.get("status")
            return out
        raise

    rows = res.data or []
    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _encode_cursor(
            {"last_message_at": last.get("last_message_at"), "id": last.get("id")}
        )

    return {
        "items": items,
        "next_cursor": next_cursor,
        "whatsapp_number": session.get("phone_number"),
        "session_status": session.get("status"),
    }


async def list_messages(
    number_id: str,
    conversation_id: str,
    *,
    cursor: str | None = None,
    limit: int = PAGE_SIZE_MESSAGES,
) -> dict[str, Any]:
    sb = await get_supabase_admin()
    if not sb:
        return {"items": [], "next_cursor": None}

    try:
        conv_res = (
            sb.table("whatsapp_conversations")
            .select("id, org_id, whatsapp_number_id, contact_number, contact_name, unread_count")
            .eq("id", conversation_id)
            .eq("whatsapp_number_id", number_id)
            .maybe_single()
            .execute()
        )
    except APIError as exc:
        if _is_chat_schema_missing(exc):
            return _chat_schema_error_result()
        raise

    conv = conv_res.data
    if not conv:
        return {"items": [], "next_cursor": None, "not_found": True}

    oid = _resolve_org_id()
    if conv.get("org_id") != oid:
        return {"items": [], "next_cursor": None, "forbidden": True}

    query = (
        sb.table("whatsapp_messages")
        .select(
            "id, conversation_id, direction, message_type, content, media_url, "
            "sent_by, status, wa_message_id, created_at"
        )
        .eq("conversation_id", conversation_id)
    )

    cur = _decode_cursor(cursor)
    if cur and cur.get("created_at"):
        query = query.lt("created_at", cur["created_at"])

    try:
        res = query.order("created_at", desc=True).limit(limit + 1).execute()
    except APIError as exc:
        if _is_chat_schema_missing(exc):
            return _chat_schema_error_result()
        raise
    rows = res.data or []
    has_more = len(rows) > limit
    page = rows[:limit]
    # Thread UI renders oldest-at-bottom — reverse page to chronological within page
    items = list(reversed(page))

    next_cursor = None
    if has_more:
        anchor = rows[limit]
        next_cursor = _encode_cursor(
            {"created_at": anchor.get("created_at"), "id": anchor.get("id")}
        )

    return {
        "items": items,
        "next_cursor": next_cursor,
        "conversation": conv,
    }


async def mark_conversation_read(number_id: str, conversation_id: str) -> bool:
    sb = await get_supabase_admin()
    if not sb:
        return False
    res = (
        sb.table("whatsapp_conversations")
        .update({"unread_count": 0, "updated_at": datetime.utcnow().isoformat()})
        .eq("id", conversation_id)
        .eq("whatsapp_number_id", number_id)
        .execute()
    )
    return bool(res.data)


async def get_conversation_contact_jid(number_id: str, conversation_id: str) -> str | None:
    sb = await get_supabase_admin()
    if not sb:
        return None
    res = (
        sb.table("whatsapp_conversations")
        .select("contact_number")
        .eq("id", conversation_id)
        .eq("whatsapp_number_id", number_id)
        .maybe_single()
        .execute()
    )
    if not res.data:
        return None
    phone = normalize_phone_number(res.data.get("contact_number"))
    digits = phone.lstrip("+").replace(" ", "")
    return f"{digits}@s.whatsapp.net" if digits else None


async def conversation_exists_for_contact(number_id: str, contact_number: str) -> bool:
    sb = await get_supabase_admin()
    if not sb:
        return False
    res = (
        sb.table("whatsapp_conversations")
        .select("id")
        .eq("whatsapp_number_id", number_id)
        .eq("contact_number", contact_number)
        .limit(1)
        .execute()
    )
    return bool(res.data)


def _digits_from_wa_id(wa_id: str) -> str:
    raw = (wa_id or "").split("@")[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        return digits
    return normalize_phone_number(wa_id).lstrip("+").replace(" ", "")


async def load_whatsapp_context_for_contact(contact_number: str, *, limit: int = 8) -> str:
    """Summarize recent WhatsApp messages for turn_context_injection / caller history."""
    sb = await get_supabase_admin()
    if not sb:
        return ""
    phone = _digits_from_wa_id(contact_number)
    if not phone:
        return ""

    conv_res = (
        sb.table("whatsapp_conversations")
        .select("id")
        .eq("contact_number", phone)
        .order("last_message_at", desc=True)
        .limit(1)
        .execute()
    )
    if not conv_res.data:
        return ""

    conv_id = conv_res.data[0]["id"]
    msg_res = (
        sb.table("whatsapp_messages")
        .select("direction, content, sent_by, created_at")
        .eq("conversation_id", conv_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    lines = []
    for row in reversed(msg_res.data or []):
        content = (row.get("content") or "").strip()
        if not content:
            continue
        who = "Customer" if row.get("direction") == "inbound" else (
            "AI" if row.get("sent_by") == "assistant" else "Agent"
        )
        lines.append(f"[{who}] {content}")
    if not lines:
        return ""
    return "Recent WhatsApp thread:\n" + "\n".join(lines)