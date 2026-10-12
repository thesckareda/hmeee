import asyncio
import certifi
import csv
import io
import json
import logging
import os

# macOS Python often lacks system CAs — certifi bundle is required for HTTPS clients.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
import re
from datetime import datetime, timedelta, timezone
from datetime_utils import now_utc
from html import escape as html_escape
from typing import Any, Optional
from uuid import UUID
from fastapi import FastAPI, Request, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator
from phone_utils import normalize_phone_number, is_valid_phone_number, is_demo_phone
from secrets_manager import get_secret
from auth import (
    AUTH_COOKIE_NAME,
    TOKEN_EXPIRE_HOURS,
    get_auth_context_from_request,
    is_auth_enabled,
    require_auth,
    require_auth_media,
    _decode_access_token,
)
from org_store import ensure_bootstrap
from tenancy import set_request_org
from utils import sanitize_caller_name, CALLER_NAME_PLACEHOLDERS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui-server")

app = FastAPI(title="Speaksy Voice AI Dashboard")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Always load .env from the project folder (not the shell's current working directory).
load_dotenv(os.path.join(BASE_DIR, ".env"))

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
else:
    logger.error("frontend/ folder not found at %s — CSS, JS, and icons will not load", FRONTEND_DIR)

FAVICON_PATH = os.path.join(FRONTEND_DIR, "favicon.png")
FAVICON_LINKS = (
    '  <link rel="icon" type="image/png" href="/static/favicon.png">\n'
    '  <link rel="apple-touch-icon" href="/static/favicon.png">\n'
)


@app.get("/favicon.ico", include_in_schema=False)
async def get_favicon():
    if not os.path.isfile(FAVICON_PATH):
        raise HTTPException(status_code=404, detail="Favicon not found")
    return FileResponse(FAVICON_PATH, media_type="image/png")

_lucide_vendor = os.path.join(FRONTEND_DIR, "vendor", "lucide", "lucide.min.js")
if os.path.isdir(FRONTEND_DIR) and not os.path.isfile(_lucide_vendor):
    logger.error(
        "Missing %s — run `git pull` to restore vendor assets, or icons will be blank",
        _lucide_vendor,
    )

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

from client_paths import CLIENT_BASE_PATH, LEGACY_BASE_PATH

_PUBLIC_PATHS = {
    "/favicon.ico",
    "/login",
    "/health",
    "/metrics",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/register",
    "/live-test",
    "/insurance",
    "/fintech",
    "/demo",
    CLIENT_BASE_PATH,
    f"{CLIENT_BASE_PATH}/login",
    LEGACY_BASE_PATH,
    f"{LEGACY_BASE_PATH}/login",
}


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/api/local/"):
        try:
            from local_live_monitor import is_enabled

            return is_enabled()
        except Exception:
            return False
    return False


def _has_valid_session(request: Request) -> bool:
    token = request.cookies.get(AUTH_COOKIE_NAME, "").strip()
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token:
        return False
    try:
        _decode_access_token(token)
        return True
    except HTTPException:
        return False


@app.middleware("http")
async def enforce_dashboard_login(request: Request, call_next):
    if not is_auth_enabled():
        set_request_org(None, filter_enabled=False)
        return await call_next(request)

    path = request.url.path
    if _is_public_path(path):
        return await call_next(request)

    if path in ("/", "/v2") and not _has_valid_session(request):
        return RedirectResponse(url="/login", status_code=302)

    if path == f"{CLIENT_BASE_PATH}/dashboard" and not _has_valid_session(request):
        return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=302)
    if (
        CLIENT_BASE_PATH != LEGACY_BASE_PATH
        and path == f"{LEGACY_BASE_PATH}/dashboard"
        and not _has_valid_session(request)
    ):
        return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=302)

    return await call_next(request)


@app.middleware("http")
async def bind_tenancy_context(request: Request, call_next):
    """Decode JWT and set org_id for multi-tenant DB filtering."""
    if not _is_public_path(request.url.path):
        try:
            get_auth_context_from_request(request)
        except HTTPException:
            if is_auth_enabled():
                set_request_org(None, filter_enabled=False)
            else:
                from tenancy import DEFAULT_ORG_ID

                set_request_org(DEFAULT_ORG_ID)
    response = await call_next(request)
    return response


# ── Security headers middleware ───────────────────────────────────────────────
_LANDING_CSP_PATHS = {CLIENT_BASE_PATH, "/insurance", "/fintech"}
_STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:;"
)
_LANDING_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https://lh3.googleusercontent.com; "
    "connect-src 'self' https://cdn.tailwindcss.com;"
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.path in _LANDING_CSP_PATHS:
        response.headers["Content-Security-Policy"] = _LANDING_CSP
    else:
        response.headers["Content-Security-Policy"] = _STRICT_CSP
    return response

CONFIG_FILE = "config.json"
IST = timezone(timedelta(hours=5, minutes=30))
_PHONE_PLACEHOLDERS = {"", "unknown", "unknown caller", "caller", "demo"}


def _canonical_name_key(name: str | None) -> str:
  cleaned = sanitize_caller_name(name)
  if not cleaned:
    return ""
  return re.sub(r"\s+", " ", cleaned.strip().lower())


