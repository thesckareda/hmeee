"""Central secrets resolution — env vars only, never config.json."""
import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_secrets() -> dict:
    required = [
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "SUPABASE_URL",
        "SUPABASE_KEY",
    ]
    secrets = {}
    missing = []
    for key in required:
        val = os.environ.get(key)
        if not val:
            missing.append(key)
        secrets[key.lower()] = val
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    optional = [
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPGRAM_API_KEY",
        "SARVAM_API_KEY",
        "CARTESIA_API_KEY",
        "SONIOX_API_KEY",
        "CAL_API_KEY",
        "CAL_EVENT_TYPE_ID",
        "GOOGLE_CALENDAR_ID",
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "REDIS_URL",
        "CRM_API_KEY",
        "CRM_WEBHOOK_SECRET",
        "CRM_WEBHOOK_URL",
        "AGENT_NOTIFICATION_URL",
        "WHATSAPP_API_URL",
        "JWT_SECRET",
        "DASHBOARD_USERNAME",
        "DASHBOARD_PASSWORD",
        "SENTRY_DSN",
        "METRICS_PORT",
        "LOG_LEVEL",
        "ENVIRONMENT",
        "VOBIZ_SIP_DOMAIN",
        "VOBIZ_USERNAME",
        "VOBIZ_PASSWORD",
        "VOBIZ_OUTBOUND_NUMBER",
        "SIP_TRUNK_ID",
        "OUTBOUND_TRUNK_ID",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_S3_ACCESS_KEY",
        "SUPABASE_S3_SECRET_KEY",
        "SUPABASE_S3_ENDPOINT",
        "SUPABASE_S3_REGION",
        "SUPABASE_RECORDINGS_BUCKET",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_WHATSAPP_NUMBER",
        "DEEPSEEK_API_KEY",
        "RE_MEMORY_DIR",
        "RE_MEMORY_ENABLED",
        "DASHBOARD_RATE_LIMIT_BACKEND",
    ]
    for key in optional:
        secrets[key.lower()] = os.environ.get(key)
    return secrets


def get_secret(key: str) -> str | None:
    return get_secrets().get(key.lower())


def clear_secrets_cache() -> None:
    get_secrets.cache_clear()
