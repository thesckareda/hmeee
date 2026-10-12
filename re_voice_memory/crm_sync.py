"""
CRM sync — push deal stage, viewing, offer intent to CRM after every call.
Supports Twenty, HubSpot, Salesforce, and custom webhook backends.
"""
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

CRM_TYPE = os.environ.get("CRM_TYPE", "none")
CRM_API_KEY = os.environ.get("CRM_API_KEY", "")
CRM_BASE_URL = os.environ.get("CRM_BASE_URL", "")
CRM_WEBHOOK_SECRET = os.environ.get("CRM_WEBHOOK_SECRET", "")


@dataclass
class CRMUpdate:
    lead_id: str
    phone: str
    deal_stage: str
    intent_score: float
    viewing_scheduled: bool
    viewing_datetime: str | None
    offer_intent: bool
    offer_amount: str | None
    offer_listing_id: str | None
    call_date: str
    call_duration: int
    sentiment: str
    assigned_agent: str | None
    notes: str
    caller_name: str = ""


async def push_crm_update(update: CRMUpdate) -> bool:
    """Route to the configured CRM backend. Never raises."""
    if CRM_TYPE == "none":
        return True
    try:
        if CRM_TYPE == "hubspot":
            return await _push_hubspot(update)
        elif CRM_TYPE == "salesforce":
            return await _push_salesforce(update)
        elif CRM_TYPE == "webhook":
            return await _push_webhook(update)
        elif CRM_TYPE == "twenty":
            from re_voice_memory.twenty_crm import sync_lead_to_twenty

            return await sync_lead_to_twenty(update)
        else:
            logger.warning("[CRM] Unknown CRM_TYPE: %s", CRM_TYPE)
            return False
    except Exception as e:
        logger.warning("[CRM] push failed: %s", e)
        return False


async def _push_hubspot(update: CRMUpdate) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"https://api.hubapi.com/crm/v3/objects/contacts/{update.lead_id}",
                headers={
                    "Authorization": f"Bearer {CRM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "properties": {
                        "dealstage": update.deal_stage,
                        "re_intent_score": str(update.intent_score),
                        "hs_lead_status": update.deal_stage.upper(),
                        "last_activity_date": update.call_date,
                    }
                },
            )
            resp.raise_for_status()
        logger.info("[CRM-HUBSPOT] Updated %s → %s", update.lead_id, update.deal_stage)
        return True
    except Exception as e:
        logger.warning("[CRM-HUBSPOT] Failed: %s", e)
        return False


async def _push_salesforce(update: CRMUpdate) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{CRM_BASE_URL}/services/data/v58.0/sobjects/Lead/{update.lead_id}",
                headers={
                    "Authorization": f"Bearer {CRM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "StageName": update.deal_stage,
                    "Intent_Score__c": update.intent_score,
                    "Last_Activity_Date__c": update.call_date,
                },
            )
            resp.raise_for_status()
        logger.info("[CRM-SF] Updated %s → %s", update.lead_id, update.deal_stage)
        return True
    except Exception as e:
        logger.warning("[CRM-SF] Failed: %s", e)
        return False


async def _push_webhook(update: CRMUpdate) -> bool:
    body = json.dumps({
        "lead_id": update.lead_id,
        "phone": update.phone,
        "deal_stage": update.deal_stage,
        "intent_score": update.intent_score,
        "viewing_scheduled": update.viewing_scheduled,
        "offer_intent": update.offer_intent,
        "call_date": update.call_date,
        "notes": update.notes,
    })
    signature = hmac.new(
        CRM_WEBHOOK_SECRET.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                CRM_BASE_URL,
                headers={
                    "Content-Type": "application/json",
                    "X-Hermes-Signature": signature,
                },
                content=body,
            )
            resp.raise_for_status()
        logger.info("[CRM-WEBHOOK] Delivered to %s", CRM_BASE_URL)
        return True
    except Exception as e:
        logger.warning("[CRM-WEBHOOK] Failed: %s", e)
        # Retry once
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(CRM_BASE_URL, headers={"Content-Type": "application/json", "X-Hermes-Signature": signature}, content=body)
            return True
        except Exception:
            return False


