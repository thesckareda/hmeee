"""Shared LiveKit server API client with self-hosted SSL support."""

from __future__ import annotations

import os
import ssl

import aiohttp
import certifi
from livekit import api


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _insecure_skip_verify() -> bool:
    explicit = os.getenv("LIVEKIT_INSECURE_SKIP_VERIFY")
    if explicit is not None and explicit.strip() != "":
        return _truthy(explicit)
    # Self-hosted setups commonly use http:// API URLs that 308-redirect to https
    # with a self-signed cert. Default to skipping verification in that case.
    api_url = (os.getenv("LIVEKIT_API_URL") or "").strip().lower()
    return api_url.startswith("http://")


def normalize_livekit_api_url(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("wss://"):
        return "https://" + value[len("wss://") :]
    if value.startswith("ws://"):
        return "http://" + value[len("ws://") :]
    if value.startswith("http://") and _insecure_skip_verify():
        return "https://" + value[len("http://") :]
    return value


def resolve_livekit_api_url() -> str:
    for key in ("LIVEKIT_API_URL", "LIVEKIT_URL"):
        normalized = normalize_livekit_api_url(os.getenv(key, ""))
        if normalized:
            return normalized
    return ""


def _ssl_connector() -> aiohttp.TCPConnector:
    """aiohttp ignores SSL_CERT_FILE on macOS — always pass certifi's CA bundle."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    if _insecure_skip_verify():
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
    return aiohttp.TCPConnector(ssl=ssl_ctx)


def create_livekit_api(
    *,
    url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> api.LiveKitAPI:
    """Return a LiveKitAPI client that works with self-hosted TLS redirects."""
    resolved_url = normalize_livekit_api_url(url or "") or resolve_livekit_api_url()
    if not resolved_url:
        raise ValueError("LiveKit API URL not configured")

    key = api_key or os.getenv("LIVEKIT_API_KEY", "")
    secret = api_secret or os.getenv("LIVEKIT_API_SECRET", "")
    if not key or not secret:
        raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")

    session = aiohttp.ClientSession(connector=_ssl_connector())
    return api.LiveKitAPI(url=resolved_url, api_key=key, api_secret=secret, session=session)