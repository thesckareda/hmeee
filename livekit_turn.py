"""LiveKit embedded TURN credential helpers + optional public ICE server override."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Iterable

import base62


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def turn_urls_from_env() -> list[str]:
    """Public TURN/STUN URLs — only when LIVEKIT_TURN_URLS or LIVEKIT_TURN_DOMAIN is set."""
    raw = (os.getenv("LIVEKIT_TURN_URLS") or "").strip()
    if raw:
        return [u.strip() for u in raw.split(",") if u.strip()]
    domain = (os.getenv("LIVEKIT_TURN_DOMAIN") or "").strip()
    if domain:
        return [f"turn:{domain}:443?transport=tcp"]
    return []


def ice_override_enabled() -> bool:
    return _truthy(os.getenv("LIVEKIT_ICE_SERVERS_OVERRIDE"))


def create_turn_credentials(
    *,
    api_key: str,
    api_secret: str,
    participant_identity: str,
    ttl_seconds: int | None = None,
) -> tuple[str, str]:
    """Match livekit-server/pkg/service/turn.go TURNAuthHandler."""
    ttl = ttl_seconds or int(os.getenv("LIVEKIT_TURN_TTL_SEC", "3600"))
    expiry = int(time.time()) + max(60, ttl)
    username_raw = f"{api_key}|{participant_identity}|{expiry}"
    username = base62.encodebytes(username_raw.encode())
    key_input = f"{api_secret}|{participant_identity}|{expiry}"
    password = base62.encodebytes(hashlib.sha256(key_input.encode()).digest())
    return username, password


def build_ice_servers(
    *,
    participant_identity: str,
    urls: Iterable[str] | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> list:
    """Return proto IceServer list for rtc.RtcConfiguration.ice_servers."""
    import livekit.rtc._proto.room_pb2 as pb

    turn_urls = list(urls) if urls is not None else turn_urls_from_env()
    if not turn_urls:
        return []

    key = api_key or os.getenv("LIVEKIT_API_KEY", "")
    secret = api_secret or os.getenv("LIVEKIT_API_SECRET", "")
    if not key or not secret:
        raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET required for TURN override")

    username, password = create_turn_credentials(
        api_key=key,
        api_secret=secret,
        participant_identity=participant_identity,
    )
    server = pb.IceServer()
    server.urls.extend(turn_urls)
    server.username = username
    server.password = password
    return [server]