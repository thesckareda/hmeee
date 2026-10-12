import os
from dataclasses import dataclass

from secrets_manager import get_secret


def _is_placeholder_secret(val: str | None) -> bool:
    from gemini_tts import is_usable_gemini_api_key

    return not is_usable_gemini_api_key(val)


@dataclass(frozen=True)
class CallConfig:
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    supabase_url: str
    supabase_key: str
    google_api_key: str | None
    gemini_api_key: str | None
    openai_api_key: str | None
    groq_api_key: str | None
    cal_api_key: str | None
    cal_event_type_id: str | None
    sarvam_api_key: str | None
    cartesia_api_key: str | None
    soniox_api_key: str | None
    supabase_s3_access_key: str | None
    supabase_s3_secret_key: str | None
    supabase_s3_endpoint: str | None
    supabase_s3_region: str


def build_call_config(live_config: dict) -> CallConfig:
    def _resolve(lc_key: str, *env_names: str, default: str | None = None) -> str:
        """live_config value wins; then try each env name via get_secret; then default."""
        val = live_config.get(lc_key)
        if val is not None and val != "" and not _is_placeholder_secret(str(val)):
            return str(val)
        for env_name in env_names:
            env_val = get_secret(env_name)
            if env_val is not None and env_val != "":
                return str(env_val)
        if default is not None:
            return default
        return ""

    return CallConfig(
        livekit_url=_resolve("livekit_url", "LIVEKIT_URL"),
        livekit_api_key=_resolve("livekit_api_key", "LIVEKIT_API_KEY"),
        livekit_api_secret=_resolve("livekit_api_secret", "LIVEKIT_API_SECRET"),
        supabase_url=_resolve("supabase_url", "SUPABASE_URL"),
        supabase_key=_resolve("supabase_key", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_KEY"),
        google_api_key=_resolve("google_api_key", "GOOGLE_API_KEY"),
        gemini_api_key=_resolve("gemini_api_key", "GEMINI_API_KEY"),
        openai_api_key=_resolve("openai_api_key", "OPENAI_API_KEY"),
        groq_api_key=_resolve("groq_api_key", "GROQ_API_KEY"),
        cal_api_key=_resolve("cal_api_key", "CAL_API_KEY"),
        cal_event_type_id=_resolve("cal_event_type_id", "CAL_EVENT_TYPE_ID"),
        sarvam_api_key=_resolve("sarvam_api_key", "SARVAM_API_KEY"),
        cartesia_api_key=_resolve("cartesia_api_key", "CARTESIA_API_KEY"),
        soniox_api_key=_resolve("soniox_api_key", "SONIOX_API_KEY"),
        supabase_s3_access_key=_resolve("supabase_s3_access_key", "SUPABASE_S3_ACCESS_KEY"),
        supabase_s3_secret_key=_resolve("supabase_s3_secret_key", "SUPABASE_S3_SECRET_KEY"),
        supabase_s3_endpoint=_resolve("supabase_s3_endpoint", "SUPABASE_S3_ENDPOINT"),
        supabase_s3_region=_resolve("supabase_s3_region", "SUPABASE_S3_REGION", default="ap-south-1"),
    )
