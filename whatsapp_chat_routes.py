"""WhatsApp Chat History — org-scoped inbox API + SSE realtime."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import require_auth_context
from phone_utils import normalize_phone_number
from tenancy import AuthContext, set_request_org
from whatsapp_db import (
    conversation_exists_for_contact,
    get_conversation_contact_jid,
    get_whatsapp_number_for_org,
    list_conversations,
    list_messages,
    list_whatsapp_numbers,
    mark_conversation_read,
)
from whatsapp_events import publish, subscribe, unsubscribe
from whatsapp_routes import WHATSAPP_INTERNAL_SECRET, WHATSAPP_SERVICE_URL, _proxy_error_detail

logger = logging.getLogger("whatsapp_chat_routes")

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-chat"])


class SendMessageBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


class MessageIngestedBody(BaseModel):
    org_id: str
    whatsapp_number_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    direction: str | None = None
    contact_number: str | None = None
    sent_by: str | None = None
    preview: str | None = None
    last_message_at: str | None = None
    unread_count: int | None = None


async def _assert_number_access(number_id: str, ctx: AuthContext) -> dict:
    row = await get_whatsapp_number_for_org(number_id, ctx.org_id)
    if not row:
        raise HTTPException(status_code=404, detail="WhatsApp number not found")
    return row


async def _proxy_send(org_id: str, to: str, text: str, *, send_kind: str) -> dict:
    headers = {"X-Internal-Secret": WHATSAPP_INTERNAL_SECRET} if WHATSAPP_INTERNAL_SECRET else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                f"{WHATSAPP_SERVICE_URL}/whatsapp/session/{org_id}/send",
                json={
                    "to": to,
                    "text": text,
                    "sent_by": "human_agent",
                    "send_kind": send_kind,
                },
                headers=headers,
            )
        except httpx.RequestError as exc:
            logger.error("whatsapp send proxy failed: %s", exc)
            raise HTTPException(status_code=503, detail="WhatsApp service unavailable") from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_proxy_error_detail(resp))
    return resp.json()


@router.get("/numbers")
async def whatsapp_list_numbers(ctx: AuthContext = Depends(require_auth_context)):
    set_request_org(ctx.org_id)
    return {"items": await list_whatsapp_numbers(ctx.org_id)}


@router.get("/{number_id}/conversations")
async def whatsapp_list_conversations(
    number_id: str,
    search: str | None = Query(None),
    cursor: str | None = Query(None),
    ctx: AuthContext = Depends(require_auth_context),
):
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)
    data = await list_conversations(number_id, search=search, cursor=cursor)
    if data.get("not_found"):
        raise HTTPException(status_code=404, detail="WhatsApp number not found")
    return data


@router.get("/{number_id}/conversations/{conversation_id}/messages")
async def whatsapp_list_messages(
    number_id: str,
    conversation_id: str,
    cursor: str | None = Query(None),
    ctx: AuthContext = Depends(require_auth_context),
):
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)
    data = await list_messages(number_id, conversation_id, cursor=cursor)
    if data.get("forbidden"):
        raise HTTPException(status_code=403, detail="forbidden")
    if data.get("not_found"):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data


@router.post("/{number_id}/conversations/{conversation_id}/messages")
async def whatsapp_send_message(
    number_id: str,
    conversation_id: str,
    body: SendMessageBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    """Human takeover — send manual message via Baileys (persisted by Node service)."""
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)

    jid = await get_conversation_contact_jid(number_id, conversation_id)
    if not jid:
        raise HTTPException(status_code=404, detail="Conversation not found")

    send_kind = "thread_reply" if await conversation_exists_for_contact(number_id, jid.split("@")[0]) else "cold_outbound"
    result = await _proxy_send(ctx.org_id, jid, body.text.strip(), send_kind=send_kind)
    return {"ok": True, **result}


@router.post("/{number_id}/conversations/start")
async def whatsapp_start_conversation(
    number_id: str,
    body: SendMessageBody,
    contact_number: str = Query(..., description="E.164 or local digits"),
    ctx: AuthContext = Depends(require_auth_context),
):
    """Outbound to a new number — creates thread on first send (cold outbound pacing applies)."""
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)

    phone = normalize_phone_number(contact_number).lstrip("+").replace(" ", "")
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid contact number")

    jid = f"{phone}@s.whatsapp.net"
    has_thread = await conversation_exists_for_contact(number_id, phone)
    send_kind = "thread_reply" if has_thread else "cold_outbound"
    result = await _proxy_send(ctx.org_id, jid, body.text.strip(), send_kind=send_kind)
    return {"ok": True, "contact_number": phone, **result}


@router.patch("/{number_id}/conversations/{conversation_id}/read")
async def whatsapp_mark_read(
    number_id: str,
    conversation_id: str,
    ctx: AuthContext = Depends(require_auth_context),
):
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)
    ok = await mark_conversation_read(number_id, conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    publish(ctx.org_id, {"type": "conversation_read", "conversation_id": conversation_id})
    return {"ok": True}


@router.get("/{number_id}/events")
async def whatsapp_events_stream(
    number_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth_context),
):
    """SSE stream — new messages + conversation list updates for this org."""
    set_request_org(ctx.org_id)
    await _assert_number_access(number_id, ctx)

    async def event_generator():
        queue = await subscribe(ctx.org_id)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'number_id': number_id})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.get("whatsapp_number_id") and event["whatsapp_number_id"] != number_id:
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            await unsubscribe(ctx.org_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/internal/message-ingested")
async def whatsapp_internal_message_ingested(body: MessageIngestedBody, request: Request):
    if WHATSAPP_INTERNAL_SECRET and request.headers.get("X-Internal-Secret") != WHATSAPP_INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    publish(
        body.org_id,
        {
            "type": "message",
            "whatsapp_number_id": body.whatsapp_number_id,
            "conversation_id": body.conversation_id,
            "message_id": body.message_id,
            "direction": body.direction,
            "contact_number": body.contact_number,
            "sent_by": body.sent_by,
            "preview": body.preview,
            "last_message_at": body.last_message_at,
            "unread_count": body.unread_count,
        },
    )
    return {"ok": True}