def _normalize_appointment_time_ist(value) -> str:
    """
    Normalize appointment timestamps into ISO-8601 IST.
    - If timezone is present, convert to IST.
    - If timezone is absent, treat input as IST wall-clock time.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""

    matched = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+\-]\d{2}:?\d{2})?$",
        raw,
        re.IGNORECASE,
    )
    if matched:
        y, mo, da, hh, mm, ss, zone = matched.groups()
        ss = ss or "00"
        if zone:
            zone_norm = "Z" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
            dt = datetime.fromisoformat(f"{y}-{mo}-{da}T{hh}:{mm}:{ss}{zone_norm}")
            return dt.astimezone(IST).isoformat(timespec="minutes")
        dt = datetime(int(y), int(mo), int(da), int(hh), int(mm), int(ss), tzinfo=IST)
        return dt.isoformat(timespec="minutes")

    normalized = raw.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if re.search(r"[+\-]\d{4}$", normalized):
        normalized = normalized[:-5] + normalized[-5:-2] + ":" + normalized[-2:]

    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt.isoformat(timespec="minutes")
    except Exception:
        return raw


def _display_caller_name(name: str | None, phone: str | None) -> str:
    cleaned_name = sanitize_caller_name(name)
    if cleaned_name:
        return cleaned_name

    resolved_phone = _resolve_display_phone(phone, name)
    return resolved_phone or "Unknown Caller"


def _resolve_display_phone(phone: str | None, fallback_text: str | None = None) -> str:
    """Return the best available caller number for UI display.

    Priority:
    1) normalized valid phone from `phone`
    2) normalized valid phone parsed from `fallback_text` (e.g., caller_name)
    3) non-placeholder raw `phone`
    4) non-placeholder raw `fallback_text`
    """
    def _is_plausible_phone(normalized_phone: str) -> bool:
      if not is_valid_phone_number(normalized_phone):
        return False
      digits_only = re.sub(r"\D", "", normalized_phone)
      # Reject short digit sequences (often dates/IDs) that passed generic E.164 checks.
      return len(digits_only) >= 10

    def _extract_phone_candidate(text: str | None) -> str:
      raw = str(text or "").strip()
      if not raw:
        return ""
      if re.fullmatch(r"\+?[\d\s\-()]{8,20}", raw):
        normalized = normalize_phone_number(raw)
        if _is_plausible_phone(normalized):
          return normalized

      matches = re.findall(r"\+?\d[\d\s\-()]{7,}\d", raw)
      for match in matches:
        normalized_match = normalize_phone_number(match)
        if _is_plausible_phone(normalized_match):
          return normalized_match
      return ""

    candidates = [phone, fallback_text]
    for candidate in candidates:
      extracted = _extract_phone_candidate(candidate)
      if extracted:
        return extracted

    phone_raw = str(phone or "").strip()
    if phone_raw and phone_raw.lower() not in _PHONE_PLACEHOLDERS:
        return phone_raw

    fallback_raw = str(fallback_text or "").strip()
    if fallback_raw and fallback_raw.lower() not in _PHONE_PLACEHOLDERS:
      extracted_fallback = _extract_phone_candidate(fallback_raw)
      if extracted_fallback:
        return extracted_fallback

    return ""


def _resolve_display_phone_from_row(row: dict | None) -> str:
    """Resolve caller phone using all available row fields."""
    if not isinstance(row, dict):
        return ""

    primary = _resolve_display_phone(row.get("phone_number"), row.get("caller_name"))
    if primary:
        return primary

    for extra in (
        row.get("recording_url"),
        row.get("room_id"),
        row.get("transcript"),
    ):
        resolved = _resolve_display_phone(extra, None)
        if resolved:
            return resolved
    return ""

def read_config():
    from config_store import read_full_config
    return read_full_config()


# ── Config caching ────────────────────────────────────────────────────────────
_config_cache: dict = {}
_config_cache_lock = asyncio.Lock()
_config_mtime: float = 0.0

async def read_config_cached() -> dict:
    global _config_cache, _config_mtime
    async with _config_cache_lock:
        try:
            mtime = os.path.getmtime(CONFIG_FILE)
        except OSError:
            mtime = 0.0
        if _config_cache and mtime == _config_mtime:
            return _config_cache
        _config_cache = read_config()
        _config_mtime = mtime
        return _config_cache

def invalidate_config_cache():
    global _config_mtime
    _config_mtime = 0.0


def _try_restart_voice_agent() -> dict | None:
    """Restart agent.py via supervisord when available."""
    try:
        from system_control import restart_voice_agent
        return restart_voice_agent()
    except Exception as exc:
        logger.warning("Voice agent restart failed: %s", exc)
        return {"success": False, "restarted": False, "message": str(exc)}


def _try_kill_and_run_voice_agent() -> dict | None:
    """Stop any running agent worker, then start fresh via supervisord."""
    try:
        from system_control import kill_and_run_voice_agent
        return kill_and_run_voice_agent()
    except Exception as exc:
        logger.warning("Voice agent kill-and-run failed: %s", exc)
        return {"success": False, "restarted": False, "message": str(exc)}


def _agent_preset_summary(full_config) -> dict:
    """Compact preset snapshot returned after Run Agent."""
    agent = full_config.agent
    return {
        "name": agent.name,
        "purpose": agent.purpose,
        "llm_provider": agent.llm_provider,
        "llm_model": agent.llm_model,
        "stt_provider": agent.stt_provider,
        "stt_model": agent.stt_model,
        "tts_provider": agent.tts_provider,
        "tts_voice_id": agent.tts_voice_id,
        "languages": agent.languages,
        "welcome_message": agent.welcome_message,
    }


KNOWN_TTS_PROVIDERS = {"sarvam", "elevenlabs", "deepgram", "gemini", "cartesia"}
KNOWN_LLM_PROVIDERS = {"gemini", "groq", "openai", "claude"}
API_SECRET_FIELDS = frozenset({
    "gemini_api_key", "openai_api_key", "supabase_key", "supabase_service_role_key",
    "livekit_api_secret", "supabase_s3_secret_key", "groq_api_key",
    "cal_api_key", "sarvam_api_key", "anthropic_api_key",
    "jwt_secret", "dashboard_password",
})

class ConfigUpdate(BaseModel):
    agent_name: Optional[str] = None
    first_line: Optional[str] = None
    agent_instructions: Optional[str] = None
    stt_min_endpointing_delay: Optional[float] = None
    stt_max_endpointing_delay: Optional[float] = None
    llm_model: Optional[str] = None
    llm_provider: Optional[str] = None
    gemini_model: Optional[str] = None
    groq_model: Optional[str] = None
    tts_voice: Optional[str] = None
    tts_language: Optional[str] = None
    tts_provider: Optional[str] = None
    stt_provider: Optional[str] = None
    stt_language: Optional[str] = None
    lang_preset: Optional[str] = None
    language: Optional[str] = None
    max_turns: Optional[int] = None
    llm_max_completion_tokens: Optional[int] = None
    llm_startup_probe: Optional[bool] = None
    tts_hard_word_cap: Optional[int] = None
    tone_preset: Optional[str] = None
    campaign_id: Optional[str] = None
    timezone: Optional[str] = None
    supabase_s3_region: Optional[str] = None

    @field_validator("tts_provider")
    @classmethod
    def validate_tts_provider(cls, v):
        if v is not None and v not in KNOWN_TTS_PROVIDERS:
            raise ValueError(f"Unknown tts_provider '{v}'. Must be one of: {', '.join(sorted(KNOWN_TTS_PROVIDERS))}")
        return v

    @field_validator("llm_provider")
    @classmethod
    def validate_llm_provider(cls, v):
        if v is not None and v not in KNOWN_LLM_PROVIDERS:
            raise ValueError(f"Unknown llm_provider '{v}'. Must be one of: {', '.join(sorted(KNOWN_LLM_PROVIDERS))}")
        return v

    @field_validator("stt_provider")
    @classmethod
    def validate_stt_provider(cls, v):
        if v is not None and v not in {"soniox", "sarvam", "deepgram"}:
            raise ValueError(f"Unknown stt_provider '{v}'. Must be one of: soniox, sarvam, deepgram")
        return v


def _apply_supabase_env_safe():
    """Set Supabase env vars from environment only (never from config.json)."""
    import db

    mapping = (
        ("SUPABASE_URL", "SUPABASE_URL"),
        ("SUPABASE_KEY", "SUPABASE_KEY"),
        ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
    )
    for env_key, _ in mapping:
        val = os.environ.get(env_key, "").strip()
        if val:
            os.environ[env_key] = val
    db.reset_supabase_clients()


def _recordings_bucket() -> str:
    return os.environ.get("SUPABASE_RECORDINGS_BUCKET", "call-recordings").strip() or "call-recordings"


def _extract_recording_object_path(recording_url: str | None, room_id: str | None) -> str:
    url = str(recording_url or "").strip()
    room = str(room_id or "").strip()
    bucket = _recordings_bucket()

    if url:
        public_marker = f"/storage/v1/object/public/{bucket}/"
        signed_marker = f"/storage/v1/object/sign/{bucket}/"
        legacy_public = "/storage/v1/object/public/call-recordings/"
        legacy_signed = "/storage/v1/object/sign/call-recordings/"
        for marker in (public_marker, signed_marker, legacy_public, legacy_signed):
            if marker in url:
                return url.split(marker, 1)[1].split("?", 1)[0]
        if not url.startswith("http"):
            return url.lstrip("/")

    if room:
        return f"recordings/{room}.ogg"
    return ""


def _resolve_recording_url(
    supabase_client,
    recording_url: str | None,
    room_id: str | None,
    *,
    sign: bool = True,
) -> str:
    current = str(recording_url or "").strip()
    # Keep already usable HTTP URL if present.
    if current.startswith("http") and (
        "/storage/v1/object/sign/" in current or "/storage/v1/object/public/" in current
    ):
        return current

    object_path = _extract_recording_object_path(current, room_id)
    if not object_path:
        return current

    bucket = _recordings_bucket()
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    public_url = (
        f"{supabase_url}/storage/v1/object/public/{bucket}/{object_path}"
        if supabase_url
        else ""
    )

    if not sign or not supabase_client:
        return public_url or current

    # Try signed URL first (works for private buckets)
    try:
        signed = supabase_client.storage.from_(bucket).create_signed_url(object_path, 24 * 60 * 60)
        if isinstance(signed, dict):
            return str(
                signed.get("signedURL")
                or signed.get("signedUrl")
                or signed.get("signed_url")
                or signed.get("url")
                or public_url
                or current
            )
        return str(signed or public_url or current)
    except Exception:
        return public_url or current

_SECRET_KEYS = frozenset({
    "livekit_url", "livekit_api_key", "livekit_api_secret", "sip_trunk_id",
    "gemini_api_key", "openai_api_key", "sarvam_api_key", "groq_api_key",
    "cal_api_key", "supabase_url", "supabase_key", "supabase_service_role_key",
    "supabase_s3_access_key", "supabase_s3_secret_key", "supabase_s3_endpoint",
    "supabase_s3_region", "anthropic_api_key", "deepgram_api_key",
    "dashboard_username", "dashboard_password", "jwt_secret",
    "sentry_dsn", "twilio_account_sid", "twilio_auth_token", "twilio_whatsapp_number",
})

def write_config(data):
    config = read_config()
    cleaned = {k: v for k, v in data.items() if k not in _SECRET_KEYS}
    preset_changed = (
        "lang_preset" in cleaned
        and str(cleaned.get("lang_preset") or "").strip().lower()
        != str(config.get("lang_preset") or "").strip().lower()
    )
    config.update(cleaned)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    if preset_changed or any(k in cleaned for k in ("languages", "first_line", "user_idle", "call_ending")):
        try:
            from worker_prewarm import invalidate_worker_prewarm

            invalidate_worker_prewarm()
        except Exception:
            pass

MASKED_FIELDS = {
    "gemini_api_key", "openai_api_key", "groq_api_key", "supabase_key",
    "livekit_api_secret", "cal_api_key", "supabase_url",
}

def sanitize_config_for_client(config: dict) -> dict:
    return {
        k: ("••••" + str(v)[-4:] if k in MASKED_FIELDS and v else v)
        for k, v in config.items()
    }

# ── Dashboard routes (analytics, leads, reports, settings) ────────────────────
from dashboard_routes import router as dashboard_router
from org_routes import router as org_router
from platform_routes import router as platform_router
from docs_routes import router as docs_router
from docs_status_routes import router as docs_status_router
from whatsapp_routes import router as whatsapp_router
from whatsapp_chat_routes import router as whatsapp_chat_router

app.include_router(dashboard_router)
app.include_router(org_router)
app.include_router(platform_router)
app.include_router(docs_router)
app.include_router(docs_status_router)
app.include_router(whatsapp_router)
app.include_router(whatsapp_chat_router)


@app.on_event("startup")
async def _bootstrap_tenancy():
    try:
        ensure_bootstrap()
    except Exception as exc:
        logger.warning("Tenancy bootstrap skipped: %s", exc)


# ── API Endpoints ──────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def login(request: Request):
    from org_routes import LoginBody, perform_login

    data = await request.json()
    body = LoginBody(
        email=data.get("email"),
        username=data.get("username"),
        password=data.get("password", ""),
        org_id=data.get("org_id"),
        org_code=data.get("org_code"),
    )
    payload = perform_login(body)
    response = JSONResponse(payload)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=payload["access_token"],
        httponly=True,
        secure=os.environ.get("ENVIRONMENT", "development") == "production",
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@app.post("/api/auth/logout")
async def logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")
    return response


@app.get("/login", response_class=HTMLResponse)
async def get_login_page():
    login_html = os.path.join(FRONTEND_DIR, "login.html")
    if os.path.exists(login_html):
        with open(login_html, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Login page not found")

@app.get("/api/config")
async def api_get_config(user: str = Depends(require_auth)):
    return sanitize_config_for_client(read_config())

@app.api_route("/api/config", methods=["POST"])
async def api_post_config(request: Request, user: str = Depends(require_auth)):
    raw_data = await request.json()
    cleaned = {k: v for k, v in raw_data.items() if k not in API_SECRET_FIELDS}
    update = ConfigUpdate(**cleaned)
    update_dict = {k: v for k, v in update.model_dump().items() if v is not None}
    from config_store import write_full_config
    write_full_config(update_dict)
    invalidate_config_cache()
    logger.info("Configuration updated via UI.")
    return {"status": "success", "config": sanitize_config_for_client(read_config())}


class MainAgentSaveRequest(BaseModel):
    agent_name: Optional[str] = None
    first_line: Optional[str] = None
    agent_instructions: Optional[str] = None
    language: Optional[str] = None
    lang_preset: Optional[str] = None
    tts_provider: Optional[str] = None
    stt_provider: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    gemini_model: Optional[str] = None
    groq_model: Optional[str] = None
    llm_max_completion_tokens: Optional[int] = None
    llm_startup_probe: Optional[bool] = None
    max_turns: Optional[int] = None
    tts_voice: Optional[str] = None
    tts_language: Optional[str] = None
    stt_language: Optional[str] = None
    stt_min_endpointing_delay: Optional[float] = None
    stt_max_endpointing_delay: Optional[float] = None
    tts_hard_word_cap: Optional[int] = None
    timezone: Optional[str] = None
    tone_preset: Optional[str] = None
    campaign_id: Optional[str] = None
    restart_agent: bool = True
    use_file_config: bool = True


@app.get("/api/main-agent")
async def api_get_main_agent(user: str = Depends(require_auth)):
    from config_store import main_agent_payload
    from system_control import get_agent_process_status

    payload = main_agent_payload()
    payload["process"] = get_agent_process_status()
    return payload


@app.put("/api/main-agent")
async def api_save_main_agent(body: MainAgentSaveRequest, user: str = Depends(require_auth)):
    from config_store import main_agent_payload, write_full_config
    from env_manager import delete_env_keys
    from system_control import get_agent_process_status

    update_dict = {k: v for k, v in body.model_dump(exclude={"restart_agent", "use_file_config"}).items() if v is not None}
    if not update_dict.get("agent_name"):
        update_dict["agent_name"] = "inbound-voice-demo"

    saved = write_full_config(update_dict)
    invalidate_config_cache()

    removed_env: list[str] = []
    if body.use_file_config:
        removed_env = delete_env_keys(["DEFAULT_AGENT_ID"])

    restart_result = None
    if body.restart_agent:
        restart_result = _try_restart_voice_agent()

    return {
        "success": True,
        "config": saved,
        "source": "config.json",
        "default_agent_id_cleared": bool(removed_env),
        "restart": restart_result,
        "process": get_agent_process_status(),
        "message": (
            "Main agent saved and restarted."
            if restart_result and restart_result.get("success")
            else "Main agent saved. Restart agent manually if calls still use old settings."
        ),
    }


@app.post("/api/system/restart-agent")
async def api_restart_agent(user: str = Depends(require_auth)):
    from agent_process import restart_agent_fresh

    result = restart_agent_fresh()
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/api/system/agent-status")
async def api_agent_status(user: str = Depends(require_auth)):
    from agent_process import get_detailed_agent_status
    from config_store import main_agent_payload

    status = get_detailed_agent_status()
    status["main_agent"] = main_agent_payload()
    return status


@app.post("/api/system/kill-agent")
async def api_kill_agent(user: str = Depends(require_auth)):
    from agent_process import get_detailed_agent_status, kill_agent_processes

    result = kill_agent_processes(force=False)
    if not result.get("success") and result.get("remaining_pids"):
        result = kill_agent_processes(force=True)
    result["status"] = get_detailed_agent_status()
    if not result.get("success") and result.get("remaining_pids"):
        raise HTTPException(status_code=503, detail=result)
    return result


@app.post("/api/system/run-dev")
async def api_run_dev_agent(user: str = Depends(require_auth)):
    from agent_process import get_detailed_agent_status, start_dev_agent

    result = start_dev_agent(reload=True)
    result["status"] = get_detailed_agent_status()
    if not result.get("success"):
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/api/system/agent-logs")
async def api_agent_logs(
    user: str = Depends(require_auth),
    tail: int = Query(200, ge=10, le=2000),
):
    from agent_process import tail_agent_log

    text = tail_agent_log()
    lines = text.splitlines()[-tail:] if text else []
    return {"lines": lines, "text": "\n".join(lines)}


@app.get("/api/assistants/settings/catalog")
async def api_assistant_settings_catalog(user: str = Depends(require_auth)):
    from assistant_settings import catalog_payload

    return catalog_payload()


@app.get("/api/assistants/{assistant_id}/settings")
async def api_get_assistant_settings(assistant_id: str, user: str = Depends(require_auth)):
    from assistant_settings import catalog_payload, load_main_assistant_settings

    if assistant_id not in ("main", "default"):
        return await _get_db_assistant_settings(assistant_id)
    settings = load_main_assistant_settings()
    return {
        "assistant_id": assistant_id,
        "settings": settings.model_dump(),
        "catalog": catalog_payload(),
    }


@app.put("/api/assistants/{assistant_id}/settings")
async def api_put_assistant_settings(
    assistant_id: str,
    body: dict,
    user: str = Depends(require_auth),
):
    from assistant_settings import AssistantSettings, catalog_payload, save_main_assistant_settings

    if assistant_id not in ("main", "default"):
        return await _put_db_assistant_settings(assistant_id, body)

    try:
        settings = AssistantSettings.model_validate(body.get("settings") or body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    saved = save_main_assistant_settings(settings)
    invalidate_config_cache()
    return {
        "success": True,
        "assistant_id": assistant_id,
        "settings": settings.model_dump(),
        "config": saved,
        "catalog": catalog_payload(),
    }


@app.post("/api/assistants/{assistant_id}/settings/apply")
async def api_apply_assistant_settings(
    assistant_id: str,
    body: dict | None = None,
    user: str = Depends(require_auth),
):
    from assistant_settings import AssistantSettings, save_main_assistant_settings, touch_reload_signal

    restart = True
    if body and isinstance(body, dict):
        restart = bool(body.get("restart_agent", True))

    if assistant_id not in ("main", "default"):
        result = await _put_db_assistant_settings(assistant_id, body or {})
        if restart:
            result["restart"] = _try_kill_and_run_voice_agent()
        return result

    raw_settings = (body or {}).get("settings")
    if raw_settings:
        settings = AssistantSettings.model_validate(raw_settings)
        save_main_assistant_settings(settings)
    else:
        touch_reload_signal()
    invalidate_config_cache()

    restart_result = _try_kill_and_run_voice_agent() if restart else None
    from agent_process import get_detailed_agent_status

    return {
        "success": True,
        "message": "Settings applied",
        "restart": restart_result,
        "status": get_detailed_agent_status(),
    }


@app.get("/api/lang-presets")
async def api_list_lang_presets(user: str = Depends(require_auth)):
    from lang_presets_store import list_presets

    return list_presets()


@app.put("/api/lang-presets/{preset_id}")
async def api_update_lang_preset(
    preset_id: str,
    body: dict,
    user: str = Depends(require_auth),
):
    from lang_presets_store import update_preset

    try:
        updated = update_preset(preset_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "preset": updated}


@app.post("/api/lang-presets/{preset_id}/activate")
async def api_activate_lang_preset(
    preset_id: str,
    body: dict | None = None,
    user: str = Depends(require_auth),
):
    from lang_presets_store import activate_preset

    restart = True
    if body and isinstance(body, dict):
        restart = bool(body.get("restart_agent", True))
    try:
        result = activate_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invalidate_config_cache()
    restart_result = _try_kill_and_run_voice_agent() if restart else None
    from agent_process import get_detailed_agent_status

    return {
        "success": True,
        "active_preset_id": preset_id,
        "preset": result.get("preset"),
        "config_updates": result.get("config_updates"),
        "restart": restart_result,
        "status": get_detailed_agent_status(),
    }


@app.post("/api/lang-presets/{preset_id}/toggle")
async def api_toggle_lang_preset(
    preset_id: str,
    body: dict,
    user: str = Depends(require_auth),
):
    from lang_presets_store import set_preset_enabled

    enabled = bool((body or {}).get("enabled", True))
    try:
        preset = set_preset_enabled(preset_id, enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "preset": preset}


async def _get_db_assistant_settings(agent_id: str):
    import agents_db
    from assistant_settings import AssistantSettings, catalog_payload, settings_from_flat_config

    try:
        full = await agents_db.get_agent_config(agent_id)
    except Exception as exc:
        raise _agents_db_error(exc) from exc
    if not full:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = full.agent
    flat = {
        "agent_name": agent.name,
        "agent_instructions": full.merged_system_prompt(),
        "first_line": agent.welcome_message or "",
        "languages": agent.languages,
        "language": (agent.languages or ["te"])[0],
        "tts_provider": agent.tts_provider,
        "tts_voice": agent.tts_voice_id,
        "llm_provider": agent.llm_provider,
        "gemini_model": agent.llm_model,
        "llm_model": agent.llm_model,
        "llm_temperature": agent.temperature,
        "stt_provider": agent.stt_provider,
        "stt_model": agent.stt_model,
        "max_turns": 40,
        "max_call_duration_seconds": agent.max_call_duration_seconds,
    }
    settings = settings_from_flat_config(flat, assistant_id=agent_id)
    return {
        "assistant_id": agent_id,
        "settings": settings.model_dump(),
        "catalog": catalog_payload(),
    }


async def _put_db_assistant_settings(agent_id: str, body: dict):
    import agents_db
    from agent_models import AgentUpdate, ConversationalFlowUpdate, FlowSection
    from assistant_settings import AssistantSettings, flat_updates_from_settings

    try:
        raw = body.get("settings") or body
        settings = AssistantSettings.model_validate(raw)
        flat = flat_updates_from_settings(settings)
        await agents_db.update_agent(
            agent_id,
            AgentUpdate(
                name=settings.name,
                system_prompt=settings.system_prompt,
                languages=settings.languages,
                llm_provider=settings.llm.provider,
                llm_model=settings.llm.model,
                temperature=settings.llm.temperature,
                stt_provider=settings.stt.provider,
                stt_model=settings.stt.model,
                tts_provider=settings.voice.provider,
                tts_voice_id=settings.voice.voice_id,
                welcome_message=settings.welcome.message,
                max_call_duration_seconds=settings.max_call_duration_seconds,
            ),
        )
        sections = [
            FlowSection(**settings.flow.identity_purpose.model_dump()),
            FlowSection(**settings.flow.facts.model_dump()),
            FlowSection(**settings.flow.actions_limits.model_dump()),
        ]
        await agents_db.update_conversational_flow(
            agent_id,
            ConversationalFlowUpdate(sections=sections),
        )
        invalidate_config_cache()
        return {"success": True, "assistant_id": agent_id, "settings": settings.model_dump()}
    except Exception as exc:
        raise _agents_db_error(exc) from exc


# ── Voice AI Agents API ───────────────────────────────────────────────────────

def _agents_db_error(exc: Exception) -> HTTPException:
    import agents_db

    if isinstance(exc, agents_db.AgentsDbError):
        status = 500
        if exc.code in {"not_configured", "migration_required", "rls_denied"}:
            status = 503
        elif exc.code == "not_found":
            status = 404
        return HTTPException(status_code=status, detail={"success": False, "error": str(exc), "code": exc.code})
    return HTTPException(status_code=500, detail={"success": False, "error": str(exc), "code": "internal_error"})


def _agent_ok(data, **extra):
    payload = {"success": True, "data": data}
    payload.update(extra)
    return payload


def _enrich_call_log_row(
    row: dict,
    sb,
    agent_names: dict[str, str] | None = None,
    *,
    sign_recording: bool = True,
) -> dict | None:
    """Normalize phones, caller name, recording URL, and agent name for API responses."""
    if not isinstance(row, dict):
        return None

    normalized_phone = normalize_phone_number(row.get("phone_number"))
    if is_demo_phone(normalized_phone):
        return None

    row_data = dict(row)
    row_data["phone_number"] = _resolve_display_phone_from_row(row_data)
    row_data["from_number"] = _resolve_display_phone(row_data.get("from_number"), None) or row_data.get("from_number")
    row_data["to_number"] = _resolve_display_phone(row_data.get("to_number"), None) or row_data.get("to_number")
    row_data["caller_name"] = _display_caller_name(row_data.get("caller_name"), row_data.get("phone_number"))
    has_recording = bool(
        str(row_data.get("recording_url") or "").strip()
        or str(row_data.get("room_id") or "").strip()
    )
    row_data["recording_url"] = _resolve_recording_url(
        sb,
        row_data.get("recording_url"),
        row_data.get("room_id") or str(row_data.get("id", "")).replace("recovered-", ""),
        sign=sign_recording or has_recording,
    )
    agent_id = str(row_data.get("agent_id") or "")
    if agent_names is not None:
        row_data["agent_name"] = agent_names.get(agent_id) or (
            "Unassigned" if not agent_id else "Unknown Agent"
        )
    return row_data


def _build_calls_query(
    *,
    agent_id: Optional[str] = None,
    bulk_call_id: Optional[str] = None,
    bulk_only: bool = False,
    call_status: Optional[str] = None,
    call_direction: Optional[str] = None,
    channel_type: Optional[str] = None,
    call_transferred: Optional[bool] = None,
    duration_min: Optional[int] = None,
    duration_max: Optional[int] = None,
    call_id: Optional[str] = None,
    from_number: Optional[str] = None,
    to_number: Optional[str] = None,
    phone: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    max_rows: Optional[int] = None,
):
    from call_logs_db import CallLogsQuery

    return CallLogsQuery(
        agent_id=agent_id,
        bulk_call_id=bulk_call_id,
        bulk_only=bulk_only,
        call_status=call_status,
        call_direction=call_direction,
        channel_type=channel_type,
        call_transferred=call_transferred,
        duration_min=duration_min,
        duration_max=duration_max,
        call_id=call_id,
        from_number=from_number,
        to_number=to_number,
        phone=phone,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        max_rows=max_rows,
    )


@app.get("/api/agents")
async def api_list_agents(
    user: str = Depends(require_auth),
    is_active: Optional[bool] = Query(default=None),
    purpose: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    _apply_supabase_env_safe()
    import agents_db

    try:
        rows = await agents_db.list_agents(
            is_active=is_active,
            purpose=purpose,
            limit=limit,
            offset=offset,
        )
        return _agent_ok(rows, total=len(rows), limit=limit, offset=offset)
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.post("/api/agents/create-from-description")
async def api_create_agent_from_description(request: Request, user: str = Depends(require_auth)):
    """Generate full agent config from natural language + use cases, then persist."""
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentCreateFromDescription
    from agent_generator import generate_agent_from_description

    try:
        body = await request.json()
        payload = AgentCreateFromDescription.model_validate(body)
        create_payload = await generate_agent_from_description(
            payload.description,
            payload.use_cases,
        )
        config = await agents_db.create_agent(create_payload)
        data = config.model_dump(mode="json")
        data["merged_system_prompt"] = config.merged_system_prompt()
        return _agent_ok(data, generated=True)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"success": False, "error": str(exc), "code": "validation_error"}) from exc
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.post("/api/agents")
async def api_create_agent(request: Request, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentCreateFull

    try:
        body = await request.json()
        payload = AgentCreateFull.model_validate(body)
        config = await agents_db.create_agent(payload)
        data = config.model_dump(mode="json")
        data["merged_system_prompt"] = config.merged_system_prompt()
        return _agent_ok(data)
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}")
async def api_get_agent(agent_id: UUID, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db

    try:
        data = await agents_db.get_agent_config_dict(agent_id)
        if not data:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})
        return _agent_ok(data)
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}/config")
async def api_get_agent_config(agent_id: UUID, user: str = Depends(require_auth)):
    """Full merged config for dashboard and LiveKit worker."""
    return await api_get_agent(agent_id, user)


@app.put("/api/agents/{agent_id}")
async def api_update_agent(agent_id: UUID, request: Request, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import AgentUpdate

    try:
        body = await request.json()
        payload = AgentUpdate.model_validate(body)
        agent = await agents_db.update_agent(agent_id, payload)
        return _agent_ok(agent.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


async def _deploy_live_agent(agent_id: UUID) -> dict:
    """
    Run one dashboard agent live:
    - Deactivate every other agent in the database
    - Set DEFAULT_AGENT_ID so config.json / main agent does not override
    - Kill the voice worker and start it again with the new default
    """
    import agents_db
    from env_manager import update_env_keys
    from system_control import get_agent_process_status

    agent = await agents_db.activate_agent(agent_id)
    full = await agents_db.get_agent_config(agent_id)
    if not full:
        raise HTTPException(
            status_code=404,
            detail={"success": False, "error": "Agent not found", "code": "not_found"},
        )

    applied = update_env_keys({"DEFAULT_AGENT_ID": str(agent.id)})
    os.environ["DEFAULT_AGENT_ID"] = str(agent.id)
    run_result = _try_kill_and_run_voice_agent()
    process = get_agent_process_status()

    restarted = bool(run_result and run_result.get("success"))
    message = (
        f"“{agent.name}” is now running live with its saved preset."
        if restarted
        else f"“{agent.name}” is set as live agent. Restart agent.py manually if calls still use old settings."
    )
    worker_hint = (
        "All other agents are paused. LiveKit worker loads this agent's full DB config on each call."
    )
    if restarted:
        worker_hint += " Voice worker stopped and restarted via supervisord."
    else:
        worker_hint += " If agent.py is not running, start it with: python agent.py start"

    return {
        "agent": agent.model_dump(mode="json"),
        "preset": _agent_preset_summary(full),
        "default_agent_id": str(agent.id),
        "agents_paused": True,
        "env_updated": bool(applied),
        "restart": run_result,
        "process": process,
        "worker_hint": worker_hint,
        "message": message,
    }


@app.post("/api/agents/{agent_id}/run")
async def api_run_agent(agent_id: UUID, user: str = Depends(require_auth)):
    """Kill all other agents, then run this agent with its saved preset."""
    _apply_supabase_env_safe()
    try:
        payload = await _deploy_live_agent(agent_id)
        return _agent_ok(payload, running=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.post("/api/agents/{agent_id}/activate")
async def api_activate_agent(agent_id: UUID, user: str = Depends(require_auth)):
    """Alias for /run — kept for older dashboard clients."""
    _apply_supabase_env_safe()
    try:
        payload = await _deploy_live_agent(agent_id)
        return _agent_ok(payload, activated=True, running=True)
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.delete("/api/agents/{agent_id}")
async def api_delete_agent(agent_id: UUID, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from env_manager import update_env_keys

    try:
        existing = await agents_db.get_agent_row(agent_id)
        if not existing:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})

        was_active = bool(existing.get("is_active"))
        deleted = await agents_db.delete_agent(agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})

        env_cleared = False
        restart_result = None
        if was_active:
            remaining = await agents_db.list_agents(limit=1)
            if remaining:
                next_agent = await agents_db.activate_agent(remaining[0]["id"])
                update_env_keys({"DEFAULT_AGENT_ID": str(next_agent.id)})
                os.environ["DEFAULT_AGENT_ID"] = str(next_agent.id)
            else:
                update_env_keys({"DEFAULT_AGENT_ID": ""})
                os.environ.pop("DEFAULT_AGENT_ID", None)
                env_cleared = True
            restart_result = _try_kill_and_run_voice_agent()

        return {
            "success": True,
            "deleted": True,
            "agent_id": str(agent_id),
            "was_active": was_active,
            "env_cleared": env_cleared,
            "restart": restart_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}/calls")
async def api_get_agent_calls(
    agent_id: UUID,
    user: str = Depends(require_auth),
    limit: int = Query(default=50, ge=1, le=200),
):
    _apply_supabase_env_safe()
    import agents_db

    try:
        import db
        from call_logs_db import fetch_agent_name_map, fetch_filtered_calls

        params = _build_calls_query(agent_id=str(agent_id), page=1, page_size=limit)
        rows, total = await fetch_filtered_calls(params)
        sb = await db.get_supabase()
        agent_names = await fetch_agent_name_map()
        cleaned = []
        for row in rows:
            enriched = _enrich_call_log_row(row, sb, agent_names, sign_recording=False)
            if enriched:
                cleaned.append(enriched)
        return _agent_ok(cleaned, total=total or len(cleaned))
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}/stats")
async def api_get_agent_stats(agent_id: UUID, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db

    try:
        stats = await agents_db.get_agent_stats(agent_id)
        return _agent_ok(stats)
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.put("/api/agents/{agent_id}/conversational-flow")
async def api_update_conversational_flow(agent_id: UUID, request: Request, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import ConversationalFlowUpdate

    try:
        body = await request.json()
        payload = ConversationalFlowUpdate.model_validate(body)
        flow = await agents_db.update_conversational_flow(agent_id, payload)
        return _agent_ok(flow.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.put("/api/agents/{agent_id}/call-configuration")
async def api_update_call_configuration(agent_id: UUID, request: Request, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import CallConfigurationUpdate

    try:
        body = await request.json()
        payload = CallConfigurationUpdate.model_validate(body)
        config = await agents_db.update_call_configuration(agent_id, payload)
        return _agent_ok(config.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.put("/api/agents/{agent_id}/post-call-actions")
async def api_update_post_call_actions(agent_id: UUID, request: Request, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    from agent_models import PostCallActionUpdate

    try:
        body = await request.json()
        payload = PostCallActionUpdate.model_validate(body)
        actions = await agents_db.update_post_call_actions(agent_id, payload)
        return _agent_ok(actions.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}/versions")
async def api_list_agent_versions(
    agent_id: UUID,
    user: str = Depends(require_auth),
    limit: int = Query(default=30, ge=1, le=100),
):
    _apply_supabase_env_safe()
    import agents_db
    import agent_version_store

    try:
        if not await agents_db.get_agent_row(agent_id):
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})
        versions = agent_version_store.list_versions(str(agent_id), limit=limit)
        return _agent_ok(versions, total=len(versions))
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.get("/api/agents/{agent_id}/versions/{version_id}")
async def api_get_agent_version(agent_id: UUID, version_id: str, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import agents_db
    import agent_version_store

    try:
        if not await agents_db.get_agent_row(agent_id):
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})
        version = agent_version_store.get_version(str(agent_id), version_id)
        if not version:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Version not found", "code": "not_found"})
        return _agent_ok(version)
    except HTTPException:
        raise
    except Exception as exc:
        raise _agents_db_error(exc) from exc


@app.post("/api/agents/{agent_id}/ai-edit")
async def api_agent_ai_edit(agent_id: UUID, request: Request, user: str = Depends(require_auth)):
    """Apply natural-language edits to the full agent configuration with version history."""
    _apply_supabase_env_safe()
    import agents_db
    import agent_ai_edit
    import agent_version_store
    from agent_models import AgentAiEditRequest

    try:
        body = await request.json()
        payload = AgentAiEditRequest.model_validate(body)
        current = await agents_db.get_agent_config_dict(agent_id)
        if not current:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})

        patch = await agent_ai_edit.generate_ai_edit_patch(current, payload.prompt)
        updates = agent_ai_edit.patch_to_updates(patch)

        if payload.preview_only:
            return _agent_ok(
                {
                    "summary": patch.summary,
                    "applied_by": patch.applied_by,
                    "patch": patch.model_dump(),
                    "preview": True,
                },
                preview=True,
            )

        before_entry = agent_version_store.save_version(
            str(agent_id),
            current,
            source="pre_ai_edit",
            prompt=payload.prompt,
            summary="Snapshot before AI edit",
            edited_by=user,
        )

        config = await agents_db.apply_agent_patch(agent_id, updates)
        after_payload = config.model_dump(mode="json")
        after_payload["merged_system_prompt"] = config.merged_system_prompt()

        after_entry = agent_version_store.save_version(
            str(agent_id),
            after_payload,
            source="ai_edit",
            prompt=payload.prompt,
            summary=patch.summary or "AI configuration update",
            edited_by=user,
        )

        restart_result = None
        live_agent_id = (os.getenv("DEFAULT_AGENT_ID") or "").strip()
        if payload.restart_if_live and live_agent_id == str(agent_id):
            restart_result = _try_kill_and_run_voice_agent()

        return _agent_ok(
            {
                "config": after_payload,
                "summary": patch.summary,
                "applied_by": patch.applied_by,
                "version_before": before_entry["id"],
                "version_after": after_entry["id"],
                "restart": restart_result,
            },
            ai_edit=True,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"success": False, "error": str(exc), "code": "validation_error"}) from exc
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.post("/api/agents/{agent_id}/versions/{version_id}/restore")
async def api_restore_agent_version(agent_id: UUID, version_id: str, request: Request, user: str = Depends(require_auth)):
    """Restore a prior configuration snapshot."""
    _apply_supabase_env_safe()
    import agents_db
    import agent_version_store
    from agent_models import AgentUpdate, CallConfigurationUpdate, ConversationalFlowUpdate, FlowSection, PostCallActionUpdate

    try:
        body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
        restart_if_live = bool((body or {}).get("restart_if_live", True))

        current = await agents_db.get_agent_config_dict(agent_id)
        if not current:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Agent not found", "code": "not_found"})

        version = agent_version_store.get_version(str(agent_id), version_id)
        if not version:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Version not found", "code": "not_found"})

        snapshot = version.get("snapshot") or {}
        agent_version_store.save_version(
            str(agent_id),
            current,
            source="pre_restore",
            summary=f"Snapshot before restore to {version_id}",
            edited_by=user,
        )

        updates: dict[str, Any] = {}
        agent_data = snapshot.get("agent") or {}
        if agent_data:
            allowed = {
                "name", "description", "purpose", "system_prompt", "languages",
                "llm_provider", "llm_model", "temperature", "stt_provider", "stt_model",
                "tts_provider", "tts_voice_id", "welcome_message", "max_call_duration_seconds",
                "is_active", "metadata",
            }
            updates["agent"] = AgentUpdate.model_validate({k: v for k, v in agent_data.items() if k in allowed})

        flow_data = snapshot.get("conversational_flow") or {}
        if flow_data:
            sections = flow_data.get("sections") or []
            updates["conversational_flow"] = ConversationalFlowUpdate.model_validate(
                {
                    "sections": [FlowSection.model_validate(s) for s in sections],
                    "version": flow_data.get("version"),
                }
            )

        call_data = snapshot.get("call_configuration") or {}
        if call_data:
            updates["call_configuration"] = CallConfigurationUpdate.model_validate(call_data)

        post_data = snapshot.get("post_call_action") or {}
        if post_data:
            updates["post_call_action"] = PostCallActionUpdate.model_validate(post_data)

        config = await agents_db.apply_agent_patch(agent_id, updates)
        restored_payload = config.model_dump(mode="json")
        restored_payload["merged_system_prompt"] = config.merged_system_prompt()

        restore_entry = agent_version_store.save_version(
            str(agent_id),
            restored_payload,
            source="restore",
            summary=f"Restored version {version_id}",
            edited_by=user,
        )

        restart_result = None
        live_agent_id = (os.getenv("DEFAULT_AGENT_ID") or "").strip()
        if restart_if_live and live_agent_id == str(agent_id):
            restart_result = _try_kill_and_run_voice_agent()

        return _agent_ok(
            {
                "config": restored_payload,
                "restored_from": version_id,
                "version_id": restore_entry["id"],
                "restart": restart_result,
            },
            restored=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise HTTPException(status_code=422, detail={"success": False, "error": exc.errors()}) from exc
        raise _agents_db_error(exc) from exc


@app.get("/api/calls")
async def api_get_calls(
    user: str = Depends(require_auth),
    agent_id: Optional[str] = Query(default=None),
    bulk_call_id: Optional[str] = Query(default=None),
    bulk_only: bool = Query(default=False),
    call_status: Optional[str] = Query(default=None),
    call_direction: Optional[str] = Query(default=None),
    channel_type: Optional[str] = Query(default=None),
    call_transferred: Optional[bool] = Query(default=None),
    duration_min: Optional[int] = Query(default=None, ge=0),
    duration_max: Optional[int] = Query(default=None, ge=0),
    call_id: Optional[str] = Query(default=None),
    from_number: Optional[str] = Query(default=None),
    to_number: Optional[str] = Query(default=None),
    phone: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
):
    _apply_supabase_env_safe()
    import db
    from call_logs_db import fetch_agent_name_map, fetch_filtered_calls, pagination_bounds

    try:
        params = _build_calls_query(
            agent_id=agent_id,
            bulk_call_id=bulk_call_id,
            bulk_only=bulk_only,
            call_status=call_status,
            call_direction=call_direction,
            channel_type=channel_type,
            call_transferred=call_transferred,
            duration_min=duration_min,
            duration_max=duration_max,
            call_id=call_id,
            from_number=from_number,
            to_number=to_number,
            phone=phone,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        rows, total = await fetch_filtered_calls(params)
        sb = await db.get_supabase()
        agent_names = await fetch_agent_name_map()
        cleaned = []
        for row in rows:
            enriched = _enrich_call_log_row(row, sb, agent_names, sign_recording=False)
            if enriched:
                cleaned.append(enriched)

        _, size = pagination_bounds(params)
        total_pages = max(1, (total + size - 1) // size) if total else 1
        return _agent_ok(
            cleaned,
            total=total,
            page=page,
            page_size=size,
            total_pages=total_pages,
        )
    except Exception as exc:
        logger.error("Error fetching calls: %s", exc)
        raise HTTPException(status_code=500, detail={"success": False, "error": str(exc)}) from exc


@app.get("/api/calls/export/csv")
async def api_export_calls_csv(
    user: str = Depends(require_auth),
    agent_id: Optional[str] = Query(default=None),
    bulk_call_id: Optional[str] = Query(default=None),
    bulk_only: bool = Query(default=False),
    call_status: Optional[str] = Query(default=None),
    call_direction: Optional[str] = Query(default=None),
    channel_type: Optional[str] = Query(default=None),
    call_transferred: Optional[bool] = Query(default=None),
    duration_min: Optional[int] = Query(default=None, ge=0),
    duration_max: Optional[int] = Query(default=None, ge=0),
    call_id: Optional[str] = Query(default=None),
    from_number: Optional[str] = Query(default=None),
    to_number: Optional[str] = Query(default=None),
    phone: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
):
    _apply_supabase_env_safe()
    import db
    from call_logs_db import fetch_agent_name_map, fetch_filtered_calls

    try:
        params = _build_calls_query(
            agent_id=agent_id,
            bulk_call_id=bulk_call_id,
            bulk_only=bulk_only,
            call_status=call_status,
            call_direction=call_direction,
            channel_type=channel_type,
            call_transferred=call_transferred,
            duration_min=duration_min,
            duration_max=duration_max,
            call_id=call_id,
            from_number=from_number,
            to_number=to_number,
            phone=phone,
            date_from=date_from,
            date_to=date_to,
            page=1,
            page_size=5000,
            max_rows=5000,
        )
        rows, _ = await fetch_filtered_calls(params)
        sb = await db.get_supabase()
        agent_names = await fetch_agent_name_map()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Call ID",
            "Call Date",
            "Bot Name",
            "From",
            "To",
            "Duration (s)",
            "Call Type",
            "Status",
            "Ended By",
            "Transferred",
            "Summary",
        ])
        for row in rows:
            enriched = _enrich_call_log_row(row, sb, agent_names, sign_recording=False)
            if not enriched:
                continue
            writer.writerow([
                enriched.get("id", ""),
                enriched.get("created_at", ""),
                enriched.get("agent_name", ""),
                enriched.get("from_number") or enriched.get("phone_number", ""),
                enriched.get("to_number", ""),
                enriched.get("duration_seconds") or 0,
                enriched.get("call_direction", ""),
                enriched.get("call_status", ""),
                enriched.get("ended_by", ""),
                "yes" if enriched.get("call_transferred") else "no",
                enriched.get("call_summary") or enriched.get("summary", ""),
            ])

        filename = f"call_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Error exporting calls CSV: %s", exc)
        raise HTTPException(status_code=500, detail={"success": False, "error": str(exc)}) from exc


@app.get("/api/calls/{call_id}")
async def api_get_call_detail(call_id: str, user: str = Depends(require_auth)):
    _apply_supabase_env_safe()
    import db
    from call_logs_db import fetch_agent_name_map, fetch_call_by_id

    try:
        if str(call_id).startswith("recovered-"):
            for item in (await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=True) or []):
                if str(item.get("id")) == str(call_id):
                    sb = await db.get_supabase()
                    agent_names = await fetch_agent_name_map()
                    enriched = _enrich_call_log_row(item, sb, agent_names)
                    if enriched:
                        return _agent_ok(enriched)
            raise HTTPException(status_code=404, detail={"success": False, "error": "Call not found"})

        row = await fetch_call_by_id(call_id)
        if not row:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Call not found"})

        sb = await db.get_supabase()
        agent_names = await fetch_agent_name_map()
        enriched = _enrich_call_log_row(row, sb, agent_names)
        if not enriched:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Call not found"})
        return _agent_ok(enriched)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error fetching call detail: %s", exc)
        raise HTTPException(status_code=500, detail={"success": False, "error": str(exc)}) from exc


@app.get("/api/calls/{call_id}/recording-url")
async def api_get_call_recording_url(call_id: str, user: str = Depends(require_auth)):
    """Return a playable recording URL (signed when bucket is private)."""
    _apply_supabase_env_safe()
    import db
    from call_logs_db import fetch_call_by_id

    try:
        row = await fetch_call_by_id(call_id)
        if not row and str(call_id).startswith("recovered-"):
            logs = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=True)
            row = next((l for l in logs if str(l.get("id")) == str(call_id)), None)
        if not row:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Call not found"})

        sb = await db.get_supabase()
        room_id = row.get("room_id") or str(call_id).replace("recovered-", "")
        url = _resolve_recording_url(
            sb,
            row.get("recording_url"),
            room_id,
            sign=True,
        )
        if not url:
            raise HTTPException(status_code=404, detail={"success": False, "error": "No recording for this call"})
        return _agent_ok({"url": url, "room_id": room_id})
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error resolving recording URL: %s", exc)
        raise HTTPException(status_code=500, detail={"success": False, "error": str(exc)}) from exc


async def _load_call_row_for_recording(call_id: str) -> dict:
    import db
    from call_logs_db import fetch_call_by_id

    row = await fetch_call_by_id(call_id)
    if not row and str(call_id).startswith("recovered-"):
        logs = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=True)
        row = next((l for l in logs if str(l.get("id")) == str(call_id)), None)
    return row or {}


async def _download_recording_bytes(sb, recording_url: str | None, room_id: str | None) -> bytes:
    object_path = _extract_recording_object_path(recording_url, room_id)
    if not object_path:
        raise HTTPException(status_code=404, detail="No recording path")

    bucket = _recordings_bucket()
    if sb:
        try:
            data = sb.storage.from_(bucket).download(object_path)
            if data:
                return data
        except Exception as exc:
            logger.warning("Storage download failed for %s/%s: %s", bucket, object_path, exc)

    signed_url = _resolve_recording_url(sb, recording_url, room_id, sign=True)
    if not signed_url:
        raise HTTPException(status_code=404, detail="Recording not found")

    import httpx

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        res = await client.get(signed_url)
        if res.status_code >= 400:
            raise HTTPException(status_code=404, detail="Recording not found")
        return res.content


@app.get("/api/calls/{call_id}/recording")
async def api_stream_call_recording(call_id: str, user: str = Depends(require_auth_media)):
    """Stream recording through the dashboard origin (fixes browser CORS on <audio>)."""
    _apply_supabase_env_safe()
    import db

    try:
        row = await _load_call_row_for_recording(call_id)
        if not row:
            raise HTTPException(status_code=404, detail="Call not found")

        sb = await db.get_supabase()
        room_id = row.get("room_id") or str(call_id).replace("recovered-", "")
        audio_bytes = await _download_recording_bytes(sb, row.get("recording_url"), room_id)
        return Response(
            content=audio_bytes,
            media_type="audio/ogg",
            headers={
                "Cache-Control": "private, max-age=3600",
                "Accept-Ranges": "bytes",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error streaming recording: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to stream recording") from exc


@app.post("/api/calls/{call_id}/summarise")
async def api_regenerate_summary(call_id: str, user: str = Depends(require_auth)):
    """Regenerate post-call summary for a completed call (unified analysis)."""
    from deepseek_summariser import generate_call_summary, format_summary_for_db
    from call_logs_db import fetch_call_by_id
    import db

    try:
        row = await fetch_call_by_id(call_id) if not str(call_id).startswith("recovered-") else None
        if not row:
            # Try transcript fallback
            logs = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=True)
            row = next((l for l in logs if str(l.get("id")) == str(call_id)), None)
        if not row:
            raise HTTPException(status_code=404, detail={"success": False, "error": "Call not found"})

        transcript = row.get("transcript") or ""
        if not transcript or transcript == "unavailable":
            return {"success": False, "error": "No transcript available for this call."}

        ds_summary = await generate_call_summary(transcript)
        call_summary, call_purpose = format_summary_for_db(ds_summary)

        # Save back to Supabase
        sb = await db.get_supabase()
        if sb:
            try:
                call_id_val = row.get("id")
                if call_id_val:
                    sb.table("call_logs").update({
                        "call_summary": call_summary,
                        "call_purpose": call_purpose,
                    }).eq("id", call_id_val).execute()
            except Exception as update_err:
                logger.warning(f"[SUMMARISE] Failed to update Supabase: {update_err}")

        return {"success": True, "summary": {**ds_summary, "call_summary": call_summary, "call_purpose": call_purpose}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SUMMARISE] Error: {e}")
        raise HTTPException(status_code=500, detail={"success": False, "error": str(e)})


@app.get("/api/logs")
async def api_get_logs(user: str = Depends(require_auth)):
    config = read_config()
    _apply_supabase_env_safe()
    import db
    try:
        logs = await db.fetch_call_logs_or_transcripts(limit=50, include_transcript=False)
        sb = await db.get_supabase()
        from call_logs_db import fetch_agent_name_map

        agent_names = await fetch_agent_name_map()
        cleaned = []
        for row in logs:
            enriched = _enrich_call_log_row(row, sb, agent_names, sign_recording=False)
            if enriched:
                cleaned.append(enriched)
        return cleaned
    except Exception as e:
        logger.error(f"Error fetching logs: {e}")
        return []

@app.get("/api/logs/{log_id}/transcript")
async def api_get_transcript(log_id: str):
    config = read_config()
    _apply_supabase_env_safe()
    import db
    try:
        if str(log_id).startswith("recovered-"):
            for item in (await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=True) or []):
                if str(item.get("id")) == str(log_id):
                    phone = _resolve_display_phone(item.get("phone_number"), item.get("caller_name"))
                    text = f"Call Log — {item.get('created_at', '')}\n"
                    text += f"Phone: {phone or '—'}\n"
                    text += f"Duration: {item.get('duration_seconds', 0)}s\n"
                    text += f"Summary: {item.get('summary', '')}\n\n"
                    text += "--- TRANSCRIPT ---\n"
                    text += item.get("transcript", "No transcript available.")
                    return PlainTextResponse(
                        content=text,
                        media_type="text/plain",
                        headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"},
                    )

        supabase = await db.get_supabase()
        if not supabase:
            return PlainTextResponse(content="Error: Supabase credentials are missing.", status_code=500)
        res = supabase.table("call_logs").select("*").eq("id", log_id).single().execute()
        data = res.data
        phone = _resolve_display_phone(data.get("phone_number"), data.get("caller_name"))
        text = f"Call Log — {data.get('created_at', '')}\n"
        text += f"Phone: {phone or '—'}\n"
        text += f"Duration: {data.get('duration_seconds', 0)}s\n"
        text += f"Summary: {data.get('summary', '')}\n\n"
        text += "--- TRANSCRIPT ---\n"
        text += data.get("transcript", "No transcript available.")
        return PlainTextResponse(content=text, media_type="text/plain",
                                 headers={"Content-Disposition": f"attachment; filename=transcript_{log_id}.txt"})
    except Exception as e:
        return PlainTextResponse(content=f"Error: {e}", status_code=500)

async def _fetch_all_bookings_payload() -> list:
    import db
    appointments = await _clean_bookings_payload(
        (await db.fetch_bookings()) + (await db.fetch_local_appointments())
    )
    site_visits = await _clean_bookings_payload(await db.fetch_site_visits())
    merged: list[dict] = []
    seen: set[str] = set()
    for row in appointments + site_visits:
        key = f"{row.get('phone_number')}:{row.get('appointment_time')}:{row.get('booking_type', 'appointment')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    merged.sort(key=lambda x: x.get("appointment_time") or "", reverse=True)
    return merged


@app.get("/api/bookings")
async def api_get_bookings():
    read_config()
    _apply_supabase_env_safe()
    try:
        return await _fetch_all_bookings_payload()
    except Exception as e:
        logger.error(f"Error fetching bookings: {e}")
        return []


@app.get("/api/site-visits")
async def api_get_site_visits(
    user: str = Depends(require_auth),
    status: Optional[str] = Query(default=None, description="Filter: booked, completed, cancelled, all"),
):
    read_config()
    _apply_supabase_env_safe()
    import db
    try:
        visits = await _clean_bookings_payload(await db.fetch_site_visits())
        filter_key = (status or "all").strip().lower()
        if filter_key == "booked":
            visits = [
                v for v in visits
                if str(v.get("status") or "confirmed").lower() in {"confirmed", "booked"}
            ]
        elif filter_key == "completed":
            visits = [v for v in visits if str(v.get("status") or "").lower() == "completed"]
        elif filter_key == "cancelled":
            visits = [v for v in visits if str(v.get("status") or "").lower() == "cancelled"]
        return visits
    except Exception as e:
        logger.error(f"Error fetching site visits: {e}")
        return []


class SiteVisitScheduleRequest(BaseModel):
    phone: str
    caller_name: Optional[str] = ""
    listing_id: str
    property_name: Optional[str] = ""
    location: Optional[str] = ""
    scheduled_at: str
    notes: Optional[str] = "Scheduled from CRM dashboard"


@app.get("/api/site-visits/properties")
async def api_site_visit_properties(user: str = Depends(require_auth)):
    from site_visit_booking import list_bookable_properties
    return {"properties": list_bookable_properties()}


@app.post("/api/site-visits")
async def api_schedule_site_visit(body: SiteVisitScheduleRequest, user: str = Depends(require_auth)):
    """Schedule a site visit from the CRM dashboard — saves + sends WhatsApp confirmation."""
    from site_visit_booking import confirm_site_visit, list_bookable_properties

    normalized_phone = normalize_phone_number(body.phone)
    if not is_valid_phone_number(normalized_phone):
        raise HTTPException(status_code=422, detail={"success": False, "error": "Invalid phone number"})

    listing_id = (body.listing_id or "").strip()
    if not listing_id:
        raise HTTPException(status_code=422, detail={"success": False, "error": "Property is required"})

    valid_keys = {p["key"] for p in list_bookable_properties()}
    if listing_id not in valid_keys:
        raise HTTPException(status_code=422, detail={"success": False, "error": f"Unknown property: {listing_id}"})

    if not (body.scheduled_at or "").strip():
        raise HTTPException(status_code=422, detail={"success": False, "error": "Visit date and time is required"})

    _apply_supabase_env_safe()
    result = await confirm_site_visit(
        caller_phone=normalized_phone,
        caller_name=(body.caller_name or "").strip(),
        listing_id=listing_id,
        requested_datetime=body.scheduled_at.strip(),
        property_name=(body.property_name or "").strip(),
        location=(body.location or "").strip(),
        notes=(body.notes or "Scheduled from CRM dashboard").strip(),
        room_id=None,
        scheduled_keys=None,
    )

    if not result.get("success"):
        status = 400
        if result.get("duplicate"):
            status = 409
        raise HTTPException(status_code=status, detail=result)

    record = result.get("record")
    if record:
        result["record"] = (await _clean_bookings_payload([record]))[0]

    logger.info(
        "[CRM] Site visit scheduled for %s — property=%s whatsapp=%s",
        normalized_phone,
        listing_id,
        result.get("whatsapp_sent"),
    )
    return result


class SiteVisitStatusUpdate(BaseModel):
    status: str


@app.patch("/api/site-visits/{visit_id}")
async def api_update_site_visit_status(
    visit_id: str,
    body: SiteVisitStatusUpdate,
    user: str = Depends(require_auth),
):
    """Mark a site visit as completed, cancelled, etc."""
    _apply_supabase_env_safe()
    import db

    result = await db.update_site_visit_status(visit_id, body.status)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result)
    record = result.get("record")
    if record:
        cleaned = await _clean_bookings_payload([record])
        result["record"] = cleaned[0] if cleaned else record
    return result


@app.get("/api/stats")
async def api_get_stats():
    config = read_config()
    _apply_supabase_env_safe()
    import db
    try:
        return await db.fetch_stats()
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {"total_calls": 0, "total_bookings": 0, "avg_duration": 0, "booking_rate": 0}

def _row_is_booked(row: dict) -> bool:
    summary_text = str(row.get("summary") or "").lower()
    return bool(row.get("was_booked")) or (
        ("confirm" in summary_text)
        or ("already_signed_up" in summary_text)
        or ("already signed up" in summary_text)
    )


def _site_visit_is_booked(status: str) -> bool:
    return str(status or "confirmed").lower() in {"confirmed", "booked"}


def _site_visit_is_completed(status: str) -> bool:
    return str(status or "").lower() == "completed"


def _enrich_contacts_with_site_visits(contacts: list, site_visits: list) -> list:
    """Attach site visit history and pipeline status to CRM contacts."""
    visits_by_phone: dict[str, list[dict]] = {}
    for visit in site_visits or []:
        phone = normalize_phone_number(visit.get("phone_number") or "")
        if not is_valid_phone_number(phone):
            continue
        status = str(visit.get("status") or visit.get("visit_status") or "confirmed").lower()
        visits_by_phone.setdefault(phone, []).append({
            "id": visit.get("id"),
            "property_name": visit.get("property_name") or visit.get("listing_id") or "Property",
            "appointment_time": visit.get("appointment_time"),
            "location": visit.get("location") or "",
            "status": status,
        })

    for visit_list in visits_by_phone.values():
        visit_list.sort(key=lambda x: str(x.get("appointment_time") or ""), reverse=True)

    existing_phones = {
        normalize_phone_number(c.get("phone_number") or "")
        for c in contacts
        if is_valid_phone_number(normalize_phone_number(c.get("phone_number") or ""))
    }

    for contact in contacts:
        phone = normalize_phone_number(contact.get("phone_number") or "")
        visits = visits_by_phone.get(phone, [])
        contact["site_visits"] = visits
        has_completed = any(_site_visit_is_completed(v.get("status")) for v in visits)
        has_booked = any(_site_visit_is_booked(v.get("status")) for v in visits)
        contact["is_completed"] = has_completed
        if has_completed:
            contact["crm_pipeline_status"] = "completed"
        elif has_booked or contact.get("is_booked"):
            contact["crm_pipeline_status"] = "booked"
            contact["is_booked"] = True
        else:
            contact["crm_pipeline_status"] = "pending"

    for phone, visits in visits_by_phone.items():
        if phone in existing_phones:
            continue
        has_completed = any(_site_visit_is_completed(v.get("status")) for v in visits)
        has_booked = any(_site_visit_is_booked(v.get("status")) for v in visits)
        sample = visits[0] if visits else {}
        matching_visit = next((v for v in site_visits if normalize_phone_number(v.get("phone_number") or "") == phone), {})
        contacts.append({
            "phone_number": phone,
            "caller_name": matching_visit.get("caller_name") or phone,
            "total_calls": 0,
            "last_seen": matching_visit.get("created_at") or sample.get("appointment_time"),
            "is_booked": has_booked,
            "is_completed": has_completed,
            "crm_pipeline_status": "completed" if has_completed else ("booked" if has_booked else "pending"),
            "latest_purpose": "site_visit",
            "latest_summary": matching_visit.get("summary") or "Site visit scheduled",
            "site_visits": visits,
        })

    contacts.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    return contacts


def _build_contacts_from_rows(rows: list) -> list:
    """CRM helper — groups call_logs by phone number, deduplicates into contacts."""
    known_phone_by_name: dict[str, str] = {}
    for r in rows:
        candidate_name_key = _canonical_name_key(r.get("caller_name"))
        if not candidate_name_key:
            continue
        candidate_phone = _resolve_display_phone_from_row(r)
        normalized_candidate_phone = normalize_phone_number(candidate_phone)
        if is_valid_phone_number(normalized_candidate_phone) and candidate_name_key not in known_phone_by_name:
            known_phone_by_name[candidate_name_key] = normalized_candidate_phone

    contacts: dict = {}
    for r in rows:
        raw_phone = r.get("phone_number")
        normalized_phone = normalize_phone_number(raw_phone)
        if is_demo_phone(normalized_phone):
            continue

        candidate_name = sanitize_caller_name(r.get("caller_name"))
        candidate_name_key = _canonical_name_key(r.get("caller_name"))
        display_phone = _resolve_display_phone_from_row(r)
        if not display_phone and candidate_name_key:
            display_phone = known_phone_by_name.get(candidate_name_key, "")

        normalized_display_phone = normalize_phone_number(display_phone)
        if is_valid_phone_number(normalized_display_phone):
            contact_key = normalized_display_phone
            display_phone = normalized_display_phone
        else:
            fallback_label = candidate_name or str(raw_phone or "").strip() or "unresolved"
            contact_key = f"unknown:{fallback_label.lower()}"

        if contact_key not in contacts:
            contacts[contact_key] = {
                "phone_number": display_phone,
                "caller_name": _display_caller_name(r.get("caller_name"), display_phone),
                "_name_key": candidate_name_key,
                "total_calls": 0,
                "last_seen": r.get("created_at"),
                "is_booked": False,
                "latest_purpose": r.get("call_purpose") or "",
                "latest_summary": r.get("call_summary") or "",
            }
        c = contacts[contact_key]
        c["total_calls"] += 1

        if not c.get("phone_number") and c.get("_name_key"):
            backfill_phone = known_phone_by_name.get(c["_name_key"], "")
            if backfill_phone:
                c["phone_number"] = backfill_phone

        if candidate_name and (
            not sanitize_caller_name(c.get("caller_name"))
            or c.get("caller_name") == c.get("phone_number")
        ):
            c["caller_name"] = candidate_name

        existing_last_seen = str(c.get("last_seen") or "")
        candidate_last_seen = str(r.get("created_at") or "")
        if candidate_last_seen and candidate_last_seen > existing_last_seen:
            c["last_seen"] = candidate_last_seen
        if _row_is_booked(r):
            c["is_booked"] = True

    contact_list = sorted(contacts.values(), key=lambda x: x["last_seen"] or "", reverse=True)
    for c in contact_list:
        c.pop("_name_key", None)
    return contact_list


def _build_analytics_from_rows(rows: list) -> dict:
    """Analytics helper for SPA charts and KPI cards."""
    logs = [r for r in rows if not is_demo_phone(r.get("phone_number"))]
    total_calls = len(logs)
    booked_calls = 0
    total_duration = 0
    connected_calls = 0
    by_day: dict[str, int] = {}
    by_hour: dict[int, int] = {}
    outcomes = {"booked": 0, "cancelled": 0, "completed": 0, "unknown": 0}

    for row in logs:
        summary = (row.get("summary") or "").lower()
        duration = int(row.get("duration_seconds") or 0)
        created_at = row.get("created_at") or ""

        if duration > 0:
            total_duration += duration
            connected_calls += 1

        if _row_is_booked(row):
            booked_calls += 1
            outcomes["booked"] += 1
        elif "cancel" in summary:
            outcomes["cancelled"] += 1
        elif summary:
            outcomes["completed"] += 1
        else:
            outcomes["unknown"] += 1

        if created_at:
            day_key = created_at[:10]
            by_day[day_key] = by_day.get(day_key, 0) + 1
            try:
                hour = int(created_at[11:13])
                by_hour[hour] = by_hour.get(hour, 0) + 1
            except Exception:
                pass

    avg_duration = round(total_duration / connected_calls) if connected_calls else 0
    booking_rate = round((booked_calls / total_calls) * 100, 1) if total_calls else 0.0
    connect_rate = round((connected_calls / total_calls) * 100, 1) if total_calls else 0.0

    today = now_utc().date()
    daily_series = [
        {
            "date": (today - timedelta(days=offset)).isoformat(),
            "calls": by_day.get((today - timedelta(days=offset)).isoformat(), 0),
        }
        for offset in range(13, -1, -1)
    ]
    hourly_series = [{"hour": h, "calls": by_hour.get(h, 0)} for h in range(24)]

    return {
        "kpis": {
            "total_calls": total_calls,
            "booked_calls": booked_calls,
            "booking_rate": booking_rate,
            "connect_rate": connect_rate,
            "avg_duration": avg_duration,
        },
        "outcomes": outcomes,
        "daily_series": daily_series,
        "hourly_series": hourly_series,
    }


def _empty_analytics_payload() -> dict:
    return {
        "kpis": {"total_calls": 0, "booked_calls": 0, "booking_rate": 0.0, "connect_rate": 0.0, "avg_duration": 0},
        "outcomes": {"booked": 0, "cancelled": 0, "completed": 0, "unknown": 0},
        "daily_series": [],
        "hourly_series": [],
    }


async def _clean_bookings_payload(bookings: list) -> list:
    cleaned = []
    for b in bookings:
        row = dict(b)
        phone = _resolve_display_phone_from_row(row)
        if is_demo_phone(phone):
            continue
        row["phone_number"] = phone
        row["caller_name"] = _display_caller_name(row.get("caller_name"), row.get("phone_number"))
        row["appointment_time"] = _normalize_appointment_time_ist(row.get("appointment_time"))
        cleaned.append(row)
    cleaned.sort(key=lambda x: x.get("appointment_time") or "", reverse=True)
    return cleaned


@app.get("/api/bootstrap")
async def api_bootstrap(user: str = Depends(require_auth)):
    """Single round-trip payload for dashboard initial load."""
    _apply_supabase_env_safe()
    import db
    from call_logs_db import fetch_agent_name_map

    try:
        rows = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=False) or []
        sb = await db.get_supabase()
        agent_names = await fetch_agent_name_map()
        bookings = await _fetch_all_bookings_payload()
        site_visits = await _clean_bookings_payload(await db.fetch_site_visits())
        contacts = _enrich_contacts_with_site_visits(_build_contacts_from_rows(rows), site_visits)

        cleaned_logs = []
        for row in rows:
            enriched = _enrich_call_log_row(row, sb, agent_names, sign_recording=False)
            if enriched:
                cleaned_logs.append(enriched)
            if len(cleaned_logs) >= 50:
                break

        return {
            "logs": cleaned_logs,
            "contacts": contacts,
            "bookings": bookings,
            "site_visits": site_visits,
            "analytics": _build_analytics_from_rows(rows),
        }
    except Exception as e:
        logger.error(f"Error fetching bootstrap data: {e}")
        return {
            "logs": [],
            "contacts": [],
            "bookings": [],
            "site_visits": [],
            "analytics": _empty_analytics_payload(),
        }


@app.get("/api/contacts")
async def api_get_contacts(user: str = Depends(require_auth)):
    """CRM endpoint — groups call_logs by phone number, deduplicates into contacts."""
    _apply_supabase_env_safe()
    try:
        import db
        rows = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=False) or []
        site_visits = await _clean_bookings_payload(await db.fetch_site_visits())
        return _enrich_contacts_with_site_visits(_build_contacts_from_rows(rows), site_visits)
    except Exception as e:
        logger.error(f"Error fetching contacts: {e}")
        return []


@app.get("/api/analytics")
async def api_get_analytics():
    """Analytics endpoint for SPA charts and KPI cards."""
    _apply_supabase_env_safe()
    import db

    try:
        rows = await db.fetch_call_logs_or_transcripts(limit=500, include_transcript=False) or []
        return _build_analytics_from_rows(rows)
    except Exception as e:
        logger.error(f"Error fetching analytics: {e}")
        return _empty_analytics_payload()


DEMO_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" type="image/png" href="/static/favicon.png">
  <link rel="apple-touch-icon" href="/static/favicon.png">
  <title>AI Voice Demo — RapidX AI</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f3f1ee;color:#1c1917;display:flex;align-items:center;justify-content:center;min-height:100vh;flex-direction:column;gap:24px;padding:24px}
    .card{background:#fff;border:1px solid #e3dfd8;border-radius:8px;padding:40px;max-width:440px;width:100%;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,0.06)}
    h1{font-size:22px;font-weight:700;margin-bottom:6px;letter-spacing:-0.01em}
    .sub{color:#8c8780;font-size:13px;margin-bottom:28px}
    .avatar{width:80px;height:80px;border-radius:50%;background:#0d9488;display:flex;align-items:center;justify-content:center;font-size:36px;margin:0 auto 24px}
    .btn{width:100%;padding:14px;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;border:none;transition:all 0.15s;font-family:inherit}
    .btn-start{background:#0d9488;color:#fff}
    .btn-start:hover{background:#0f766e}
    .btn-end{background:#b91c1c;color:#fff;display:none}
    .btn-end:hover{background:#991b1b}
    #status{font-size:13px;color:#8c8780;margin-top:16px;min-height:20px}
    .pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:#15803d;margin-right:6px;animation:pulse 1.5s infinite}
    @keyframes pulse{0%,100%{box-shadow:0 0 4px #15803d}50%{box-shadow:0 0 12px #15803d}}
    .vol-bar{display:flex;gap:3px;align-items:flex-end;justify-content:center;height:32px;margin-top:12px;display:none}
    .vol-bar span{width:4px;background:#0d9488;border-radius:2px;transition:height 0.1s}
  </style>
</head>
<body>
  <div class="card">
    <div class="avatar">🎙</div>
    <h1>Talk to Aryan</h1>
    <div class="sub">AI-powered multilingual consultant · RapidX AI</div>
    <button class="btn btn-start" id="startBtn" onclick="startCall()">📞 Start Demo Call</button>
    <button class="btn btn-end" id="endBtn" onclick="endCall()">📵 End Call</button>
    <div id="status">Click to start a live voice demo</div>
    <div class="vol-bar" id="volBar">
      <span id="b1" style="height:8px"></span><span id="b2" style="height:14px"></span>
      <span id="b3" style="height:20px"></span><span id="b4" style="height:14px"></span>
      <span id="b5" style="height:8px"></span>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js"></script>
  <script>
    let room;
    async function startCall() {
      document.getElementById('status').textContent = 'Connecting...';
      document.getElementById('startBtn').disabled = true;
      try {
        const res = await fetch('/api/demo-token').then(r => r.json());
        if (res.error) throw new Error(res.error);
        room = new LivekitClient.Room();
        await room.connect(res.url, res.token, {autoSubscribe: true});
        await room.localParticipant.setMicrophoneEnabled(true);
        document.getElementById('startBtn').style.display = 'none';
        document.getElementById('endBtn').style.display = 'block';
        document.getElementById('volBar').style.display = 'flex';
        setStatus('<span class="pulse"></span>Connected — speak now!');
        animateBars();
      } catch(e) {
        setStatus('❌ ' + e.message);
        document.getElementById('startBtn').disabled = false;
      }
    }
    async function endCall() {
      if (room) { await room.disconnect(); room = null; }
      document.getElementById('startBtn').style.display = 'block';
      document.getElementById('startBtn').disabled = false;
      document.getElementById('endBtn').style.display = 'none';
      document.getElementById('volBar').style.display = 'none';
      setStatus('Call ended. Click to start again.');
    }
    function setStatus(html) { document.getElementById('status').innerHTML = html; }
    function animateBars() {
      if (!room) return;
      ['b1','b2','b3','b4','b5'].forEach(id => {
        document.getElementById(id).style.height = (4 + Math.random()*24) + 'px';
      });
      setTimeout(animateBars, 150);
    }
  </script>
</body>
</html>"""


