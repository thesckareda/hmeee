"""Request-scoped organisation context for multi-tenant data filtering."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

DEFAULT_ORG_ID = "00000000-0000-4000-8000-000000000001"

_current_org_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_org_id", default=None
)
_org_filter_enabled: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "org_filter_enabled", default=True
)


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    email: str
    org_id: str | None
    role: str
    is_superadmin: bool = False

    @property
    def username(self) -> str:
        return self.email

    def can_manage_org(self) -> bool:
        return self.is_superadmin or self.role in ("owner", "admin")

    def can_manage_platform(self) -> bool:
        return self.is_superadmin


def set_request_org(org_id: str | None, *, filter_enabled: bool = True) -> None:
    _current_org_id.set(org_id)
    _org_filter_enabled.set(filter_enabled)


def get_request_org_id() -> str | None:
    return _current_org_id.get()


def org_filter_active() -> bool:
    return _org_filter_enabled.get() and bool(get_request_org_id())


def apply_org_filter(query: Any, *, column: str = "org_id") -> Any:
    """Add org_id equality filter when tenancy context is set."""
    org_id = get_request_org_id()
    if not org_id or not _org_filter_enabled.get():
        return query
    try:
        return query.eq(column, org_id)
    except Exception:
        return query


def inject_org_id(data: dict, *, column: str = "org_id") -> dict:
    """Attach org_id to insert/update payloads when context is set."""
    org_id = get_request_org_id()
    if org_id and column not in data:
        data = dict(data)
        data[column] = org_id
    return data