"""VK Realty portal — workspace roles, feature gates, dashboard URLs."""

from __future__ import annotations

import json
from typing import Any

PORTAL_FEATURES: dict[str, str] = {
    "overview": "Today",
    "leads": "All Leads",
    "hot-leads": "Priority Leads",
    "performance": "Performance",
    "listing-performance": "Listings",
    "reports": "Reports",
    "main-agent": "Arjun (AI consultant)",
    "lang-presets": "How Arjun speaks",
    "assistants": "AI assistants",
    "call-logs": "Call history",
    "analytics": "Numbers & trends",
    "calendar": "Site visits",
    "campaigns": "Outbound lists",
    "settings": "Settings",
    "crm": "CRM",
    "knowledge": "Property information",
    "integrations": "Connections",
    "billing": "Billing",
    "organisation": "Organisation",
}

# Non-technical VK Realty clients — simplified 6-section nav
CLIENT_PORTAL_FEATURES = [
    "overview",
    "leads",
    "hot-leads",
    "call-logs",
    "calendar",
    "reports",
    "settings",
]

FULL_VK_FEATURES = list(PORTAL_FEATURES.keys())
DEFAULT_VK_FEATURES = list(CLIENT_PORTAL_FEATURES)

MEMBER_PORTAL_FEATURES = [
    k for k in FULL_VK_FEATURES if k not in ("organisation", "billing", "integrations")
]


def parse_org_metadata(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def org_features(org: dict | None) -> list[str]:
    if not org:
        return list(DEFAULT_VK_FEATURES)
    meta = parse_org_metadata(org.get("metadata"))
    feats = meta.get("features")
    if isinstance(feats, list) and feats:
        result = [f for f in feats if f in PORTAL_FEATURES]
    else:
        result = list(DEFAULT_VK_FEATURES)
    # Parent workspace always retains outbound campaigns (legacy metadata may omit it).
    if not org.get("parent_org_id") and "campaigns" not in result:
        result.append("campaigns")
    return result


def workspace_portal_path(slug: str) -> str:
    from client_paths import CLIENT_BASE_PATH

    return f"{CLIENT_BASE_PATH}/o/{slug}/dashboard"


def workspace_portal_url(slug: str, *, base: str = "") -> str:
    path = workspace_portal_path(slug)
    return f"{base.rstrip('/')}{path}" if base else path