# ── Outbound Calls ────────────────────────────────────────────────────────────

def _livekit_agent_name() -> str:
    return os.environ.get("LIVEKIT_AGENT_NAME", "inbound-voice-demo")


def _build_dispatch_metadata(phone: str, agent_id: str | None = None, **extra) -> str:
    """Build LiveKit dispatch metadata with agent_id for dynamic DB loading."""
    import agents_db

    meta = {
        "phone_number": phone,
        "phone": phone,
        "agent_id": agent_id or os.environ.get("DEFAULT_AGENT_ID") or agents_db.DEFAULT_AGENT_ID,
    }
    meta.update(extra)
    return json.dumps(meta)


@app.post("/api/call/single")
async def api_call_single(request: Request, user: str = Depends(require_auth)):
    """Dispatch a single outbound call via LiveKit."""
    data = await request.json()
    phone = normalize_phone_number((data.get("phone") or "").strip())
    agent_id = (data.get("agent_id") or "").strip() or None
    if not is_valid_phone_number(phone):
        return {
            "status": "error",
            "message": "Invalid phone number. For India use +91XXXXXXXXXX, 91XXXXXXXXXX, or XXXXXXXXXX (10 digits).",
        }
    config = read_config()
    try:
        import random
        from livekit import api as lkapi
        from livekit_api_client import create_livekit_api

        lk = create_livekit_api()
        room_name = f"call-{phone.replace('+','')}-{random.randint(1000,9999)}"
        dispatch = await lk.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name=_livekit_agent_name(),
                room=room_name,
                metadata=_build_dispatch_metadata(phone, agent_id),
            )
        )
        await lk.aclose()
        logger.info(f"Outbound call dispatched to {phone}: {dispatch.id}")
        return {
            "status": "ok",
            "dispatch_id": dispatch.id,
            "room": room_name,
            "phone": phone,
            "agent_id": agent_id or os.environ.get("DEFAULT_AGENT_ID"),
        }
    except Exception as e:
        logger.error(f"Call dispatch error: {e}")
        return {"status": "error", "message": str(e)}

