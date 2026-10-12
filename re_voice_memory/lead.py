"""Lead profile CRUD — single source of truth for lead identity."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False

from re_voice_memory.constants import MEMORY_DIR
from voice_memory.fs import atomic_write_text

# ── Per-phone asyncio lock (STEP 16) ────────────────────────────────────────
_phone_locks: dict[str, asyncio.Lock] = {}
_phone_locks_meta = asyncio.Lock()


async def _get_phone_lock(phone: str) -> asyncio.Lock:
    """Return a per-phone asyncio.Lock, creating it under a meta-lock if needed."""
    lock = _phone_locks.get(phone)
    if lock is not None:
        return lock
    async with _phone_locks_meta:
        lock = _phone_locks.get(phone)
        if lock is None:
            lock = asyncio.Lock()
            _phone_locks[phone] = lock
        return lock


LEADID_NAMESPACE = uuid.UUID("a3f1b8c0-4e2d-7f9a-8b1c-5d6e0f3a2c4b")

Role = Literal["buyer", "seller", "renter", "investor"]
LoanStatus = Literal["pre_approved", "exploring", "cash", "unknown"]
DealStage = Literal["cold", "aware", "interested", "viewing", "hot", "offer", "closed", "lost"]
Language = Literal["en", "hi", "hinglish", "tamil", "telugu"]


@dataclass
class LeadProfile:
    phone: str
    lead_id: str = ""
    stable_name: str = ""
    role: Role = "buyer"
    budget_min: int | None = None
    budget_max: int | None = None
    preferred_areas: list[str] = field(default_factory=list)
    excluded_areas: list[str] = field(default_factory=list)
    must_haves: list[str] = field(default_factory=list)
    deal_breakers: list[str] = field(default_factory=list)
    timeline_weeks: int | None = None
    loan_status: LoanStatus = "unknown"
    decision_factors: list[str] = field(default_factory=list)
    intent_score: float = 5.0
    deal_stage: DealStage = "cold"
    assigned_agent: str | None = None
    call_count: int = 0
    last_contact_date: str | None = None
    language_pref: Language = "en"
    notes: str = ""
    # IQ Phase 2 — additive fields (old profiles read as empty)
    relation_names: list[dict] = field(default_factory=list)  # [{name, relation, context}] — IQ #4
    slot_values: dict = field(default_factory=dict)  # {budget, location, bhk, timeline, purpose} — IQ #17

    def __post_init__(self):
        if not self.lead_id:
            self.lead_id = str(uuid.uuid5(LEADID_NAMESPACE, self.phone))


def _profile_path(phone: str) -> Path:
    return MEMORY_DIR / "leads" / phone / "lead_profile.md"


def _profile_to_frontmatter(profile: LeadProfile) -> dict[str, Any]:
    """Convert LeadProfile to ordered YAML-safe dict."""
    return {
        "lead_id": profile.lead_id,
        "phone": profile.phone,
        "stable_name": profile.stable_name,
        "role": profile.role,
        "budget_min": profile.budget_min,
        "budget_max": profile.budget_max,
        "preferred_areas": profile.preferred_areas,
        "excluded_areas": profile.excluded_areas,
        "must_haves": profile.must_haves,
        "deal_breakers": profile.deal_breakers,
        "timeline_weeks": profile.timeline_weeks,
        "loan_status": profile.loan_status,
        "decision_factors": profile.decision_factors,
        "intent_score": profile.intent_score,
        "deal_stage": profile.deal_stage,
        "call_count": profile.call_count,
        "last_contact_date": profile.last_contact_date,
        "language_pref": profile.language_pref,
        "assigned_agent": profile.assigned_agent,
        "relation_names": profile.relation_names,
        "slot_values": profile.slot_values,
        "ttl_days": 365,
        "type": "lead_profile",
    }


def _render_frontmatter_yaml(metadata: dict[str, Any]) -> str:
    """Render metadata dict as YAML frontmatter block with --- delimiters."""
    if not _HAS_YAML or yaml is None:
        raise RuntimeError("yaml module is not available; install PyYAML")
    yaml_str = yaml.safe_dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False)
    yaml_str = yaml_str.rstrip("\n")
    return f"---\n{yaml_str}\n---"


def _parse_frontmatter_yaml(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter enclosed by --- delimiters."""
    if not _HAS_YAML or yaml is None:
        raise RuntimeError("yaml module is not available; install PyYAML")
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}
    raw_yaml = "\n".join(lines[1:end_idx])
    try:
        loaded = yaml.safe_load(raw_yaml) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(k): v for k, v in loaded.items()}


