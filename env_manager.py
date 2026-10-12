"""Read/write dashboard-managed environment variables (.env)."""

from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"

MASK_SENTINEL = "••••"

# All keys manageable from the dashboard API settings panel.
API_KEY_GROUPS: list[dict] = [
    {
        "id": "livekit",
        "label": "LiveKit",
        "keys": [
            {"env": "LIVEKIT_URL", "label": "LiveKit URL", "secret": False},
            {"env": "LIVEKIT_API_KEY", "label": "LiveKit API Key", "secret": True},
            {"env": "LIVEKIT_API_SECRET", "label": "LiveKit API Secret", "secret": True},
            {"env": "LIVEKIT_AGENT_NAME", "label": "Agent Name", "secret": False},
        ],
    },
    {
        "id": "supabase",
        "label": "Supabase",
        "keys": [
            {"env": "SUPABASE_URL", "label": "Supabase URL", "secret": False},
            {"env": "SUPABASE_KEY", "label": "Supabase Anon Key", "secret": True},
            {"env": "SUPABASE_SERVICE_ROLE_KEY", "label": "Service Role Key", "secret": True},
            {"env": "SUPABASE_S3_ACCESS_KEY", "label": "S3 Access Key", "secret": True},
            {"env": "SUPABASE_S3_SECRET_KEY", "label": "S3 Secret Key", "secret": True},
            {"env": "SUPABASE_S3_ENDPOINT", "label": "S3 Endpoint", "secret": False},
            {"env": "SUPABASE_S3_REGION", "label": "S3 Region", "secret": False},
        ],
    },
    {
        "id": "llm",
        "label": "LLM Providers",
        "keys": [
            {"env": "GEMINI_API_KEY", "label": "Gemini API Key", "secret": True},
            {"env": "OPENAI_API_KEY", "label": "OpenAI API Key", "secret": True},
            {"env": "GROQ_API_KEY", "label": "Groq API Key", "secret": True},
            {"env": "ANTHROPIC_API_KEY", "label": "Anthropic API Key", "secret": True},
            {"env": "DEEPSEEK_API_KEY", "label": "DeepSeek API Key", "secret": True},
        ],
    },
    {
        "id": "speech",
        "label": "Speech (STT/TTS)",
        "keys": [
            {"env": "TTS_PROVIDER", "label": "TTS Provider (cartesia|gemini|sarvam)", "secret": False},
            {"env": "CARTESIA_API_KEY", "label": "Cartesia API Key", "secret": True},
            {"env": "CARTESIA_TTS_MODEL", "label": "Cartesia Model (e.g. sonic-3.5)", "secret": False},
            {"env": "SARVAM_API_KEY", "label": "Sarvam API Key", "secret": True},
            {"env": "DEEPGRAM_API_KEY", "label": "Deepgram API Key", "secret": True},
            {"env": "SONIOX_API_KEY", "label": "Soniox API Key", "secret": True},
        ],
    },
    {
        "id": "telephony",
        "label": "Telephony & SIP",
        "keys": [
            {"env": "SIP_TRUNK_ID", "label": "SIP Trunk ID", "secret": False},
            {"env": "OUTBOUND_TRUNK_ID", "label": "Outbound Trunk ID", "secret": False},
            {"env": "INBOUND_PHONE_NUMBERS", "label": "Inbound Numbers (comma-separated)", "secret": False},
            {"env": "DEFAULT_TRANSFER_NUMBER", "label": "Default Transfer Number", "secret": False},
            {"env": "VOBIZ_SIP_DOMAIN", "label": "Vobiz SIP Domain", "secret": False},
            {"env": "VOBIZ_USERNAME", "label": "Vobiz Username", "secret": False},
            {"env": "VOBIZ_PASSWORD", "label": "Vobiz Password", "secret": True},
            {"env": "VOBIZ_OUTBOUND_NUMBER", "label": "Vobiz Outbound Number", "secret": False},
        ],
    },
    {
        "id": "notifications",
        "label": "Notifications",
        "keys": [
            {"env": "TWILIO_ACCOUNT_SID", "label": "Twilio Account SID", "secret": True},
            {"env": "TWILIO_AUTH_TOKEN", "label": "Twilio Auth Token", "secret": True},
            {"env": "TWILIO_WHATSAPP_NUMBER", "label": "Twilio WhatsApp Number", "secret": False},
            {"env": "NOTIFY_EMAIL", "label": "Notify Email", "secret": False},
            {"env": "AGENT_NOTIFICATION_URL", "label": "Webhook Notification URL", "secret": False},
        ],
    },
    {
        "id": "integrations",
        "label": "Integrations",
        "keys": [
            {"env": "CAL_API_KEY", "label": "Cal.com API Key", "secret": True},
            {"env": "CAL_EVENT_TYPE_ID", "label": "Cal Event Type ID", "secret": False},
            {"env": "GOOGLE_CALENDAR_ID", "label": "Google Calendar ID", "secret": False},
            {"env": "GOOGLE_SERVICE_ACCOUNT_FILE", "label": "Google Service Account File", "secret": False},
            {"env": "CRM_TYPE", "label": "CRM Type (none|twenty|hubspot|salesforce|webhook)", "secret": False},
            {"env": "CRM_API_KEY", "label": "CRM API Key", "secret": True},
            {"env": "CRM_BASE_URL", "label": "CRM Base URL", "secret": False},
            {"env": "CRM_WEBHOOK_SECRET", "label": "CRM Webhook Secret", "secret": True},
            {"env": "CRM_WEBHOOK_URL", "label": "CRM Webhook URL", "secret": False},
            {"env": "WHATSAPP_API_URL", "label": "WhatsApp API URL", "secret": False},
            {"env": "REDIS_URL", "label": "Redis URL", "secret": True},
            {"env": "SENTRY_DSN", "label": "Sentry DSN", "secret": True},
        ],
    },
    {
        "id": "dashboard",
        "label": "Dashboard & Security",
        "keys": [
            {"env": "DASHBOARD_USERNAME", "label": "Dashboard Username", "secret": False},
            {"env": "DASHBOARD_PASSWORD", "label": "Dashboard Password", "secret": True},
            {"env": "JWT_SECRET", "label": "JWT Secret", "secret": True},
            {"env": "API_SETTINGS_PASSWORD", "label": "API Settings Panel Password", "secret": True},
            {"env": "ALLOWED_ORIGINS", "label": "Allowed Origins (CORS)", "secret": False},
            {"env": "ENVIRONMENT", "label": "Environment", "secret": False},
            {"env": "DEFAULT_AGENT_ID", "label": "Default Active Agent ID", "secret": False},
        ],
    },
]

