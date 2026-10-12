"""WhatsApp "Phone WhatsApp" connection — thin FastAPI bridge.

Two kinds of routes here:
  * User-facing, org-scoped (`/api/whatsapp/session/...`) — proxy the
    dashboard's requests to the Node whatsapp-service over the internal
    Docker network, so the frontend never needs to know a second backend
    language exists.
  * One internal, service-to-service route (`/api/whatsapp/internal/incoming`)
    that the Node service calls when a WhatsApp message arrives — not exposed
    to the browser, guarded by a shared secret instead of a user JWT.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from auth import require_auth_context
from tenancy import AuthContext, set_request_org

logger = logging.getLogger("whatsapp_routes")

router = APIRouter(prefix="/api", tags=["whatsapp"])

def _whatsapp_service_url() -> str:
    """Resolve the Node whatsapp-service base URL.

    Docker Compose sets WHATSAPP_SERVICE_URL to the internal hostname; when
    running ui_server locally (uvicorn), default to localhost instead.
    """
    explicit = os.environ.get("WHATSAPP_SERVICE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return "http://127.0.0.1:4001"


WHATSAPP_SERVICE_URL = _whatsapp_service_url()
WHATSAPP_INTERNAL_SECRET = os.environ.get("WHATSAPP_INTERNAL_SECRET", "")


def _proxy_error_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error") or body.get("detail")
            if err:
                return str(err)
    except Exception:
        pass
    text = (resp.text or "").strip()
    return text or f"WhatsApp service error ({resp.status_code})"


async def _proxy(method: str, path: str, *, json: dict | None = None) -> dict:
    headers = {"X-Internal-Secret": WHATSAPP_INTERNAL_SECRET} if WHATSAPP_INTERNAL_SECRET else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.request(method, f"{WHATSAPP_SERVICE_URL}{path}", json=json, headers=headers)
        except httpx.RequestError as exc:
            logger.error("whatsapp-service unreachable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="WhatsApp service is not running. Start it with: cd whatsapp-service && npm start",
            ) from exc

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=_proxy_error_detail(resp))
    return resp.json()


# ── User-facing (org-scoped) ──────────────────────────────────────────────────


@router.post("/whatsapp/session/start")
async def whatsapp_session_start(ctx: AuthContext = Depends(require_auth_context)):
    return await _proxy("POST", "/whatsapp/session/start", json={"org_id": ctx.org_id})


@router.get("/whatsapp/session/status")
async def whatsapp_session_status(ctx: AuthContext = Depends(require_auth_context)):
    return await _proxy("GET", f"/whatsapp/session/{ctx.org_id}/status")


@router.post("/whatsapp/session/stop")
async def whatsapp_session_stop(ctx: AuthContext = Depends(require_auth_context)):
    return await _proxy("POST", f"/whatsapp/session/{ctx.org_id}/stop")


class PairingCodeBody(BaseModel):
    phone_number: str


@router.post("/whatsapp/session/pairing-code")
async def whatsapp_pairing_code(
    body: PairingCodeBody, ctx: AuthContext = Depends(require_auth_context)
):
    return await _proxy(
        "POST",
        f"/whatsapp/session/{ctx.org_id}/pairing-code",
        json={"phone_number": body.phone_number},
    )


# ── Internal (service-to-service, no user auth) ───────────────────────────────


class IncomingMessageBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    org_id: str
    text: str
    ts: int | None = None
    from_: str = Field(alias="from")
    to_number: str | None = None


@router.post("/whatsapp/internal/incoming")
async def whatsapp_internal_incoming(body: IncomingMessageBody, request: Request):
    if WHATSAPP_INTERNAL_SECRET and request.headers.get("X-Internal-Secret") != WHATSAPP_INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    from whatsapp_reply_engine import generate_reply

    set_request_org(body.org_id)
    reply = await generate_reply(
        org_id=body.org_id,
        wa_number=body.from_,
        text=body.text,
        to_number=body.to_number,
    )
    return {"reply": reply}