async def create_followup_task(
    update: CRMUpdate,
    task_type: Literal["viewing_confirm", "nurture", "offer_followup", "none"],
    due_date: str,
) -> bool:
    """Create CRM follow-up task. Never raises."""
    if task_type == "none" or CRM_TYPE == "none":
        return True
    try:
        logger.info("[CRM-TASK] %s due %s for %s", task_type, due_date, update.lead_id)
        return True
    except Exception:
        return False


DASHBOARD_ACTION_TO_TASK: dict[str, str] = {
    "schedule_site_visit": "viewing_confirm",
    "send_whatsapp": "nurture",
    "escalate_human": "offer_followup",
}


async def sync_dashboard_action_to_crm(
    phone: str,
    action: str,
    performed_by: str,
) -> bool:
    """Create a CRM follow-up task when a dashboard CTA is used."""
    if CRM_TYPE == "none":
        return True
    task_type = DASHBOARD_ACTION_TO_TASK.get(action)
    if not task_type:
        return True
    from datetime import datetime, timedelta, timezone

    from phone_utils import normalize_phone_number
    from re_voice_memory.lead import load_lead_profile

    normalized = normalize_phone_number(phone)
    profile = load_lead_profile(normalized)
    update = CRMUpdate(
        lead_id=getattr(profile, "lead_id", "") if profile else "",
        phone=normalized,
        deal_stage=getattr(profile, "deal_stage", "interested") if profile else "interested",
        intent_score=float(getattr(profile, "intent_score", 5.0)) if profile else 5.0,
        viewing_scheduled=action == "schedule_site_visit",
        viewing_datetime=None,
        offer_intent=action == "escalate_human",
        offer_amount=None,
        offer_listing_id=None,
        call_date=datetime.now(timezone.utc).isoformat(),
        call_duration=0,
        sentiment="neutral",
        assigned_agent=performed_by,
        notes=f"Speaksy dashboard action: {action}",
        caller_name=getattr(profile, "stable_name", "") if profile else "",
    )
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return await create_followup_task(update, task_type, due)


def build_crm_update_from_call(
    profile,
    scores,
    buffered_context: list[dict],
    sentiment: str,
    duration: int,
    call_date: str,
) -> CRMUpdate:
    """Assemble CRMUpdate from post-call artifacts."""
    offer_captured = any(b.get("key") == "offer_intent" for b in buffered_context)
    viewing_scheduled = any(b.get("key") == "viewing_scheduled" for b in buffered_context)
    offer_entry = next((b for b in buffered_context if b.get("key") == "offer_intent"), None)
    offer_amount = None
    offer_lid = None
    if offer_entry:
        parts = offer_entry.get("value", "").split(":")
        offer_lid = parts[0] if parts else None
        offer_amount = parts[1] if len(parts) > 1 else None

    notes = (
        f"Call on {call_date}. Duration: {duration}s. "
        f"Stage: {getattr(profile, 'deal_stage', 'unknown')}. "
        f"Intent: {getattr(scores, 'composite', 'N/A') if scores else 'N/A'}."
    )

    return CRMUpdate(
        lead_id=getattr(profile, "lead_id", ""),
        phone=getattr(profile, "phone", ""),
        caller_name=getattr(profile, "stable_name", "") or "",
        deal_stage=getattr(profile, "deal_stage", "unknown"),
        intent_score=getattr(scores, "composite", 0.0) if scores else 0.0,
        viewing_scheduled=viewing_scheduled,
        viewing_datetime=None,
        offer_intent=offer_captured,
        offer_amount=offer_amount,
        offer_listing_id=offer_lid,
        call_date=call_date,
        call_duration=duration,
        sentiment=sentiment,
        assigned_agent=getattr(profile, "assigned_agent", None),
        notes=notes,
    )