MANAGED_ENV_KEYS = {
    item["env"]
    for group in API_KEY_GROUPS
    for item in group["keys"]
}


def _quote_env(value: str) -> str:
    raw = str(value)
    if re.search(r'[\s#"\'\\]', raw):
        escaped = raw.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return raw


def _unquote_env(value: str) -> str:
    raw = value.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return raw


def read_env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        out[key.strip()] = _unquote_env(val)
    return out


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    text = str(value)
    if len(text) <= 4:
        return MASK_SENTINEL
    return f"{MASK_SENTINEL}{text[-4:]}"


def is_masked_value(value: str | None) -> bool:
    return str(value or "").startswith(MASK_SENTINEL)


def get_api_keys_for_dashboard() -> dict:
    file_vals = read_env_file()
    groups = []
    for group in API_KEY_GROUPS:
        items = []
        for spec in group["keys"]:
            env_key = spec["env"]
            raw = os.environ.get(env_key) or file_vals.get(env_key) or ""
            display = mask_secret(raw) if spec.get("secret") and raw else raw
            items.append({
                "env": env_key,
                "label": spec["label"],
                "secret": bool(spec.get("secret")),
                "value": display,
                "is_set": bool(raw),
            })
        groups.append({"id": group["id"], "label": group["label"], "keys": items})
    return {
        "env_file": str(ENV_FILE.name),
        "groups": groups,
    }


def delete_env_keys(keys: list[str]) -> list[str]:
    """Remove keys from .env and os.environ."""
    if not keys:
        return []
    target = {k.strip() for k in keys if k and k.strip()}
    if not target:
        return []

    removed: list[str] = []
    lines: list[str] = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in target:
                    removed.append(key)
                    continue
            lines.append(line)

    if removed:
        ENV_FILE.write_text("\n".join(lines).rstrip() + ("\n" if lines else ""), encoding="utf-8")
        for key in removed:
            os.environ.pop(key, None)
        try:
            from secrets_manager import clear_secrets_cache
            clear_secrets_cache()
        except Exception:
            pass
    return removed


def update_env_keys(updates: dict[str, str | None]) -> dict[str, str]:
    """Persist updates to .env and os.environ. Skips masked placeholders."""
    file_vals = read_env_file()
    applied: dict[str, str] = {}

    for key, value in updates.items():
        if key not in MANAGED_ENV_KEYS:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if is_masked_value(text):
            continue
        applied[key] = text
        file_vals[key] = text
        os.environ[key] = text

    if not applied:
        return applied

    lines: list[str] = []
    seen: set[str] = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in applied:
                    lines.append(f"{key}={_quote_env(applied[key])}")
                    seen.add(key)
                    continue
            lines.append(line)

    for key, val in applied.items():
        if key not in seen:
            lines.append(f"{key}={_quote_env(val)}")

    ENV_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    try:
        from secrets_manager import clear_secrets_cache

        clear_secrets_cache()
    except Exception:
        pass

    return applied


def api_settings_password() -> str:
    return (
        os.environ.get("API_SETTINGS_PASSWORD")
        or read_env_file().get("API_SETTINGS_PASSWORD")
        or "test@123"
    )