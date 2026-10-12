"""LiveKit room composite egress → Supabase Storage (S3-compatible)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

_PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(_PROJECT_DIR / ".env")

logger = logging.getLogger("recording")

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()

S3_ACCESS_KEY = os.getenv("SUPABASE_S3_ACCESS_KEY", "").strip()
S3_SECRET_KEY = os.getenv("SUPABASE_S3_SECRET_KEY", "").strip()
S3_ENDPOINT = os.getenv("SUPABASE_S3_ENDPOINT", "").strip()
S3_REGION = os.getenv("SUPABASE_S3_REGION", "ap-south-1").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_BUCKET = (
    os.getenv("SUPABASE_RECORDINGS_BUCKET", "").strip()
    or os.getenv("SUPABASE_BUCKET", "").strip()
    or "call-recordings"
)


def _livekit_api_url() -> str:
    """LiveKitAPI expects HTTPS; LIVEKIT_URL is usually wss://."""
    return LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")


def build_recording_url(room_name: str) -> str:
    if not SUPABASE_URL or not room_name:
        return ""
    return (
        f"{SUPABASE_URL}/storage/v1/object/public/"
        f"{SUPABASE_BUCKET}/recordings/{room_name}.ogg"
    )


def recording_configured() -> bool:
    return bool(
        LIVEKIT_URL
        and LIVEKIT_API_KEY
        and LIVEKIT_API_SECRET
        and S3_ACCESS_KEY
        and S3_SECRET_KEY
        and S3_ENDPOINT
        and SUPABASE_URL
    )


def _missing_recording_env() -> list[str]:
    required = {
        "LIVEKIT_URL": LIVEKIT_URL,
        "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
        "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
        "SUPABASE_S3_ACCESS_KEY": S3_ACCESS_KEY,
        "SUPABASE_S3_SECRET_KEY": S3_SECRET_KEY,
        "SUPABASE_S3_ENDPOINT": S3_ENDPOINT,
        "SUPABASE_URL": SUPABASE_URL,
    }
    return [key for key, value in required.items() if not value]


def _s3_upload() -> api.S3Upload:
    return api.S3Upload(
        access_key=S3_ACCESS_KEY,
        secret=S3_SECRET_KEY,
        endpoint=S3_ENDPOINT,
        bucket=SUPABASE_BUCKET,
        region=S3_REGION,
        force_path_style=True,
    )


async def start_recording(room_name: str) -> dict:
    missing = _missing_recording_env()
    if missing:
        return {
            "success": False,
            "error": f"Recording not configured. Missing: {', '.join(missing)}",
        }

    lk_api = None
    try:
        from livekit_api_client import create_livekit_api

        lk_api = create_livekit_api()

        request = api.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=True,
            file_outputs=[
                api.EncodedFileOutput(
                    file_type=api.EncodedFileType.OGG,
                    filepath=f"recordings/{room_name}.ogg",
                    disable_manifest=True,
                    s3=_s3_upload(),
                )
            ],
        )

        info = await lk_api.egress.start_room_composite_egress(request)
        recording_url = build_recording_url(room_name)
        egress_id = getattr(info, "egress_id", None)
        logger.info("[RECORDING] Started egress_id=%s room=%s", egress_id, room_name)
        return {
            "success": True,
            "egress_id": egress_id,
            "room_name": room_name,
            "recording_url": recording_url,
        }
    except Exception as exc:
        logger.error("[RECORDING] Start failed for room %s: %s", room_name, exc)
        return {"success": False, "error": str(exc)}
    finally:
        if lk_api is not None:
            await lk_api.aclose()


async def stop_recording(egress_id: str, room_name: str) -> dict:
    """Stop egress and return the Supabase public URL for the uploaded OGG."""
    if not egress_id:
        return {"success": False, "error": "Missing egress_id"}

    lk_api = None
    try:
        from livekit_api_client import create_livekit_api

        lk_api = create_livekit_api()
        await lk_api.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
        # Give egress time to flush the file to Supabase S3.
        await asyncio.sleep(3)

        recording_url = build_recording_url(room_name)
        logger.info("[RECORDING] Stopped egress_id=%s room=%s url=%s", egress_id, room_name, recording_url)
        return {
            "success": True,
            "egress_id": egress_id,
            "room_name": room_name,
            "recording_url": recording_url,
        }
    except Exception as exc:
        err = str(exc).lower()
        # Egress may already be complete or cleaned up by LiveKit — still return URL.
        if "not_found" in err or "does not exist" in err or "404" in err:
            recording_url = build_recording_url(room_name)
            logger.warning(
                "[RECORDING] Egress already gone egress_id=%s room=%s — using stored URL",
                egress_id,
                room_name,
            )
            return {
                "success": True,
                "egress_id": egress_id,
                "room_name": room_name,
                "recording_url": recording_url,
                "already_stopped": True,
            }
        logger.error("[RECORDING] Stop failed egress_id=%s room=%s: %s", egress_id, room_name, exc)
        return {"success": False, "error": str(exc)}
    finally:
        if lk_api is not None:
            await lk_api.aclose()