def _in_call_window(window_start: str, window_end: str, timezone_name: str) -> tuple[bool, str]:
    """Return whether current local time is inside HH:MM window."""
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(timezone_name or "Asia/Kolkata")
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")

    now = now_utc().astimezone(tz)
    try:
        start_h, start_m = [int(x) for x in str(window_start or "09:00").split(":", 1)]
        end_h, end_m = [int(x) for x in str(window_end or "20:00").split(":", 1)]
    except Exception:
        start_h, start_m, end_h, end_m = 9, 0, 20, 0

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    current_minutes = now.hour * 60 + now.minute

    if start_minutes <= end_minutes:
        inside = start_minutes <= current_minutes <= end_minutes
    else:
        inside = current_minutes >= start_minutes or current_minutes <= end_minutes

    label = now.strftime("%H:%M %Z")
    if inside:
        return True, label
    return False, f"Current time {label} is outside {window_start}–{window_end}"


@app.post("/api/call/bulk")
async def api_call_bulk(request: Request, user: str = Depends(require_auth)):
    """Dispatch outbound calls to multiple numbers (one per line)."""
    import random
    import uuid
    from livekit import api as lkapi

    data = await request.json()
    bulk_agent_id = (data.get("agent_id") or "").strip() or None
    campaign_name = (data.get("campaign_name") or "").strip()
    campaign_id = str(data.get("campaign_id") or uuid.uuid4())
    delay_seconds = max(0, min(int(data.get("delay_seconds") or 0), 120))
    max_concurrent = max(1, min(int(data.get("max_concurrent") or 1), 20))
    respect_window = bool(data.get("respect_window", True))
    window_start = (data.get("window_start") or "09:00").strip()
    window_end = (data.get("window_end") or "20:00").strip()
    timezone_name = (data.get("timezone") or "Asia/Kolkata").strip()

    raw_numbers = data.get("numbers") or ""
    if isinstance(raw_numbers, list):
        lines = [str(n) for n in raw_numbers]
    else:
        lines = str(raw_numbers).replace(",", "\n").replace(";", "\n").splitlines()
    numbers = [normalize_phone_number(n.strip()) for n in lines if n.strip()]

    if respect_window:
        inside, window_msg = _in_call_window(window_start, window_end, timezone_name)
        if not inside:
            return {
                "results": [],
                "total": 0,
                "blocked_by_window": True,
                "message": window_msg,
                "campaign_id": campaign_id,
            }

    from livekit_api_client import create_livekit_api

    results = []
    try:
        lk_shared = create_livekit_api()
    except ValueError as exc:
        return {
            "results": [],
            "total": 0,
            "ok": 0,
            "status": "error",
            "message": str(exc),
            "campaign_id": campaign_id,
        }

    dispatch_sem = asyncio.Semaphore(max_concurrent)

    async def _dispatch_phone(phone: str) -> dict:
        async with dispatch_sem:
            room_name = f"call-{phone.replace('+', '')}-{random.randint(1000, 9999)}"
            dispatch = await lk_shared.agent_dispatch.create_dispatch(
                lkapi.CreateAgentDispatchRequest(
                    agent_name=_livekit_agent_name(),
                    room=room_name,
                    metadata=_build_dispatch_metadata(
                        phone,
                        bulk_agent_id,
                        bulk_call_id=campaign_id,
                        campaign_name=campaign_name or None,
                        call_direction="outbound",
                    ),
                )
            )
            logger.info(f"Bulk outbound dispatched to {phone}: {dispatch.id}")
            return {
                "phone": phone,
                "status": "ok",
                "dispatch_id": dispatch.id,
                "agent_id": bulk_agent_id or os.environ.get("DEFAULT_AGENT_ID"),
                "campaign_id": campaign_id,
            }

    for idx, phone in enumerate(numbers):
        if idx > 0 and delay_seconds:
            await asyncio.sleep(delay_seconds)

        if not is_valid_phone_number(phone):
            results.append(
                {
                    "phone": phone,
                    "status": "error",
                    "message": "Invalid phone number. India format: +91XXXXXXXXXX / 91XXXXXXXXXX / XXXXXXXXXX.",
                }
            )
            continue
        try:
            results.append(await _dispatch_phone(phone))
        except Exception as e:
            results.append({"phone": phone, "status": "error", "message": str(e)})

    await lk_shared.aclose()
    ok = sum(1 for r in results if r.get("status") == "ok")
    try:
        import campaign_store

        campaign_record = campaign_store.create_campaign(
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            agent_id=bulk_agent_id,
            phones=numbers,
            dispatch_results=results,
            window={
                "start": window_start,
                "end": window_end,
                "timezone": timezone_name,
                "delay_seconds": delay_seconds,
                "max_concurrent": max_concurrent,
            },
        )
    except Exception as exc:
        logger.warning("Campaign persist failed: %s", exc)
        campaign_record = None

    return {
        "results": results,
        "total": len(results),
        "ok": ok,
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "delay_seconds": delay_seconds,
        "max_concurrent": max_concurrent,
        "window": {"start": window_start, "end": window_end, "timezone": timezone_name},
        "stats": (campaign_record or {}).get("stats"),
    }