def _metadata_to_profile(metadata: dict[str, Any]) -> LeadProfile:
    """Populate LeadProfile from parsed YAML metadata dict."""
    profile = LeadProfile(
        phone=str(metadata.get("phone", "")),
        lead_id=str(metadata.get("lead_id", "")),
        stable_name=str(metadata.get("stable_name", "")),
        role=metadata.get("role", "buyer"),
        budget_min=_coerce_optional_int(metadata.get("budget_min")),
        budget_max=_coerce_optional_int(metadata.get("budget_max")),
        preferred_areas=_coerce_str_list(metadata.get("preferred_areas")),
        excluded_areas=_coerce_str_list(metadata.get("excluded_areas")),
        must_haves=_coerce_str_list(metadata.get("must_haves")),
        deal_breakers=_coerce_str_list(metadata.get("deal_breakers")),
        timeline_weeks=_coerce_optional_int(metadata.get("timeline_weeks")),
        loan_status=metadata.get("loan_status", "unknown"),
        decision_factors=_coerce_str_list(metadata.get("decision_factors")),
        intent_score=float(metadata.get("intent_score", 5.0)),
        deal_stage=metadata.get("deal_stage", "cold"),
        assigned_agent=metadata.get("assigned_agent"),
        call_count=int(metadata.get("call_count", 0)),
        last_contact_date=metadata.get("last_contact_date"),
        language_pref=metadata.get("language_pref", "en"),
        relation_names=[r for r in (metadata.get("relation_names") or []) if isinstance(r, dict)],
        slot_values=metadata.get("slot_values") if isinstance(metadata.get("slot_values"), dict) else {},
    )
    return profile


def load_lead_profile(phone: str) -> LeadProfile | None:
    """Load a lead profile from disk. Returns None if file is missing or unreadable."""
    path = _profile_path(phone)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None

    metadata = _parse_frontmatter_yaml(raw)
    if not metadata:
        return None

    body_start = 0
    lines = raw.splitlines()
    frontmatter_closed = False
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not frontmatter_closed and i > 0:
                frontmatter_closed = True
                body_start = i + 1
            elif frontmatter_closed:
                body_start = i + 1
                break

    body = "\n".join(lines[body_start:]).strip()

    profile = _metadata_to_profile(metadata)
    if body:
        profile.notes = body
    return profile


def _write_lead_profile_nolock(profile: LeadProfile) -> Path:
    """Write lead profile to disk (caller must hold per-phone lock)."""
    path = _profile_path(profile.phone)
    metadata = _profile_to_frontmatter(profile)
    frontmatter = _render_frontmatter_yaml(metadata)
    body = profile.notes.strip()
    if body:
        content = f"{frontmatter}\n\n{body}\n"
    else:
        content = f"{frontmatter}\n"
    atomic_write_text(path, content)
    return path


async def save_lead_profile(profile: LeadProfile) -> Path:
    """Write lead profile to disk as YAML frontmatter + markdown body (per-phone locked)."""
    lock = await _get_phone_lock(profile.phone)
    async with lock:
        return _write_lead_profile_nolock(profile)


async def upsert_lead_profile(phone: str, **updates: Any) -> LeadProfile:
    """Load existing profile or create default, apply updates, save, and return (per-phone locked)."""
    lock = await _get_phone_lock(phone)
    async with lock:
        profile = load_lead_profile(phone)
        if profile is None:
            profile = LeadProfile(phone=phone, role="buyer")
        for key, value in updates.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        _write_lead_profile_nolock(profile)
        return profile


async def get_or_create_lead(phone: str, stable_name: str = "") -> LeadProfile:
    """Return existing lead profile or create a minimal new one (per-phone locked)."""
    lock = await _get_phone_lock(phone)
    async with lock:
        profile = load_lead_profile(phone)
        if profile is not None:
            return profile
        profile = LeadProfile(phone=phone, stable_name=stable_name, role="buyer")
        _write_lead_profile_nolock(profile)
        return profile


def _coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return []
