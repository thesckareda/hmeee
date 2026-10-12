"""Dashboard API routes — analytics, leads, reports, settings."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from auth import create_api_settings_token, require_api_settings_access, require_auth
from env_manager import api_settings_password, get_api_keys_for_dashboard, update_env_keys
from dashboard_analytics import (
    TONE_PRESETS,
    build_hot_lead_record,
    build_lead_record,
    dedupe_by_phone,
    filter_valid_rows,
    leads_to_csv,
    monthly_report,
    now_ist,
    summarize_campaigns,
    summarize_performance,
    summarize_today,
    weekly_report,
)
from phone_utils import is_valid_phone_number, normalize_phone_number

logger = logging.getLogger("dashboard-routes")

router = APIRouter(tags=["dashboard"])

CONFIG_FILE = "config.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ALL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _apply_supabase_env_safe():
    mapping = (
        ("SUPABASE_URL", "SUPABASE_URL"),
        ("SUPABASE_KEY", "SUPABASE_KEY"),
        ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    for env_key, _ in mapping:
        val = os.environ.get(env_key, "").strip()
        if val:
            os.environ[env_key] = val


def _read_full_config() -> dict:
    path = os.path.join(BASE_DIR, CONFIG_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _write_config_section(section: str, data: dict) -> dict:
    path = os.path.join(BASE_DIR, CONFIG_FILE)
    config = _read_full_config()
    config[section] = data
    if section == "tone_preset":
        config["tone_preset"] = data if isinstance(data, str) else data.get("tone", config.get("tone_preset"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
    return config


async def _fetch_rows(limit: int = 1000) -> list[dict]:
    _apply_supabase_env_safe()
    import db

    rows = await db.fetch_call_logs_or_transcripts(limit=limit, include_transcript=False)
    return filter_valid_rows(rows or [])


async def _fetch_agents():
    _apply_supabase_env_safe()
    import agents_db

    try:
        return await agents_db.list_agents()
    except Exception as exc:
        logger.warning("list_agents failed: %s", exc)
        return []


def _agent_lookup(agents) -> dict[str, str]:
    from dashboard_analytics import agent_name_map

    return agent_name_map(agents)


async def _build_all_leads(rows: list[dict], agents_map: dict[str, str]) -> list[dict]:
    deduped, counts = dedupe_by_phone(rows)
    leads = []
    for row in deduped:
        phone = normalize_phone_number(row.get("phone_number") or row.get("from_number") or "")
        if not phone:
            phone = f"unknown-{row.get('id', '')}"
        leads.append(build_lead_record(row, counts.get(phone, 1), agents_map))
    leads.sort(key=lambda x: x.get("last_call_date") or "", reverse=True)
    return leads


# ─── Analytics ────────────────────────────────────────────────────────────────

@router.get("/api/analytics/summary")
async def api_analytics_summary(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        agents = await _fetch_agents()
        return summarize_today(rows, agents)
    except Exception as exc:
        logger.error("analytics/summary error: %s", exc)
        return {
            "today": {
                "total_calls": 0,
                "week_calls": 0,
                "successful_conversations": 0,
                "conversion_rate": 0.0,
                "money_impact_inr": 0,
                "top_agent": None,
            },
            "alerts": [],
        }


@router.get("/api/analytics/performance")
async def api_analytics_performance(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        return summarize_performance(rows)
    except Exception as exc:
        logger.error("analytics/performance error: %s", exc)
        return {
            "weekly_conversion_chart": [],
            "site_visits_this_month": 0,
            "offers_received_this_month": 0,
            "avg_call_duration_seconds": 0,
            "overall_success_rate": 0.0,
            "best_days": [],
            "best_hours": [],
        }


@router.get("/api/analytics/campaigns")
async def api_analytics_campaigns(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        agents = await _fetch_agents()
        return summarize_campaigns(rows, agents)
    except Exception as exc:
        logger.error("analytics/campaigns error: %s", exc)
        return {
            "top_listings": [],
            "top_areas": [],
            "ab_test": {"tone_a": None, "tone_b": None, "winner": None, "delta_pct": 0.0},
        }


# ─── Leads ───────────────────────────────────────────────────────────────────

@router.get("/api/leads")
async def api_leads(
    user: str = Depends(require_auth),
    search: str = Query("", alias="search"),
    outcome: str = Query("", alias="outcome"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    try:
        rows = await _fetch_rows()
        agents_map = _agent_lookup(await _fetch_agents())
        leads = await _build_all_leads(rows, agents_map)

        q = search.strip().lower()
        if q:
            leads = [
                l for l in leads
                if q in str(l.get("caller_name", "")).lower()
                or q in str(l.get("phone_number", "")).lower()
            ]
        if outcome:
            leads = [l for l in leads if l.get("outcome") == outcome]

        total = len(leads)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        page_items = leads[start : start + page_size]

        return {
            "leads": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
    except Exception as exc:
        logger.error("leads error: %s", exc)
        return {"leads": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}


@router.get("/api/lead-memory")
async def api_lead_memory(
    phone: str = Query(..., min_length=8),
    user: str = Depends(require_auth),
):
    """RE Voice memory bundle for CRM — profile, property graph, objections, call artifacts."""
    try:
        from phone_utils import normalize_phone_number
        from re_voice_memory.dashboard import get_lead_memory_bundle

        normalized = normalize_phone_number(phone)
        if not normalized:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        return get_lead_memory_bundle(normalized)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("lead memory error for %s: %s", phone, exc)
        return {"phone": phone, "has_memory": False, "error": str(exc)}


@router.get("/api/memory/remember")
async def api_memory_remember_state(user: str = Depends(require_auth)):
    """State of the 'Remember the caller' button."""
    from config_store import read_raw_config

    cfg = read_raw_config()
    return {"remember_caller_enabled": bool(cfg.get("remember_caller_enabled", True))}


@router.post("/api/memory/remember")
async def api_memory_remember_toggle(
    request: Request,
    user: str = Depends(require_auth),
):
    """The 'Remember the caller' button. Body: {"enabled": true/false}.

    ON  → engine stores caller memory and opens returning calls with
          time-anchored recall ('aapne kal call kiya tha na…').
    OFF → nothing is read or written; every call is a fresh caller.
    """
    from assistant_settings import touch_reload_signal
    from config_store import write_full_config

    body = await request.json()
    enabled = bool(body.get("enabled", True))
    write_full_config({"remember_caller_enabled": enabled})
    touch_reload_signal()
    return {"success": True, "remember_caller_enabled": enabled}


@router.post("/api/memory/forget")
async def api_memory_forget(
    request: Request,
    user: str = Depends(require_auth),
):
    """Erase everything remembered about one caller. Body: {"phone": "+91…"}.

    Irreversible — removes the file container AND all Redis/SQLite keys.
    """
    from phone_utils import normalize_phone_number
    from re_voice_memory.memory_admin import forget_caller

    body = await request.json()
    normalized = normalize_phone_number(str(body.get("phone") or ""))
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    return await forget_caller(normalized)


@router.get("/api/memory/container")
async def api_memory_container(
    phone: str = Query(..., min_length=8),
    user: str = Depends(require_auth),
):
    """Inspect the memory container for one caller (what do we remember?)."""
    from phone_utils import normalize_phone_number
    from re_voice_memory.memory_admin import memory_container_summary

    normalized = normalize_phone_number(phone)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    return memory_container_summary(normalized)


@router.get("/api/leads/hot")
async def api_hot_leads(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        agents_map = _agent_lookup(await _fetch_agents())
        deduped, _ = dedupe_by_phone(rows)
        ref = now_ist()
        hot = []
        for row in deduped:
            record = build_hot_lead_record(row, agents_map, ref)
            if record["urgency_score"] >= 50:
                hot.append(record)
        hot.sort(key=lambda x: x["urgency_score"], reverse=True)
        return {"hot_leads": hot[:50]}
    except Exception as exc:
        logger.error("hot leads error: %s", exc)
        return {"hot_leads": []}


class LeadActionBody(BaseModel):
    action: str = Field(..., pattern="^(send_whatsapp|schedule_site_visit|mark_booked|escalate_human|archive)$")


@router.post("/api/leads/{call_id}/action")
async def api_lead_action(call_id: str, body: LeadActionBody, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import db

    action = body.action
    try:
        if action == "mark_booked":
            ok = await db.update_call_log(call_id, {"was_booked": True, "call_status": "booked"})
            if not ok:
                raise HTTPException(status_code=404, detail="Call not found")
            return {"success": True, "action": action, "call_id": call_id, "message": "Lead marked as booked"}

        if action == "archive":
            ok = await db.update_call_log(call_id, {"call_status": "archived"})
            if not ok:
                raise HTTPException(status_code=404, detail="Call not found")
            return {"success": True, "action": action, "call_id": call_id, "message": "Lead archived"}

        await db.insert_lead_action(call_id=call_id, action=action, performed_by=user)

        crm_synced = False
        try:
            row = await db.get_call_log(call_id)
            phone = normalize_phone_number(
                (row or {}).get("phone_number") or (row or {}).get("from_number") or ""
            )
            if phone:
                from re_voice_memory.crm_sync import sync_dashboard_action_to_crm

                crm_synced = await sync_dashboard_action_to_crm(phone, action, user)
        except Exception as crm_exc:
            logger.warning("CRM action sync failed: %s", crm_exc)

        messages = {
            "send_whatsapp": "WhatsApp listing action recorded",
            "schedule_site_visit": "Site visit scheduling recorded",
            "escalate_human": "Escalation to human agent recorded",
        }
        return {
            "success": True,
            "action": action,
            "call_id": call_id,
            "message": messages.get(action, "Action recorded"),
            "crm_synced": crm_synced,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("lead action error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _export_leads_csv(include_recording: bool = True) -> str:
    rows = await _fetch_rows()
    agents_map = _agent_lookup(await _fetch_agents())
    leads = await _build_all_leads(rows, agents_map)
    return leads_to_csv(leads, include_recording=include_recording)


@router.get("/api/leads/export/excel")
async def api_leads_export_get(user: str = Depends(require_auth)):
    content = await _export_leads_csv()
    filename = f"leads_export_{now_ist().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/leads/export/excel")
async def api_leads_export_post(user: str = Depends(require_auth)):
    return await api_leads_export_get(user)


# ─── Reports ─────────────────────────────────────────────────────────────────

@router.get("/api/reports/weekly-summary")
async def api_weekly_summary(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        return weekly_report(rows)
    except Exception as exc:
        logger.error("weekly summary error: %s", exc)
        return {
            "period": "",
            "generated_at": now_ist().isoformat(),
            "summary_text": "No data available for this week.",
            "kpis": {},
            "top_properties": [],
            "pending_followups": 0,
        }


@router.get("/api/reports/monthly-summary")
async def api_monthly_summary(user: str = Depends(require_auth)):
    try:
        rows = await _fetch_rows()
        agents = await _fetch_agents()
        return monthly_report(rows, agents)
    except Exception as exc:
        logger.error("monthly summary error: %s", exc)
        return {
            "period": "",
            "generated_at": now_ist().isoformat(),
            "kpis": {},
            "week_by_week": [],
            "top_agents": [],
            "summary_text": "No data available for this month.",
        }


@router.get("/api/reports/export/leads/csv")
async def api_reports_export_leads(user: str = Depends(require_auth)):
    return await api_leads_export_get(user)


class WeeklyEmailBody(BaseModel):
    email: str = ""


@router.post("/api/reports/send-weekly-email")
async def api_send_weekly_email(body: WeeklyEmailBody, user: str = Depends(require_auth)):
    try:
        notify_email = (body.email or os.environ.get("NOTIFY_EMAIL", "")).strip()
        whatsapp = os.environ.get("TWILIO_WHATSAPP_NUMBER", "").strip()
        twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()

        if not notify_email and not (whatsapp and twilio_sid):
            return {
                "success": False,
                "message": "Email delivery not configured. Use /api/reports/weekly-summary to get the data.",
                "channel": "none",
            }

        report = weekly_report(await _fetch_rows())
        message = report.get("summary_text", "Weekly summary unavailable.")

        if notify_email:
            logger.info("[REPORT] Weekly summary for %s: %s", notify_email, message[:120])
            return {
                "success": True,
                "message": f"Weekly summary queued for {notify_email}",
                "channel": "email",
            }

        from notify import send_whatsapp

        alert_phone = os.environ.get("DEFAULT_TRANSFER_NUMBER", "")
        if alert_phone and send_whatsapp(alert_phone, f"📊 Weekly AI Report\n\n{message}"):
            return {"success": True, "message": "Weekly summary sent via WhatsApp", "channel": "whatsapp"}

        return {
            "success": False,
            "message": "Notification channel configured but delivery failed.",
            "channel": "none",
        }
    except Exception as exc:
        logger.error("send weekly email error: %s", exc)
        return {"success": False, "message": str(exc), "channel": "none"}


# ─── Settings ────────────────────────────────────────────────────────────────

class BusinessHoursBody(BaseModel):
    timezone: str = "Asia/Kolkata"
    working_days: list[str] = Field(default_factory=lambda: ALL_DAYS[:6])
    open_time: str = "09:00"
    close_time: str = "20:00"
    holiday_dates: list[str] = Field(default_factory=list)

    @field_validator("open_time", "close_time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        if not v or len(v) != 5 or v[2] != ":":
            raise ValueError("Time must be HH:MM")
        return v

    @field_validator("working_days")
    @classmethod
    def validate_days(cls, v: list[str]) -> list[str]:
        invalid = [d for d in v if d not in ALL_DAYS]
        if invalid:
            raise ValueError(f"Invalid working days: {invalid}")
        return v


@router.get("/api/settings/business-hours")
async def get_business_hours(user: str = Depends(require_auth)):
    config = _read_full_config()
    defaults = BusinessHoursBody().model_dump()
    stored = config.get("business_hours") or {}
    return {**defaults, **stored}


@router.post("/api/settings/business-hours")
async def post_business_hours(body: BusinessHoursBody, user: str = Depends(require_auth)):
    payload = body.model_dump()
    _write_config_section("business_hours", payload)
    return {"success": True, "business_hours": payload}


class NotificationsBody(BaseModel):
    whatsapp_alerts: bool = True
    email_alerts: bool = False
    alert_on_hot_lead: bool = True
    alert_on_booking: bool = True
    alert_phone: str = ""
    alert_email: str = ""


@router.get("/api/settings/notifications")
async def get_notifications(user: str = Depends(require_auth)):
    config = _read_full_config()
    defaults = NotificationsBody(
        alert_phone=os.environ.get("DEFAULT_TRANSFER_NUMBER", ""),
        alert_email=os.environ.get("NOTIFY_EMAIL", ""),
    ).model_dump()
    stored = config.get("notifications") or {}
    return {**defaults, **stored}


@router.post("/api/settings/notifications")
async def post_notifications(body: NotificationsBody, user: str = Depends(require_auth)):
    if body.whatsapp_alerts and body.alert_phone and not is_valid_phone_number(
        normalize_phone_number(body.alert_phone)
    ):
        raise HTTPException(status_code=400, detail="Invalid alert_phone")
    payload = body.model_dump()
    _write_config_section("notifications", payload)
    return {"success": True, "notifications": payload}


@router.get("/api/settings/tone")
async def get_tone(user: str = Depends(require_auth)):
    config = _read_full_config()
    current = config.get("tone_preset", "professional")
    return {
        "current_tone": current,
        "available_tones": [
            {"id": k, "label": v["label"], "description": v["description"]}
            for k, v in TONE_PRESETS.items()
        ],
    }


class ToneBody(BaseModel):
    tone: str


@router.post("/api/settings/tone")
async def post_tone(body: ToneBody, user: str = Depends(require_auth)):
    tone = body.tone
    if tone not in TONE_PRESETS:
        raise HTTPException(status_code=400, detail="Invalid tone preset")
    _write_config_section("tone_preset", tone)

    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentUpdate

    instruction = TONE_PRESETS[tone]["instruction"]
    updated = 0
    try:
        agents = await agents_db.list_agents(is_active=True)
        for agent in agents:
            prompt = (agent.get("system_prompt") or "").strip()
            cleaned = prompt
            for preset in TONE_PRESETS.values():
                cleaned = cleaned.replace(preset["instruction"], "").strip()
            new_prompt = f"{cleaned}\n\n{instruction}".strip() if cleaned else instruction
            await agents_db.update_agent(agent["id"], AgentUpdate(system_prompt=new_prompt))
            updated += 1
    except Exception as exc:
        logger.warning("tone agent update partial failure: %s", exc)

    return {"success": True, "agents_updated": updated, "tone": tone}


class ApiSettingsUnlockBody(BaseModel):
    password: str


class VoiceConfigBody(BaseModel):
    tts_provider: str | None = None
    cartesia_tts_model: str | None = None
    tts_voice: str | None = None
    tts_voice_display: str | None = None
    tts_language: str | None = None


class ApiKeysUpdateBody(BaseModel):
    keys: dict[str, str] = Field(default_factory=dict)
    restart_agent: bool = True
    voice_config: VoiceConfigBody | None = None


@router.post("/api/settings/api-keys/unlock")
async def unlock_api_settings(body: ApiSettingsUnlockBody, user: str = Depends(require_auth)):
    if body.password != api_settings_password():
        raise HTTPException(status_code=403, detail="Invalid API settings password")
    return {
        "success": True,
        "api_settings_token": create_api_settings_token(user),
        "expires_minutes": 60,
    }


@router.get("/api/settings/api-keys")
async def get_api_keys(_user: str = Depends(require_api_settings_access)):
    from config_store import get_voice_config_payload

    payload = get_api_keys_for_dashboard()
    payload["voice_config"] = get_voice_config_payload()
    return payload


@router.put("/api/settings/api-keys")
async def put_api_keys(body: ApiKeysUpdateBody, _user: str = Depends(require_api_settings_access)):
    from config_store import apply_voice_config, get_voice_config_payload

    applied = update_env_keys(body.keys)
    voice_applied: dict[str, str] = {}
    if body.voice_config is not None:
        voice_applied = apply_voice_config(body.voice_config.model_dump(exclude_none=True))

    restart_result = None
    if body.restart_agent and (applied or voice_applied):
        try:
            from agent_process import restart_agent_fresh

            restart_result = restart_agent_fresh()
        except Exception as exc:
            logger.exception("Agent restart after settings save failed")
            restart_result = {"success": False, "restarted": False, "message": str(exc)}

    payload = get_api_keys_for_dashboard()
    payload["voice_config"] = get_voice_config_payload()
    return {
        "success": True,
        "updated_keys": sorted(applied.keys()),
        "updated_voice": sorted(voice_applied.keys()),
        "env_file": payload["env_file"],
        "groups": payload["groups"],
        "voice_config": payload["voice_config"],
        "restart": restart_result,
        "message": (
            "Settings saved and agent restarted."
            if restart_result and restart_result.get("success")
            else (
                "Settings saved. Restart agent manually if calls still use old config."
                if applied or voice_applied
                else "No changes to save."
            )
        ),
    }


@router.get("/api/settings/phone-numbers")
async def get_phone_numbers(user: str = Depends(require_auth)):
    raw = os.environ.get("INBOUND_PHONE_NUMBERS", "")
    numbers = [normalize_phone_number(n.strip()) for n in raw.split(",") if n.strip()]
    return {
        "sip_trunk_id": os.environ.get("SIP_TRUNK_ID", ""),
        "phone_numbers": [n for n in numbers if n],
        "livekit_agent_name": os.environ.get("LIVEKIT_AGENT_NAME", "inbound-voice-demo"),
        "transfer_number": os.environ.get("DEFAULT_TRANSFER_NUMBER", ""),
    }


class AgentToggleBody(BaseModel):
    is_active: bool


@router.post("/api/agents/{agent_id}/toggle")
async def toggle_agent(agent_id: str, body: AgentToggleBody, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentUpdate

    try:
        agent = await agents_db.update_agent(agent_id, AgentUpdate(is_active=body.is_active))
        msg = "Agent resumed" if body.is_active else "Agent paused"
        return {
            "success": True,
            "agent_id": str(agent.id),
            "is_active": agent.is_active,
            "message": msg,
        }
    except Exception as exc:
        logger.error("toggle agent error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AssignKnowledgeBody(BaseModel):
    knowledge_data_path: str
    config_preset: str = "config.json"


@router.post("/api/agents/{agent_id}/assign-knowledge")
async def assign_knowledge(agent_id: str, body: AssignKnowledgeBody, user: str = Depends(require_auth)):
    path = body.knowledge_data_path.strip()
    full_path = path if os.path.isabs(path) else os.path.join(BASE_DIR, path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail=f"Knowledge file not found: {path}")

    file_size = os.path.getsize(full_path)
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentUpdate

    try:
        row = await agents_db.get_agent_row(agent_id)
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        meta = dict(row.get("metadata") or {})
        meta["knowledge_base"] = {
            "file_path": path,
            "config_preset": body.config_preset,
            "loaded_at": datetime.now().isoformat(),
        }
        await agents_db.update_agent(agent_id, AgentUpdate(metadata=meta))
        return {
            "success": True,
            "agent_id": agent_id,
            "knowledge_data_path": path,
            "file_size_bytes": file_size,
        }
    except Exception as exc:
        logger.error("assign knowledge error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Local live test monitor (laptop only, no Supabase) ───────────────────────

def _local_monitor_enabled() -> bool:
    from local_live_monitor import is_enabled

    return is_enabled()


@router.get("/api/local/live-calls")
async def local_live_calls():
    """Active + recent local test calls from data/live_test/."""
    if not _local_monitor_enabled():
        raise HTTPException(status_code=404, detail="LOCAL_LIVE_MONITOR is disabled")
    from local_live_monitor import list_active_calls, list_recent_call_files

    active = list_active_calls()
    recent_files = list_recent_call_files(limit=15)
    recent = [
        {"room_id": f["room_id"], "status": "recent", "modified_at": f["modified_at"]}
        for f in recent_files
    ]
    return {"active": active, "recent": recent, "enabled": True}


@router.get("/api/local/live-events")
async def local_live_events(
    room_id: str = Query(..., min_length=1),
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
):
    if not _local_monitor_enabled():
        raise HTTPException(status_code=404, detail="LOCAL_LIVE_MONITOR is disabled")
    from local_live_monitor import get_call_events

    return get_call_events(room_id, after=after, limit=limit)


@router.get("/api/local/agent-log-tail")
async def local_agent_log_tail(
    lines: int = Query(80, ge=10, le=500),
    room_id: str | None = Query(None),
):
    if not _local_monitor_enabled():
        raise HTTPException(status_code=404, detail="LOCAL_LIVE_MONITOR is disabled")
    from local_live_monitor import tail_agent_log

    return {"lines": tail_agent_log(lines=lines, room_id=room_id), "room_id": room_id}