@app.get("/api/campaigns")
async def api_campaigns_list(user: str = Depends(require_auth)):
    """List recent outbound campaigns with summary stats."""
    import campaign_store

    return {"success": True, "campaigns": campaign_store.list_campaigns()}


@app.get("/api/campaigns/{campaign_id}")
async def api_campaign_detail(campaign_id: str, user: str = Depends(require_auth)):
    """Return full campaign detail including per-contact outcomes."""
    import campaign_store

    data = campaign_store.get_campaign(campaign_id)
    if not data:
        return JSONResponse(status_code=404, content={"success": False, "message": "Campaign not found"})
    return {"success": True, "campaign": data}


@app.get("/api/campaigns/{campaign_id}/not-lifted/export/csv")
async def api_campaign_not_lifted_csv(campaign_id: str, user: str = Depends(require_auth)):
    """Download CSV of contacts that did not answer the outbound call."""
    import campaign_store
    from fastapi.responses import Response

    data = campaign_store.get_campaign(campaign_id)
    if not data:
        return JSONResponse(status_code=404, content={"success": False, "message": "Campaign not found"})
    csv_text = campaign_store.export_not_lifted_csv(campaign_id)
    slug = (data.get("campaign_name") or campaign_id)[:40].replace(" ", "_")
    filename = f"not_lifted_{slug}_{campaign_id[:8]}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ── Demo Link ─────────────────────────────────────────────────────────────────

@app.get("/api/demo-token")
async def api_demo_token():
    """Generate a LiveKit room + access token for browser-based demo call."""
    config = read_config()
    try:
        from datetime import timedelta

        from livekit.api import AccessToken, VideoGrants
        import random
        room_name = f"demo-{random.randint(10000,99999)}"
        api_key    = os.environ.get("LIVEKIT_API_KEY","")
        api_secret = os.environ.get("LIVEKIT_API_SECRET","")
        from livekit_api_client import create_livekit_api, resolve_livekit_api_url

        livekit_url = resolve_livekit_api_url()

        token = AccessToken(api_key, api_secret) \
            .with_identity("demo-user") \
            .with_name("Demo Caller") \
            .with_grants(VideoGrants(room_join=True, room=room_name)) \
            .with_ttl(timedelta(seconds=3600)) \
            .to_jwt()

        # Also dispatch the agent into the room
        from livekit import api as lkapi
        lk = create_livekit_api(url=livekit_url, api_key=api_key, api_secret=api_secret)
        await lk.agent_dispatch.create_dispatch(
            lkapi.CreateAgentDispatchRequest(
                agent_name=_livekit_agent_name(),
                room=room_name,
                metadata=_build_dispatch_metadata("demo", is_demo=True),
            )
        )
        await lk.aclose()
        return {"token": token, "room": room_name, "url": livekit_url}
    except Exception as e:
        logger.error(f"Demo token error: {e}")
        return {"error": str(e)}

@app.get("/demo", response_class=HTMLResponse)
async def get_demo_page():
    """Browser-based demo call page using LiveKit JS SDK."""
    return HTMLResponse(content=DEMO_PAGE_HTML)


