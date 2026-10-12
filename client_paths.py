"""Client dashboard URL prefix — set CLIENT_BASE_PATH=/realestate in production."""
from __future__ import annotations

import os

_DEFAULT = "/vk-realty-hme"
_LEGACY = "/vk-realty-hme"


def client_base_path() -> str:
    raw = (os.environ.get("CLIENT_BASE_PATH") or _DEFAULT).strip()
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw.rstrip("/") or _DEFAULT


CLIENT_BASE_PATH = client_base_path()
LEGACY_BASE_PATH = _LEGACY