# ── Prometheus Metrics (#40) ──────────────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram, Gauge, REGISTRY, generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response as _Resp

    def _get_or_create_metric(metric_cls, name, documentation, **kwargs):
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
        return metric_cls(name, documentation, **kwargs)

    _voice_calls_total = _get_or_create_metric(
        Counter,
        "voice_calls_total",
        "Total calls handled by the agent",
    )
    _voice_calls_booked = _get_or_create_metric(
        Counter,
        "voice_calls_booked_total",
        "Calls that resulted in a booking",
    )
    _voice_call_duration = _get_or_create_metric(
        Histogram,
        "voice_call_duration_seconds",
        "Call duration in seconds",
        buckets=[10, 30, 60, 120, 300, 600, 1200],
    )
    _voice_calls_active = _get_or_create_metric(
        Gauge,
        "voice_calls_active",
        "Currently active calls",
    )

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        """Prometheus metrics scrape endpoint."""
        return _Resp(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/internal/record-call", include_in_schema=False)
    async def record_call_metric(request: Request):
        """Called by agent.py at shutdown to update Prometheus counters."""
        data = await request.json()
        _voice_calls_total.inc()
        if data.get("booked"):
            _voice_calls_booked.inc()
        if data.get("duration"):
            _voice_call_duration.observe(data["duration"])
        return {"ok": True}

    logger.info("[METRICS] Prometheus metrics enabled at /metrics")

except ImportError:
    logger.warning("[METRICS] prometheus_client not installed — /metrics disabled")

# ── Main Dashboard HTML ────────────────────────────────────────────────────────


@app.get("/live-test", response_class=HTMLResponse, include_in_schema=False)
def live_test_monitor_page():
    """Local-only live call monitor for laptop testing (no Supabase)."""
    path = os.path.join(FRONTEND_DIR, "live-monitor.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="live-monitor.html not found")
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/health")
def health_check():
    """Health check endpoint for Coolify monitoring (#22)."""
    env_path = os.path.join(BASE_DIR, ".env")
    required_env = (
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )
    missing_env = [key for key in required_env if not os.environ.get(key, "").strip()]
    recording_env = (
        "SUPABASE_S3_ACCESS_KEY",
        "SUPABASE_S3_SECRET_KEY",
        "SUPABASE_S3_ENDPOINT",
    )
    missing_recording_env = [key for key in recording_env if not os.environ.get(key, "").strip()]
    frontend_assets = {
        "index_html": os.path.isfile(os.path.join(FRONTEND_DIR, "index.html")),
        "app_js": os.path.isfile(os.path.join(FRONTEND_DIR, "app.js")),
        "lucide_js": os.path.isfile(_lucide_vendor),
        "styles_css": os.path.isfile(os.path.join(FRONTEND_DIR, "styles.css")),
    }
    setup_ok = not missing_env and all(frontend_assets.values())
    provider_keys = {
        "gemini": bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
        "soniox": bool(os.environ.get("SONIOX_API_KEY")),
        "sarvam": bool(os.environ.get("SARVAM_API_KEY")),
        "cartesia": bool(os.environ.get("CARTESIA_API_KEY")),
        "livekit": bool(os.environ.get("LIVEKIT_URL")),
    }
    knowledge_ok = os.path.isfile(os.path.join(BASE_DIR, "data", "company_knowledge", "tinglish.json"))
    from agent_config_loader import get_canary_health_info

    canary = get_canary_health_info()
    conversion_health: dict = {}
    try:
        from conversion_metrics import get_conversion_health

        conversion_health = get_conversion_health()
    except Exception:
        conversion_health = {"error": "unavailable"}
    return {
        "status": "ok" if setup_ok else "degraded",
        "timestamp": now_utc().isoformat(),
        "service": "inbound-voice-app",
        "providers": provider_keys,
        "knowledge_packs": knowledge_ok,
        "canary": canary,
        "conversion": conversion_health,
        "setup": {
            "env_file_found": os.path.isfile(env_path),
            "env_file_path": env_path,
            "missing_env": missing_env,
            "missing_recording_env": missing_recording_env,
            "recordings_bucket": _recordings_bucket(),
            "frontend_dir": FRONTEND_DIR,
            "frontend_assets": frontend_assets,
            "static_mounted": os.path.isdir(FRONTEND_DIR),
        },
    }

def _serve_spa_html(filename: str) -> HTMLResponse | None:
    spa_path = os.path.join(FRONTEND_DIR, filename)
    if os.path.exists(spa_path):
        with open(spa_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return None


@app.get("/", response_class=HTMLResponse)
async def get_dashboard(request: Request):
    if is_auth_enabled() and not _has_valid_session(request):
        return RedirectResponse(url="/login", status_code=302)

    served = _serve_spa_html("index.html")
    if served is not None:
        return served

    config = read_config()

    def sel(key, val):
        return "selected" if config.get(key) == val else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {FAVICON_LINKS}  <title>AI Voice Agent — Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #f3f1ee;
      --sidebar: #fbfaf8;
      --card: #ffffff;
      --border: #e3dfd8;
      --border-strong: #d1ccc3;
      --accent: #0d9488;
      --accent-subtle: #ccfbf1;
      --accent-hover: #0f766e;
      --text: #1c1917;
      --text-secondary: #57534e;
      --muted: #8c8780;
      --green: #15803d;
      --green-subtle: #dcfce7;
      --red: #b91c1c;
      --yellow: #b45309;
      --yellow-subtle: #fef3c7;
      --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      --font-mono: 'SF Mono', 'Fira Code', 'Fira Mono', 'Roboto Mono', monospace;
      --sidebar-w: 200px;
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: var(--font); background: var(--bg); color: var(--text); display: flex; height: 100vh; overflow: hidden; font-size: 14px; -webkit-font-smoothing: antialiased; }}

    #sidebar {{
      width: var(--sidebar-w); min-width: var(--sidebar-w);
      background: var(--sidebar); border-right: 1px solid var(--border);
      display: flex; flex-direction: column; padding: 16px 0;
    }}
    .sidebar-brand {{
      padding: 0 14px 14px; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 10px; margin-bottom: 8px;
    }}
    .sidebar-brand .logo {{
      width: 32px; height: 32px; background: var(--accent); color: #fff;
      border-radius: 6px; display: flex; align-items: center; justify-content: center;
      font-size: 14px; font-weight: 700; flex-shrink: 0;
    }}
    .sidebar-brand .brand-text {{ font-weight: 650; font-size: 13px; line-height: 1.2; }}
    .sidebar-brand .brand-sub {{ font-size: 9px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
    .sidebar-nav {{ padding: 8px 0; flex: 1; overflow-y: auto; }}
    .nav-section {{ padding: 8px 14px 4px; font-size: 9px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; }}
    .nav-item {{
      display: flex; align-items: center; gap: 8px;
      padding: 7px 14px; cursor: pointer; font-size: 12.5px; font-weight: 500;
      color: var(--text-secondary); border: none; background: none; font-family: var(--font);
      transition: background 0.12s, color 0.12s; user-select: none;
    }}
    .nav-item:hover {{ color: var(--text); background: var(--bg); }}
    .nav-item.active {{ color: var(--accent); background: var(--accent-subtle); font-weight: 600; }}
    .nav-item .icon {{ font-size: 14px; width: 18px; text-align: center; }}
    .sidebar-footer {{
      padding: 12px 14px; border-top: 1px solid var(--border);
      font-size: 11px; color: var(--muted); margin-top: auto;
    }}
    .status-dot {{
      display: inline-block; width: 7px; height: 7px; border-radius: 50%;
      background: var(--green); margin-right: 6px;
    }}

    #main {{ flex: 1; overflow-y: auto; background: var(--bg); }}
    .page {{ display: none; padding: 16px; min-height: 100%; }}
    .page.active {{ display: block; }}
    .page-header {{ margin-bottom: 20px; }}
    .page-title {{ font-size: 18px; font-weight: 700; letter-spacing: -0.01em; }}
    .page-sub {{ font-size: 12px; color: var(--muted); margin-top: 3px; }}

    .card {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 6px; padding: 16px;
    }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; margin-bottom: 16px; }}
    .stat-card {{ background: var(--card); border: 1px solid var(--border); padding: 12px 14px; }}
    .stat-card:first-child {{ border-radius: 6px 0 0 6px; }}
    .stat-card:last-child {{ border-radius: 0 6px 6px 0; }}
    .stat-label {{ font-size: 10px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; }}
    .stat-value {{ font-size: 22px; font-weight: 700; margin-top: 4px; letter-spacing: -0.02em; }}
    .stat-sub {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}

    .table-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12.5px; }}
    thead th {{ padding: 10px 14px; text-align: left; font-size: 10px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }}
    tbody td {{ padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
    tbody tr:last-child td {{ border-bottom: none; }}
    tbody tr:hover {{ background: var(--bg); }}
    .badge {{ display: inline-flex; align-items: center; gap: 3px; padding: 2px 8px; border-radius: 20px; font-size: 10px; font-weight: 600; border: 1px solid var(--border); }}
    .badge-green {{ color: var(--green); border-color: var(--green); background: var(--green-subtle); }}
    .badge-blue {{ color: #60a5fa; border-color: #60a5fa; background: rgba(96,165,250,0.12); }}
    .badge-gray {{ background: var(--bg); color: var(--muted); }}
    .badge-yellow {{ color: var(--yellow); border-color: var(--yellow); background: var(--yellow-subtle); }}

    label {{ display: block; font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 5px; }}
    input[type=text], input[type=password], input[type=number], select, textarea {{
      width: 100%; background: var(--bg); border: 1px solid var(--border);
      border-radius: 6px; padding: 8px 10px; color: var(--text); font-family: var(--font);
      font-size: 13px; outline: none; transition: border-color 0.15s;
    }}
    input:focus, select:focus, textarea:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-subtle); }}
    textarea {{ font-family: var(--font-mono); resize: vertical; }}
    .form-group {{ margin-bottom: 16px; }}
    .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .hint {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
    .section-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 20px; margin-bottom: 16px; }}
    .section-title {{ font-size: 13px; font-weight: 650; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid var(--border); }}

    .btn {{ display: inline-flex; align-items: center; gap: 5px; padding: 8px 14px; border-radius: 6px; font-size: 12.5px; font-weight: 600; cursor: pointer; border: none; font-family: var(--font); transition: all 0.15s; }}
    .btn-primary {{ background: var(--accent); color: #fff; }}
    .btn-primary:hover {{ background: var(--accent-hover); }}
    .btn-ghost {{ background: transparent; border: 1px solid var(--border); color: var(--text-secondary); }}
    .btn-ghost:hover {{ border-color: var(--accent); color: var(--accent); background: var(--accent-subtle); }}
    .btn-sm {{ padding: 4px 10px; font-size: 11px; }}
    .save-bar {{
      position: sticky; bottom: 0; left: 0; right: 0;
      background: var(--card); border-top: 1px solid var(--border);
      padding: 12px 16px; display: flex; align-items: center; justify-content: space-between; z-index: 20;
    }}
    .save-status {{ font-size: 12px; font-weight: 500; color: var(--green); opacity: 0; transition: opacity 0.3s; }}

    .cal-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }}
    .cal-grid {{ display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }}
    .cal-day-name {{ text-align: center; font-size: 10px; color: var(--muted); font-weight: 600; padding: 6px 0; text-transform: uppercase; letter-spacing: 0.06em; }}
    .cal-cell {{
      min-height: 70px; background: var(--card); border: 1px solid var(--border);
      border-radius: 6px; padding: 8px; cursor: pointer; transition: border-color 0.15s, background 0.15s;
    }}
    .cal-cell:hover {{ border-color: var(--accent); background: var(--accent-subtle); }}
    .cal-cell.today {{ border-color: var(--accent); background: var(--accent-subtle); }}
    .cal-cell.other-month {{ opacity: 0.25; }}
    .cal-num {{ font-size: 12px; font-weight: 700; }}
    .cal-dot {{ width: 5px; height: 5px; border-radius: 50%; background: var(--accent); margin-top: 5px; }}
    .cal-booking-count {{ font-size: 9px; color: var(--accent); font-weight: 600; margin-top: 2px; }}
    .day-panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 16px; margin-top: 16px; display: none; }}
    .day-panel.show {{ display: block; animation: fadeIn 0.2s ease; }}
    .booking-item {{ padding: 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; transition: border-color 0.15s; }}
    .booking-item:hover {{ border-color: var(--accent); }}
    .booking-item:last-child {{ margin-bottom: 0; }}

    .modal-overlay {{
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,0.55); z-index: 1000;
      align-items: center; justify-content: center;
    }}
    .modal-overlay.open {{ display: flex; animation: fadeIn 0.2s ease; }}
    .modal-box {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 8px; padding: 24px; min-width: 420px; max-width: 560px; width: 90%;
      box-shadow: 0 16px 48px rgba(0,0,0,0.2);
      animation: slideUp 0.2s ease;
    }}
    .modal-title {{ font-size: 16px; font-weight: 700; margin-bottom: 4px; }}
    .modal-sub {{ font-size: 11px; color: var(--muted); margin-bottom: 16px; }}
    .modal-close {{
      position: absolute; top: 16px; right: 20px;
      background: none; border: none; color: var(--muted);
      font-size: 18px; cursor: pointer; line-height: 1;
    }}
    .modal-close:hover {{ color: var(--text); }}
    @keyframes fadeIn {{ from {{ opacity:0 }} to {{ opacity:1 }} }}
    @keyframes slideUp {{ from {{ transform:translateY(16px); opacity:0 }} to {{ transform:translateY(0); opacity:1 }} }}

    .stat-card {{ transition: border-color 0.15s; }}
    .stat-card:hover {{ border-color: var(--border-strong); }}
    .stat-accent {{ color: var(--accent); }}
    .pulse {{ animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0%,100% {{ box-shadow: 0 0 4px var(--green); }} 50% {{ box-shadow: 0 0 10px var(--green); }} }}

    @media (max-width: 900px) {{
      body {{ flex-direction: column; }}
      #sidebar {{ width: 100%; min-width: auto; flex-direction: row; padding: 0; border-right: none; border-top: 1px solid var(--border); position: fixed; bottom: 0; z-index: 50; }}
      .sidebar-brand, .sidebar-footer, .nav-section {{ display: none; }}
      .sidebar-nav {{ display: flex; flex-direction: row; overflow-x: auto; padding: 4px; }}
      .nav-item {{ flex-shrink: 0; }}
      #main {{ padding-bottom: 52px; }}
      .stat-grid {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>

<!-- ── Day Detail Modal ── -->
<div class="modal-overlay" id="day-modal" onclick="if(event.target===this)closeDayModal()">
  <div class="modal-box" style="position:relative;">
    <button class="modal-close" onclick="closeDayModal()">✕</button>
    <div class="modal-title" id="modal-date-title">Bookings</div>
    <div class="modal-sub" id="modal-date-sub"></div>
    <div id="modal-bookings-body"></div>
  </div>
</div>

<!-- ── Sidebar ── -->
<nav id="sidebar">
  <div class="sidebar-brand">
    <div class="logo">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" fill="rgba(255,255,255,0.12)"/>
        <path d="M8 12c0-2.21 1.79-4 4-4s4 1.79 4 4" stroke="white" stroke-width="1.8" stroke-linecap="round"/>
        <circle cx="12" cy="15" r="2" fill="white"/>
        <path d="M6 18c1.5-1.5 3.5-2.5 6-2.5s4.5 1 6 2.5" stroke="white" stroke-width="1.4" stroke-linecap="round" opacity="0.6"/>
      </svg>
    </div>
    <div>
      <div class="brand-text">Voice Agent</div>
      <div class="brand-sub">RapidX AI</div>
    </div>
  </div>
  <div class="sidebar-nav">
    <div class="nav-section">Overview</div>
    <div class="nav-item active" onclick="goTo('dashboard', this)"><span class="icon">📊</span> Dashboard</div>
    <div class="nav-item" onclick="goTo('calendar', this); loadCalendar();"><span class="icon">📅</span> Calendar</div>
    <div class="nav-section" style="margin-top:12px;">Configuration</div>
    <div class="nav-item" onclick="goTo('agent', this)"><span class="icon">🤖</span> Agent Settings</div>
    <div class="nav-item" onclick="goTo('models', this)"><span class="icon">🎙️</span> Models & Voice</div>
    <div class="nav-item" onclick="goTo('credentials', this)"><span class="icon">🔑</span> API Credentials</div>
    <div class="nav-section" style="margin-top:12px;">Data</div>
    <div class="nav-item" onclick="goTo('logs', this); loadLogs();"><span class="icon">📞</span> Call Logs</div>
    <div class="nav-item" onclick="goTo('crm', this); loadCRM();"><span class="icon">👥</span> CRM Contacts</div>
    <div class="nav-section" style="margin-top:12px;">Calling</div>
    <div class="nav-item" onclick="goTo('outbound', this)"><span class="icon">📲</span> Outbound Calls</div>
    <div class="nav-item" onclick="goTo('languages', this); initLanguagePage();"><span class="icon">🌐</span> Language Presets</div>
    <div class="nav-item" onclick="goTo('demo', this); initDemo();"><span class="icon">✨</span> Demo Link</div>
  </div>
  <div class="sidebar-footer">
    <span class="status-dot pulse"></span>Agent Online
  </div>
</nav>

<!-- ── Main Content ── -->
<div id="main">

  <!-- ── Dashboard ── -->
  <div id="page-dashboard" class="page active">
    <div class="page-header">
      <div class="page-title">Dashboard</div>
      <div class="page-sub">Real-time overview of your AI voice agent performance</div>
    </div>
    <div class="stat-grid" id="stat-grid">
      <div class="stat-card"><div class="stat-label">Total Calls</div><div class="stat-value" id="stat-calls">—</div><div class="stat-sub">All time</div></div>
      <div class="stat-card"><div class="stat-label">Bookings & Visits</div><div class="stat-value" id="stat-bookings">—</div><div class="stat-sub">Appointments + site visits</div></div>
      <div class="stat-card"><div class="stat-label">Site Visits</div><div class="stat-value" id="stat-site-visits">—</div><div class="stat-sub">Property visits booked</div></div>
      <div class="stat-card"><div class="stat-label">Avg Duration</div><div class="stat-value" id="stat-duration">—</div><div class="stat-sub">Seconds per call</div></div>
      <div class="stat-card"><div class="stat-label">Booking Rate</div><div class="stat-value" id="stat-rate">—</div><div class="stat-sub">Calls that converted</div></div>
    </div>
    <div class="section-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div class="section-title" style="border:none;padding:0;margin:0;">Recent Calls</div>
        <button class="btn btn-ghost btn-sm" onclick="loadDashboard()">↻ Refresh</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Date</th><th>Phone</th><th>Duration</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody id="dash-table-body"><tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Calendar ── -->
  <div id="page-calendar" class="page">
    <div class="page-header">
      <div class="page-title">Booking Calendar</div>
      <div class="page-sub">Appointments and site visits by date</div>
    </div>
    <div class="section-card">
      <div class="cal-header">
        <button class="btn btn-ghost btn-sm" onclick="changeMonth(-1)">← Prev</button>
        <div style="font-size:16px;font-weight:700;" id="cal-month-label">Month Year</div>
        <button class="btn btn-ghost btn-sm" onclick="changeMonth(1)">Next →</button>
      </div>
      <div class="cal-grid" id="cal-grid"></div>
      <div class="day-panel" id="day-panel">
        <div style="font-size:14px;font-weight:700;margin-bottom:12px;" id="day-panel-title">Selected Day</div>
        <div id="day-panel-body"></div>
      </div>
    </div>
  </div>

  <!-- ── Agent Settings ── -->
  <div id="page-agent" class="page">
    <div class="page-header">
      <div class="page-title">Agent Settings</div>
      <div class="page-sub">Configure AI personality, opening line, and sensitivity</div>
    </div>
    <div class="section-card">
      <div class="section-title">Opening Greeting</div>
      <div class="form-group">
        <label>First Line (What the agent says when a call connects)</label>
        <input type="text" id="first_line" value="{html_escape(str(config.get('first_line', '')))}" placeholder="Namaste! This is Aryan from RapidX AI...">
        <div class="hint">This is the very first thing the agent says. Keep it concise and warm.</div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">System Prompt</div>
      <div class="form-group">
        <label>Master System Prompt</label>
        <textarea id="agent_instructions" rows="16" placeholder="Enter the AI's full personality and instructions...">{config.get('agent_instructions', '')}</textarea>
        <div class="hint">Date and time context are injected automatically. Do not hardcode today's date.</div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Listening Sensitivity</div>
      <div class="form-group" style="max-width:220px;">
        <label>Endpointing Delay (seconds)</label>
        <input type="number" id="stt_min_endpointing_delay" step="0.01" min="0.20" max="1.20" value="{html_escape(str(config.get('stt_min_endpointing_delay', 0.35)))}">
        <div class="hint">Seconds the AI waits after silence before responding. Recommended: 0.30 to 0.60</div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-agent">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('agent')">💾 Save Agent Settings</button>
    </div>
  </div>

  <!-- ── Models & Voice ── -->
  <div id="page-models" class="page">
    <div class="page-header">
      <div class="page-title">Models & Voice</div>
      <div class="page-sub">Select the LLM brain and TTS voice persona</div>
    </div>
    <div class="section-card">
      <div class="section-title">Language Model (LLM)</div>
      <div class="form-group" style="max-width:360px;">
        <label>OpenAI Model</label>
        <select id="llm_model">
          <option value="gpt-4o-mini" {sel('llm_model','gpt-4o-mini')}>gpt-4o-mini — Fast &amp; Cheap (Default)</option>
          <option value="gpt-4o" {sel('llm_model','gpt-4o')}>gpt-4o — Balanced</option>
          <option value="gpt-4.1" {sel('llm_model','gpt-4.1')}>gpt-4.1 — Latest (Recommended)</option>
          <option value="gpt-4.1-mini" {sel('llm_model','gpt-4.1-mini')}>gpt-4.1-mini — Fast &amp; Latest</option>
          <option value="gpt-4.5-preview" {sel('llm_model','gpt-4.5-preview')}>gpt-4.5-preview — Most Capable</option>
          <option value="o4-mini" {sel('llm_model','o4-mini')}>o4-mini — Reasoning, Fast</option>
          <option value="o3" {sel('llm_model','o3')}>o3 — Reasoning, Best</option>
          <option value="gpt-4-turbo" {sel('llm_model','gpt-4-turbo')}>gpt-4-turbo — Legacy</option>
          <option value="gpt-3.5-turbo" {sel('llm_model','gpt-3.5-turbo')}>gpt-3.5-turbo — Cheapest</option>
        </select>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Voice Synthesis (Sarvam bulbul:v3)</div>
      <div class="form-row" style="max-width:720px;">
        <div class="form-group">
          <label>Speaker Voice</label>
          <select id="tts_voice">
            <option value="kavya" {sel('tts_voice','kavya')}>Kavya — Female, Friendly</option>
            <option value="rohan" {sel('tts_voice','rohan')}>Rohan — Male, Balanced</option>
            <option value="priya" {sel('tts_voice','priya')}>Priya — Female, Warm</option>
            <option value="shubh" {sel('tts_voice','shubh')}>Shubh — Male, Formal</option>
            <option value="shreya" {sel('tts_voice','shreya')}>Shreya — Female, Clear</option>
            <option value="ritu" {sel('tts_voice','ritu')}>Ritu — Female, Soft</option>
            <option value="rahul" {sel('tts_voice','rahul')}>Rahul — Male, Deep</option>
            <option value="amit" {sel('tts_voice','amit')}>Amit — Male, Casual</option>
            <option value="neha" {sel('tts_voice','neha')}>Neha — Female, Energetic</option>
            <option value="dev" {sel('tts_voice','dev')}>Dev — Male, Professional</option>
          </select>
        </div>
        <div class="form-group">
          <label>Language</label>
          <select id="tts_language">
            <option value="hi-IN" {sel('tts_language','hi-IN')}>Hindi (hi-IN)</option>
            <option value="en-IN" {sel('tts_language','en-IN')}>English India (en-IN)</option>
            <option value="ta-IN" {sel('tts_language','ta-IN')}>Tamil (ta-IN)</option>
            <option value="te-IN" {sel('tts_language','te-IN')}>Telugu (te-IN)</option>
            <option value="kn-IN" {sel('tts_language','kn-IN')}>Kannada (kn-IN)</option>
            <option value="ml-IN" {sel('tts_language','ml-IN')}>Malayalam (ml-IN)</option>
            <option value="mr-IN" {sel('tts_language','mr-IN')}>Marathi (mr-IN)</option>
            <option value="gu-IN" {sel('tts_language','gu-IN')}>Gujarati (gu-IN)</option>
            <option value="bn-IN" {sel('tts_language','bn-IN')}>Bengali (bn-IN)</option>
          </select>
        </div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-models">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('models')">💾 Save Model Settings</button>
    </div>
  </div>

  <!-- ── API Credentials ── -->
  <!-- CRM Contacts Page -->
  <div id="page-crm" class="page">
    <div class="page-header">
      <div class="page-title">👥 CRM Contacts</div>
      <div class="page-sub">Every caller recorded automatically — name, phone, call history</div>
    </div>
    <div class="section-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <div class="section-title" style="margin:0;">All Contacts</div>
        <button class="btn btn-ghost btn-sm" onclick="loadCRM()">&#x21bb; Refresh</button>
      </div>
      <div style="overflow-x:auto;">
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead>
            <tr style="border-bottom:1px solid var(--border);">
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Name</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Phone</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Total Calls</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Last Seen</th>
              <th style="padding:10px 12px;text-align:left;color:var(--muted);font-weight:500;">Status</th>
            </tr>
          </thead>
          <tbody id="crm-tbody">
            <tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading contacts...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- ── Language Presets Page ── -->
  <div id="page-languages" class="page">
    <div class="page-header">
      <div class="page-title">🌐 Language Presets</div>
      <div class="page-sub">One-click language configuration — saves immediately and takes effect on the next call</div>
    </div>
    <div class="section-card">
      <div class="section-title">Select a Language Mode</div>
      <div id="lang-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px;"></div>
    </div>
    <div class="section-card" style="margin-top:0;">
      <div class="section-title">About Multilingual Mode</div>
      <p style="font-size:13px;color:var(--muted);line-height:1.7;">
        In <strong style="color:var(--text);">Multilingual (Auto)</strong> mode the agent listens to the caller's first message and 
        automatically replies in the same language for the rest of the call. 
        Ideal for showcasing the agent across different audiences.<br><br>
        Language changes take effect on the <strong style="color:var(--accent);">next incoming call</strong>. 
        The TTS voice and language code are updated automatically.
      </p>
    </div>
  </div>

  <!-- ── Outbound Calls Page ── -->
  <div id="page-outbound" class="page">
    <div class="page-header">
      <div class="page-title">📲 Outbound Calls</div>
      <div class="page-sub">Dispatch the AI agent to call any number instantly</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">
      <div class="section-card">
        <div class="section-title">Single Call</div>
        <div class="form-group">
          <label>Phone Number (with country code)</label>
          <input type="text" id="call-single-num" placeholder="+91XXXXXXXXXX" style="font-family:monospace;">
          <div class="hint">Must start with + and country code e.g. +91</div>
        </div>
        <button class="btn btn-primary" onclick="makeSingleCall()" style="width:100%;">📞 Call Now</button>
        <div id="single-call-status" style="margin-top:12px;font-size:13px;"></div>
      </div>
      <div class="section-card">
        <div class="section-title">Bulk Call</div>
        <div class="form-group">
          <label>Phone Numbers (one per line)</label>
          <textarea id="call-bulk-nums" rows="6" placeholder="+91XXXXXXXXXX&#10;+91YYYYYYYYYY&#10;+44ZZZZZZZZZ"></textarea>
          <div class="hint">Each line is a separate call dispatched simultaneously</div>
        </div>
        <button class="btn btn-primary" onclick="makeBulkCall()" style="width:100%;">🚀 Call All Numbers</button>
        <div id="bulk-call-status" style="margin-top:12px;font-size:13px;"></div>
      </div>
    </div>
    <div class="section-card" id="call-results-card" style="display:none;">
      <div class="section-title">Call Results</div>
      <div id="call-results-body"></div>
    </div>
  </div>

  <!-- ── Demo Link Page ── -->
  <div id="page-demo" class="page">
    <div class="page-header">
      <div class="page-title">✨ Demo Link</div>
      <div class="page-sub">Generate a shareable browser link to let anyone test the AI agent live</div>
    </div>
    <div class="section-card" style="max-width:640px;">
      <div class="section-title">Browser Demo Call</div>
      <p style="font-size:13px;color:var(--muted);margin-bottom:20px;line-height:1.7;">
        Click <strong style="color:var(--text);">Generate Demo Link</strong> to create a unique session. 
        Share the link with anyone — they can talk to the AI agent directly from their browser, no app needed.
        Each session is valid for <strong style="color:var(--accent);">60 minutes</strong>.
      </p>
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
        <button class="btn btn-primary" onclick="generateDemo()">✨ Generate Demo Link</button>
        <button class="btn btn-ghost" id="copy-demo-btn" onclick="copyDemoLink()" style="display:none;">📋 Copy Link</button>
        <a id="open-demo-btn" href="#" target="_blank" class="btn btn-ghost" style="display:none;">↗ Open Demo</a>
      </div>
      <div id="demo-link-box" style="margin-top:16px;padding:12px 16px;background:var(--bg);border:1px solid var(--border);border-radius:8px;font-family:monospace;font-size:13px;color:var(--accent);display:none;word-break:break-all;"></div>
      <div id="demo-status" style="margin-top:10px;font-size:13px;color:var(--muted);"></div>
    </div>
    <div class="section-card" style="max-width:640px;margin-top:0;">
      <div class="section-title">Embedded Preview</div>
      <iframe id="demo-iframe" src="" style="width:100%;height:520px;border:none;border-radius:12px;background:#0f1117;display:none;"></iframe>
      <div style="font-size:12px;color:var(--muted);margin-top:8px;">The demo runs inside your dashboard. Use the generated link to share with others.</div>
    </div>
  </div>

    <div id="page-credentials" class="page">
    <div class="page-header">
      <div class="page-title">API Credentials</div>
      <div class="page-sub">Credentials here override .env values at runtime. Never share this page.</div>
    </div>
    <div class="section-card">
      <div class="section-title">LiveKit</div>
      <div class="form-row">
        <div class="form-group"><label>LiveKit URL</label><input type="text" id="livekit_url" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>SIP Trunk ID</label><input type="text" id="sip_trunk_id" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>API Key</label><input type="password" id="livekit_api_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>API Secret</label><input type="password" id="livekit_api_secret" placeholder="Set via .env" readonly></div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">AI Providers</div>
      <div class="form-row">
        <div class="form-group"><label>Gemini API Key</label><input type="password" id="gemini_api_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>OpenAI API Key</label><input type="password" id="openai_api_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Sarvam API Key</label><input type="password" id="sarvam_api_key" placeholder="Set via .env" readonly></div>
      </div>
    </div>
    <div class="section-card">
      <div class="section-title">Integrations</div>
        <div class="form-row">
        <div class="form-group"><label>Cal.com API Key</label><input type="password" id="cal_api_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Cal.com Event Type ID</label><input type="text" id="cal_event_type_id" value="{html_escape(str(config.get('cal_event_type_id', '')))}"></div>
        <div class="form-group"><label>Supabase URL</label><input type="text" id="supabase_url" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase Anon Key</label><input type="password" id="supabase_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase Service Role Key (Recommended)</label><input type="password" id="supabase_service_role_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase S3 Access Key</label><input type="password" id="supabase_s3_access_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase S3 Secret Key</label><input type="password" id="supabase_s3_secret_key" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase S3 Endpoint</label><input type="text" id="supabase_s3_endpoint" placeholder="Set via .env" readonly></div>
        <div class="form-group"><label>Supabase S3 Region</label><input type="text" id="supabase_s3_region" value="{html_escape(str(config.get('supabase_s3_region', 'ap-south-1')))}"></div>
      </div>
    </div>
    <div class="save-bar">
      <span class="save-status" id="save-status-credentials">✅ Saved!</span>
      <button class="btn btn-primary" onclick="saveConfig('credentials')">💾 Save Credentials</button>
    </div>
  </div>

  <!-- ── Call Logs ── -->
  <div id="page-logs" class="page">
    <div class="page-header">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div>
          <div class="page-title">Call Logs</div>
          <div class="page-sub">Full history of all incoming calls and transcripts</div>
        </div>
        <button class="btn btn-ghost" onclick="loadLogs()">↻ Refresh</button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date & Time</th>
            <th>Phone</th>
            <th>Duration</th>
            <th>Status</th>
            <th>Summary</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="logs-table-body"><tr><td colspan="6" style="text-align:center;padding:32px;color:var(--muted);">Click Refresh to load call logs</td></tr></tbody>
      </table>
    </div>
  </div>

</div><!-- /main -->

<script>
// ── Navigation ──────────────────────────────────────────────────────────────
function goTo(pageId, el) {{
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + pageId).classList.add('active');
  el.classList.add('active');
}}

function displayPhone(phone) {{
  const value = String(phone || '').trim();
  const lowered = value.toLowerCase();
  if (['unknown', 'unknown caller', 'caller', 'demo'].includes(lowered)) return '—';
  return value || '—';
}}

function cleanCallerName(name) {{
  const value = String(name || '').trim();
  if (!value) return '';
  const lowered = value.toLowerCase();
  if (['unknown', 'unknown caller', 'caller', 'sai'].includes(lowered)) return '';
  return value;
}}

function displayCallerName(name, phone) {{
  const cleaned = cleanCallerName(name);
  const resolvedPhone = displayPhone(phone);
  if (cleaned) return cleaned;
  if (resolvedPhone !== '—') return resolvedPhone;
  return 'Unknown Caller';
}}

// ── Stats & Dashboard ───────────────────────────────────────────────────────
async function loadDashboard() {{
  try {{
    const [stats, logs, bookings] = await Promise.all([
      fetch('/api/stats').then(r => r.json()),
      fetch('/api/logs').then(r => r.json()),
      fetch('/api/bookings').then(r => r.json()).catch(() => [])
    ]);
    document.getElementById('stat-calls').textContent = stats.total_calls ?? '—';
    document.getElementById('stat-bookings').textContent = (bookings && bookings.length) ? bookings.length : (stats.total_bookings ?? '—');
    const siteVisitCount = (bookings || []).filter(b => b.booking_type === 'site_visit').length;
    const siteVisitEl = document.getElementById('stat-site-visits');
    if (siteVisitEl) siteVisitEl.textContent = siteVisitCount || '0';
    document.getElementById('stat-duration').textContent = stats.avg_duration ? stats.avg_duration + 's' : '—';
    document.getElementById('stat-rate').textContent = stats.booking_rate ? stats.booking_rate + '%' : '—';

    const tbody = document.getElementById('dash-table-body');
    if (!logs || logs.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">No calls yet. Make a test call!</td></tr>';
      return;
    }}
    logs.slice(0, 10).forEach(log => {{
      const tr = document.createElement('tr');
      const td1 = document.createElement('td'); td1.style.color = 'var(--muted)'; td1.textContent = new Date(log.created_at).toLocaleString(); tr.appendChild(td1);
      const td2 = document.createElement('td'); td2.style.fontWeight = '600'; td2.textContent = displayPhone(log.phone_number); tr.appendChild(td2);
      const td3 = document.createElement('td'); td3.textContent = (log.duration_seconds || 0) + 's'; tr.appendChild(td3);
      const td4 = document.createElement('td'); td4.appendChild(badgeFor(log.summary)); tr.appendChild(td4);
      const td5 = document.createElement('td');
      if (log.id) {{
        td5.innerHTML = '<a style="color:var(--accent);font-size:12px;text-decoration:none;" href="/api/logs/' + log.id + '/transcript" download="transcript_' + log.id + '.txt">⬇ Download</a>';
      }}
      tr.appendChild(td5);
      tbody.appendChild(tr);
    }});
  }} catch(e) {{
    document.getElementById('dash-table-body').innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted);">Could not load data — check Supabase credentials.</td></tr>';
  }}
}}

function badgeFor(summary) {{
  const span = document.createElement('span');
  if (!summary) {{ span.className = 'badge badge-gray'; span.textContent = 'Ended'; }}
  else if (summary.toLowerCase().includes('confirm')) {{ span.className = 'badge badge-green'; span.textContent = '✓ Booked'; }}
  else if (summary.toLowerCase().includes('cancel')) {{ span.className = 'badge badge-yellow'; span.textContent = '✗ Cancelled'; }}
  else {{ span.className = 'badge badge-gray'; span.textContent = 'Completed'; }}
  return span;
}}

// ── Call Logs ───────────────────────────────────────────────────────────────
async function loadLogs() {{
  const tbody = document.getElementById('logs-table-body');
  tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">Loading...</td></tr>';
  try {{
    const logs = await fetch('/api/logs').then(r => r.json());
    if (!logs || logs.length === 0) {{
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted);">No call logs found.</td></tr>';
      return;
    }}
    logs.forEach(log => {{
      const tr = document.createElement('tr');
      const td1 = document.createElement('td'); td1.style.color = 'var(--muted)'; td1.style.whiteSpace = 'nowrap'; td1.textContent = new Date(log.created_at).toLocaleString(); tr.appendChild(td1);
      const td2 = document.createElement('td'); td2.style.fontWeight = '600'; td2.textContent = displayPhone(log.phone_number); tr.appendChild(td2);
      const td3 = document.createElement('td'); td3.textContent = (log.duration_seconds || 0) + 's'; tr.appendChild(td3);
      const td4 = document.createElement('td'); td4.appendChild(badgeFor(log.summary)); tr.appendChild(td4);
      const td5 = document.createElement('td'); td5.style.color = 'var(--muted)'; td5.style.fontSize = '12px'; td5.style.maxWidth = '200px'; td5.style.overflow = 'hidden'; td5.style.textOverflow = 'ellipsis'; td5.style.whiteSpace = 'nowrap'; td5.title = log.summary || ''; td5.textContent = log.summary || '\u2014'; tr.appendChild(td5);
      const td6 = document.createElement('td');
      if (log.id) {{
        td6.innerHTML = '<a class="btn btn-ghost btn-sm" style="text-decoration:none;" href="/api/logs/' + log.id + '/transcript" download="transcript_' + log.id + '.txt">⬇ Transcript</a>';
      }} else {{
        td6.textContent = '\u2014';
      }}
      if (log.recording_url) {{
        td6.innerHTML += '<a class="btn btn-ghost btn-sm" style="text-decoration:none;margin-left:4px;" href="' + log.recording_url + '" target="_blank">🎧 Recording</a>';
      }}
      tr.appendChild(td6);
      tbody.appendChild(tr);
    }});
  }} catch(e) {{
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">Error loading logs. Check Supabase credentials.</td></tr>';
  }}
}}

// ── Calendar ────────────────────────────────────────────────────────────────
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let allBookings = [];

async function loadCalendar() {{
  try {{ allBookings = await fetch('/api/bookings').then(r => r.json()); }} catch(e) {{ allBookings = []; }}
  renderCalendar();
}}

function changeMonth(dir) {{ calMonth += dir; if (calMonth > 11) {{ calMonth = 0; calYear++; }} else if (calMonth < 0) {{ calMonth = 11; calYear--; }} renderCalendar(); }}

function renderCalendar() {{
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('cal-month-label').textContent = `${{months[calMonth]}} ${{calYear}}`;
  const grid = document.getElementById('cal-grid');
  const days = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  const today = new Date();

  // Build booking map by appointment date string YYYY-MM-DD
  const bookMap = {{}};
  allBookings.forEach(b => {{
    const d = b.appointment_time ? String(b.appointment_time).slice(0,10) : null;
    if (d) {{ bookMap[d] = bookMap[d] || []; bookMap[d].push(b); }}
  }});

  let html = days.map(d => `<div class="cal-day-name">${{d}}</div>`).join('');

  const first = new Date(calYear, calMonth, 1);
  const last = new Date(calYear, calMonth + 1, 0);
  const startPad = first.getDay();

  // Prev month padding
  for (let i = 0; i < startPad; i++) {{
    const d = new Date(calYear, calMonth, -startPad + i + 1);
    html += `<div class="cal-cell other-month"><div class="cal-num">${{d.getDate()}}</div></div>`;
  }}

  for (let day = 1; day <= last.getDate(); day++) {{
    const dateStr = `${{calYear}}-${{String(calMonth+1).padStart(2,'0')}}-${{String(day).padStart(2,'0')}}`;
    const bks = bookMap[dateStr] || [];
    const isToday = today.getFullYear()===calYear && today.getMonth()===calMonth && today.getDate()===day;
    html += `<div class="cal-cell${{isToday?' today':''}}" onclick="showDay('${{dateStr}}', ${{JSON.stringify(bks).replace(/'/g,"&apos;")}})">
      <div class="cal-num">${{day}}</div>
      ${{bks.length ? `<div class="cal-dot"></div><div class="cal-booking-count">${{bks.length}} booking${{bks.length>1?'s':''}}</div>` : ''}}
    </div>`;
  }}

  // Next month padding
  const endPad = 6 - last.getDay();
  for (let i = 1; i <= endPad; i++) {{
    html += `<div class="cal-cell other-month"><div class="cal-num">${{i}}</div></div>`;
  }}

  grid.innerHTML = html;
  document.getElementById('day-panel').classList.remove('show');
}}

function showDay(dateStr, bookings) {{
  // Update old inline panel too
  const panel = document.getElementById('day-panel');
  if (panel) {{
    panel.classList.add('show');
    document.getElementById('day-panel-title').textContent = `Bookings on ${{dateStr}}`;
  }}
  // Open modal overlay
  openDayModal(dateStr, bookings);
}}

function openDayModal(dateStr, bookings) {{
  const modal = document.getElementById('day-modal');
  const dateObj = new Date(dateStr + 'T00:00:00');
  const formatted = dateObj.toLocaleDateString('en-IN', {{weekday:'long', year:'numeric', month:'long', day:'numeric'}});
  document.getElementById('modal-date-title').textContent = formatted;
  document.getElementById('modal-date-sub').textContent =
    bookings.length ? `${{bookings.length}} booking${{bookings.length>1?'s':''}} on this day` : 'No bookings on this day';

  if (!bookings || bookings.length === 0) {{
    document.getElementById('modal-bookings-body').innerHTML =
      '<div style="text-align:center;padding:32px;color:var(--muted);font-size:14px;">📅 No bookings on this day.</div>';
  }} else {{
    const body = document.getElementById('modal-bookings-body');
    body.innerHTML = '';
    bookings.forEach(b => {{
      const item = document.createElement('div');
      item.className = 'booking-item';
      const row = document.createElement('div');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.justifyContent = 'space-between';
      const phoneDiv = document.createElement('div');
      phoneDiv.style.fontWeight = '700';
      phoneDiv.style.fontSize = '14px';
      phoneDiv.textContent = '📞 ' + displayPhone(b.phone_number);
      row.appendChild(phoneDiv);
      const badge = document.createElement('span');
      const isSiteVisit = b.booking_type === 'site_visit';
      badge.className = isSiteVisit ? 'badge badge-blue' : 'badge badge-green';
      badge.textContent = isSiteVisit ? '🏠 Site Visit' : '✅ Booked';
      row.appendChild(badge);
      item.appendChild(row);
      const nameDiv = document.createElement('div');
      nameDiv.style.fontSize = '12px';
      nameDiv.style.color = 'var(--text)';
      nameDiv.style.marginTop = '6px';
      nameDiv.textContent = '👤 ' + (b.caller_name || 'Unknown');
      item.appendChild(nameDiv);
      const timeDiv = document.createElement('div');
      timeDiv.style.fontSize = '12px';
      timeDiv.style.color = 'var(--muted)';
      timeDiv.style.marginTop = '6px';
      timeDiv.textContent = '🕐 ' + (b.appointment_time ? new Date(b.appointment_time).toLocaleString('en-IN', {{day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit'}}) : 'Time unavailable');
      item.appendChild(timeDiv);
      if (isSiteVisit && (b.property_name || b.location)) {{
        const propDiv = document.createElement('div');
        propDiv.style.fontSize = '12px';
        propDiv.style.color = 'var(--muted)';
        propDiv.style.marginTop = '4px';
        propDiv.textContent = '🏠 ' + (b.property_name || '') + (b.location ? ' · ' + b.location : '');
        item.appendChild(propDiv);
      }}
      if (isSiteVisit && b.maps_url) {{
        const mapDiv = document.createElement('div');
        mapDiv.style.fontSize = '12px';
        mapDiv.style.marginTop = '6px';
        mapDiv.innerHTML = '🗺️ <a href="' + b.maps_url + '" target="_blank" style="color:var(--accent);">Open property location</a>';
        item.appendChild(mapDiv);
      }}
      if (isSiteVisit && b.brochure_sent) {{
        const brochureDiv = document.createElement('div');
        brochureDiv.style.fontSize = '12px';
        brochureDiv.style.color = 'var(--muted)';
        brochureDiv.style.marginTop = '4px';
        brochureDiv.textContent = '📄 Brochure sent on WhatsApp';
        item.appendChild(brochureDiv);
      }}
      if (b.summary) {{
        const summaryDiv = document.createElement('div');
        summaryDiv.style.fontSize = '12px';
        summaryDiv.style.color = 'var(--text)';
        summaryDiv.style.marginTop = '6px';
        summaryDiv.style.padding = '8px';
        summaryDiv.style.background = 'rgba(255,255,255,0.04)';
        summaryDiv.style.borderRadius = '6px';
        summaryDiv.textContent = '💬 ' + b.summary;
        item.appendChild(summaryDiv);
      }}
      body.appendChild(item);
    }});
  }}
  modal.classList.add('open');
}}

function closeDayModal() {{
  document.getElementById('day-modal').classList.remove('open');
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeDayModal(); }});

// ── CRM ─────────────────────────────────────────────────────────────────────
async function loadCRM() {{
  const tbody = document.getElementById('crm-tbody');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--muted);">Loading contacts...</td></tr>';
  try {{
    const contacts = await fetch('/api/contacts').then(r => r.json());
    if (!contacts.length) {{
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:40px;color:var(--muted);">No contacts yet. They will appear here automatically after calls.</td></tr>';
      return;
    }}
    tbody.innerHTML = '';
    contacts.forEach(c => {{
      const tr = document.createElement('tr');
      tr.style.borderBottom = '1px solid var(--border)';
      tr.style.transition = 'background 0.12s';
      tr.onmouseover = function() {{ this.style.background = 'rgba(255,255,255,0.025)'; }};
      tr.onmouseout  = function() {{ this.style.background = ''; }};
      const td1 = document.createElement('td');
      td1.style.padding = '14px 16px';
      td1.style.fontWeight = '600';
      td1.textContent = displayCallerName(c.caller_name, c.phone_number);
      tr.appendChild(td1);
      const td2 = document.createElement('td');
      td2.style.padding = '14px 16px';
      td2.style.fontFamily = 'monospace';
      td2.style.fontSize = '13px';
      td2.textContent = displayPhone(c.phone_number);
      tr.appendChild(td2);
      const td3 = document.createElement('td');
      td3.style.padding = '14px 16px';
      td3.style.textAlign = 'center';
      const countSpan = document.createElement('span');
      countSpan.style.background = 'rgba(108,99,255,0.12)';
      countSpan.style.color = 'var(--accent)';
      countSpan.style.padding = '3px 10px';
      countSpan.style.borderRadius = '20px';
      countSpan.style.fontSize = '12px';
      countSpan.style.fontWeight = '700';
      countSpan.textContent = c.total_calls;
      td3.appendChild(countSpan);
      tr.appendChild(td3);
      const td4 = document.createElement('td');
      td4.style.padding = '14px 16px';
      td4.style.color = 'var(--muted)';
      td4.style.fontSize = '12px';
      td4.textContent = c.last_seen ? new Date(c.last_seen).toLocaleString('en-IN') : '\\u2014';
      tr.appendChild(td4);
      const td5 = document.createElement('td');
      td5.style.padding = '14px 16px';
      const statusSpan = document.createElement('span');
      statusSpan.className = 'badge ' + (c.is_booked ? 'badge-green' : 'badge-gray');
      statusSpan.textContent = c.is_booked ? '✅ Booked' : '📵 No booking';
      td5.appendChild(statusSpan);
      tr.appendChild(td5);
      tbody.appendChild(tr);
    }});
  }} catch(e) {{
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:#ef4444;">Error loading contacts. Check Supabase credentials.</td></tr>';
  }}
}}

// ── Save Config ─────────────────────────────────────────────────────────────
async function saveConfig(section) {{
  const get = id => {{ const el = document.getElementById(id); return el ? el.value : null; }};

  const payload = {{}};

  if (section === 'agent') {{
    Object.assign(payload, {{
      first_line: get('first_line'),
      agent_instructions: get('agent_instructions'),
      stt_min_endpointing_delay: parseFloat(get('stt_min_endpointing_delay')),
    }});
  }} else if (section === 'models') {{
    Object.assign(payload, {{
      llm_model: get('llm_model'),
      tts_voice: get('tts_voice'),
      tts_language: get('tts_language'),
    }});
  }} else if (section === 'credentials') {{
    Object.assign(payload, {{
      cal_event_type_id: get('cal_event_type_id'),
      supabase_s3_region: get('supabase_s3_region'),
    }});
  }}

  const res = await fetch('/api/config', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload)
  }});

  const statusEl = document.getElementById('save-status-' + section);
  if (res.ok) {{
    statusEl.style.opacity = '1';
    setTimeout(() => {{ statusEl.style.opacity = '0'; }}, 2500);
  }} else {{
    alert('Failed to save. Check server logs.');
  }}
}}


// ── Language Presets ─────────────────────────────────────────────────────────
const LANG_PRESETS = {{
  hinglish:    {{ flag:'🇮🇳', label:'Hinglish',                sub:'Hindi + English mix',        color:'#6c63ff' }},
  hindi:       {{ flag:'🇮🇳', label:'Hindi',                   sub:'Pure Hindi',                  color:'#a855f7' }},
  english:     {{ flag:'🇬🇧', label:'English (India)',          sub:'Indian English',              color:'#3b82f6' }},
  tamil:       {{ flag:'🇮🇳', label:'Tamil',                   sub:'தமிழ்',                       color:'#f59e0b' }},
  telugu:      {{ flag:'🇮🇳', label:'Telugu',                  sub:'తెలుగు',                      color:'#10b981' }},
  gujarati:    {{ flag:'🇮🇳', label:'Gujarati',                sub:'ગુજરાતી',                     color:'#ef4444' }},
  bengali:     {{ flag:'🇮🇳', label:'Bengali',                 sub:'বাংলা',                       color:'#f97316' }},
  marathi:     {{ flag:'🇮🇳', label:'Marathi',                 sub:'मराठी',                       color:'#14b8a6' }},
  kannada:     {{ flag:'🇮🇳', label:'Kannada',                 sub:'ಕನ್ನಡ',                       color:'#8b5cf6' }},
  malayalam:   {{ flag:'🇮🇳', label:'Malayalam',               sub:'മലയാളം',                      color:'#ec4899' }},
  multilingual:{{ flag:'🌍', label:'Multilingual (Auto)',       sub:"Detects caller's language",   color:'#22c55e' }},
}};

let currentLangPreset = 'hinglish';

async function initLanguagePage() {{
  try {{
    const cfg = await fetch('/api/config').then(r=>r.json());
    currentLangPreset = cfg.lang_preset || 'hinglish';
  }} catch(e) {{}}
  renderLangGrid();
}}

function renderLangGrid() {{
  const grid = document.getElementById('lang-grid');
  if (!grid) return;
  grid.innerHTML = Object.entries(LANG_PRESETS).map(([id, p]) => `
    <div onclick="selectLangPreset('${{id}}')" style="
      background:${{id===currentLangPreset ? 'rgba(108,99,255,0.15)' : 'var(--bg)'}};
      border:2px solid ${{id===currentLangPreset ? p.color : 'var(--border)'}};
      border-radius:12px;padding:18px;cursor:pointer;transition:all 0.15s;
      ${{id===currentLangPreset ? 'box-shadow:0 0 16px rgba(108,99,255,0.2)' : ''}}
    " onmouseover="this.style.borderColor='${{p.color}}'" onmouseout="this.style.borderColor='${{id===currentLangPreset?p.color:'var(--border)}}'">
      <div style="font-size:28px;margin-bottom:8px;">${{p.flag}}</div>
      <div style="font-weight:700;font-size:14px;color:${{id===currentLangPreset?p.color:'var(--text)'}}">${{p.label}}</div>
      <div style="font-size:11px;color:var(--muted);margin-top:3px;">${{p.sub}}</div>
      ${{id===currentLangPreset ? '<div style="font-size:10px;color:#22c55e;margin-top:6px;font-weight:600;">✓ ACTIVE</div>' : ''}}
    </div>`).join('');
}}

async function selectLangPreset(id) {{
  const p = LANG_PRESETS[id];
  if (!p) return;
  currentLangPreset = id;
  renderLangGrid();
  // Save lang_preset, tts_language, tts_voice to config
  try {{
    const cfg = await fetch('/api/config').then(r=>r.json());
    const voices = {{ hinglish:'kavya', hindi:'ritu', english:'dev', tamil:'priya', telugu:'kavya', gujarati:'rohan', bengali:'neha', marathi:'shubh', kannada:'rahul', malayalam:'ritu', multilingual:'kavya' }};
    const langs  = {{ hinglish:'hi-IN', hindi:'hi-IN', english:'en-IN', tamil:'ta-IN', telugu:'te-IN', gujarati:'gu-IN', bengali:'bn-IN', marathi:'mr-IN', kannada:'kn-IN', malayalam:'ml-IN', multilingual:'hi-IN' }};
    await fetch('/api/config', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{ lang_preset: id, tts_language: langs[id], tts_voice: voices[id] }})
    }});
    const toast = document.createElement('div');
    toast.style.cssText='position:fixed;bottom:24px;right:24px;background:#22c55e;color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:9999;animation:slideUp 0.3s ease';
    toast.textContent = `✅ ${{p.label}} preset activated!`;
    document.body.appendChild(toast);
    setTimeout(()=>toast.remove(), 2500);
  }} catch(e) {{ alert('Failed to save: ' + e); }}
}}

// ── Outbound Calls ─────────────────────────────────────────────────────────── 
async function makeSingleCall() {{
  const phone = document.getElementById('call-single-num').value.trim();
  if (!phone) return;
  const el = document.getElementById('single-call-status');
  el.textContent = '⏳ Dispatching...';
  el.style.color = 'var(--muted)';
  try {{
    const res = await fetch('/api/call/single', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{phone}})
    }}).then(r=>r.json());
    if (res.status === 'ok') {{
      el.innerHTML = `✅ Call dispatched! Dispatch ID: <code>${{res.dispatch_id}}</code>`;
      el.style.color = 'var(--green)';
    }} else {{
      el.textContent = '❌ ' + res.message;
      el.style.color = 'var(--red)';
    }}
  }} catch(e) {{
    el.textContent = '❌ Error: ' + e;
    el.style.color = 'var(--red)';
  }}
}}

async function makeBulkCall() {{
  const nums = document.getElementById('call-bulk-nums').value.trim();
  if (!nums) return;
  const el = document.getElementById('bulk-call-status');
  el.textContent = '⏳ Dispatching all numbers...';
  try {{
    const res = await fetch('/api/call/bulk', {{
      method:'POST', headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{numbers: nums}})
    }}).then(r=>r.json());
    const results = res.results || [];
    document.getElementById('call-results-card').style.display = 'block';
    document.getElementById('call-results-body').innerHTML = results.map(r => `
      <div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">
        <span style="font-family:monospace;">${{r.phone}}</span>
        <span class="badge ${{r.status==='ok'?'badge-green':'badge-gray'}}">${{r.status==='ok'?'✅ Sent':'❌ '+r.message}}</span>
      </div>`).join('');
    el.textContent = `✅ ${{results.filter(r=>r.status==='ok').length}}/${{results.length}} calls dispatched`;
    el.style.color = 'var(--green)';
  }} catch(e) {{
    el.textContent = '❌ Error: ' + e;
    el.style.color = 'var(--red)';
  }}
}}

// ── Demo Link ─────────────────────────────────────────────────────────────────
let demoUrl = '';
function initDemo() {{
  // no-op until user clicks generate
}}
async function generateDemo() {{
  const statusEl = document.getElementById('demo-status');
  statusEl.textContent = '⏳ Generating session...';
  try {{
    const origin = window.location.origin;
    demoUrl = origin + '/demo';
    document.getElementById('demo-link-box').textContent = demoUrl;
    document.getElementById('demo-link-box').style.display = 'block';
    document.getElementById('copy-demo-btn').style.display = 'inline-flex';
    document.getElementById('open-demo-btn').style.display = 'inline-flex';
    document.getElementById('open-demo-btn').href = demoUrl;
    document.getElementById('demo-iframe').src = demoUrl;
    document.getElementById('demo-iframe').style.display = 'block';
    statusEl.textContent = 'Session ready — share the link or use the preview below';
  }} catch(e) {{
    statusEl.textContent = '❌ ' + e;
  }}
}}
function copyDemoLink() {{
  navigator.clipboard.writeText(demoUrl);
  document.getElementById('copy-demo-btn').textContent = '✅ Copied!';
  setTimeout(()=>document.getElementById('copy-demo-btn').textContent='📋 Copy Link', 2000);
}}

// ── Boot ────────────────────────────────────────────────────────────────────
loadDashboard();
</script>
</body>
</html>"""

    return HTMLResponse(content=html)


@app.get("/v2", response_class=HTMLResponse)
async def get_dashboard_v2(request: Request):
    if is_auth_enabled() and not _has_valid_session(request):
        return RedirectResponse(url="/login", status_code=302)

    served = _serve_spa_html("index-v2.html")
    if served is not None:
        return served

    return RedirectResponse(url="/", status_code=302)


@app.get("/insurance", response_class=HTMLResponse)
async def get_insurance_landing():
    """Scroll-scrub insurance / BFSI marketing page."""
    path = os.path.join(FRONTEND_DIR, "insurance", "index.html")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Insurance landing page not found")


@app.get("/fintech", response_class=HTMLResponse)
async def get_fintech_landing():
    """Scroll-scrub banking / lending / fintech marketing page."""
    path = os.path.join(FRONTEND_DIR, "fintech", "index.html")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Fintech landing page not found")


def _serve_vk_realty_html(filename: str) -> HTMLResponse:
    path = os.path.join(FRONTEND_DIR, "vk-realty-hme", filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="VK Realty page not found")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    base_meta = f'  <meta name="client-base" content="{CLIENT_BASE_PATH}">\n'
    if 'name="client-base"' not in content:
        content = content.replace("<head>\n", f"<head>\n{base_meta}", 1)
    content = content.replace("/vk-realty-hme-hme", CLIENT_BASE_PATH)
    return HTMLResponse(content=content)


def _client_login_redirect() -> RedirectResponse:
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}#login", status_code=302)


@app.get(CLIENT_BASE_PATH, response_class=HTMLResponse)
async def get_vk_realty_landing():
    """VK Realty white-label landing — scroll-scrub frames + login."""
    return _serve_vk_realty_html("index.html")


@app.get(f"{CLIENT_BASE_PATH}/login", response_class=HTMLResponse)
async def get_vk_realty_login():
    """Alias — login lives on the main VK Realty page after the frame animation."""
    return _client_login_redirect()


@app.get(f"{CLIENT_BASE_PATH}/dashboard", response_class=HTMLResponse)
async def get_vk_realty_dashboard(request: Request):
    """VK Realty branded dashboard (requires session when auth enabled)."""
    if is_auth_enabled() and not _has_valid_session(request):
        return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=302)
    return _serve_vk_realty_html("dashboard.html")


@app.get(f"{CLIENT_BASE_PATH}/o/{{role_slug}}", response_class=HTMLResponse)
async def get_vk_workspace_root(role_slug: str):
    """Role workspace — redirect to scoped dashboard."""
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}/o/{role_slug}/dashboard", status_code=302)


@app.get(f"{CLIENT_BASE_PATH}/o/{{role_slug}}/dashboard", response_class=HTMLResponse)
async def get_vk_workspace_dashboard(role_slug: str, request: Request):
    """Per-role VK Realty dashboard (org resolved by slug at login)."""
    if is_auth_enabled() and not _has_valid_session(request):
        return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=302)
    from org_store import get_organization_by_slug

    org = get_organization_by_slug(role_slug)
    if not org:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return _serve_vk_realty_html("dashboard.html")


@app.get(f"{CLIENT_BASE_PATH}/whatsapp/chat/{{number_id}}", response_class=HTMLResponse)
async def get_whatsapp_chat_page(number_id: str, request: Request):
    """Deep-linkable WhatsApp chat history (same dashboard shell)."""
    if is_auth_enabled() and not _has_valid_session(request):
        return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=302)
    return _serve_vk_realty_html("dashboard.html")


@app.get("/whatsapp/chat/{number_id}", response_class=HTMLResponse)
async def get_whatsapp_chat_short(number_id: str, request: Request):
    """Alias — /whatsapp/chat/{number_id} → dashboard chat view."""
    return await get_whatsapp_chat_page(number_id, request)


@app.get(LEGACY_BASE_PATH, response_class=HTMLResponse, include_in_schema=False)
async def get_legacy_vk_realty_landing():
    if CLIENT_BASE_PATH == LEGACY_BASE_PATH:
        return _serve_vk_realty_html("index.html")
    return RedirectResponse(url=CLIENT_BASE_PATH, status_code=301)


@app.get(f"{LEGACY_BASE_PATH}/login", response_class=HTMLResponse, include_in_schema=False)
async def get_legacy_vk_realty_login():
    if CLIENT_BASE_PATH == LEGACY_BASE_PATH:
        return _client_login_redirect()
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}/login", status_code=301)


@app.get(f"{LEGACY_BASE_PATH}/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def get_legacy_vk_realty_dashboard(request: Request):
    if CLIENT_BASE_PATH == LEGACY_BASE_PATH:
        return await get_vk_realty_dashboard(request)
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}/dashboard", status_code=301)


@app.get(f"{LEGACY_BASE_PATH}/o/{{role_slug}}", response_class=HTMLResponse, include_in_schema=False)
async def get_legacy_vk_workspace_root(role_slug: str):
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}/o/{role_slug}/dashboard", status_code=301)


@app.get(f"{LEGACY_BASE_PATH}/o/{{role_slug}}/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def get_legacy_vk_workspace_dashboard(role_slug: str, request: Request):
    if CLIENT_BASE_PATH == LEGACY_BASE_PATH:
        return await get_vk_workspace_dashboard(role_slug, request)
    return RedirectResponse(url=f"{CLIENT_BASE_PATH}/o/{role_slug}/dashboard", status_code=301)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_server:app", host="0.0.0.0", port=8000, reload=True)
