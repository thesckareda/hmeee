"""SQLite-backed organisations, users, and memberships (local E2E without Supabase auth)."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import bcrypt
from secrets_manager import get_secret

from org_portal import parse_org_metadata
from tenancy import DEFAULT_ORG_ID

logger = logging.getLogger("org_store")

DATA_DIR = Path(__file__).resolve().parent / "data"
TENANCY_DB = DATA_DIR / "tenancy.db"

ROLES = frozenset({"owner", "admin", "member", "viewer"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    slug = slug.strip("-") or "org"
    return slug[:48]


@contextmanager
def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(TENANCY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                slug        TEXT NOT NULL UNIQUE,
                status      TEXT NOT NULL DEFAULT 'active',
                metadata    TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id              TEXT PRIMARY KEY,
                email           TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash   TEXT NOT NULL,
                display_name    TEXT NOT NULL DEFAULT '',
                is_superadmin   INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memberships (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                org_id      TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                role        TEXT NOT NULL DEFAULT 'member',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                UNIQUE (user_id, org_id)
            );

            CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
            CREATE INDEX IF NOT EXISTS idx_memberships_org ON memberships(org_id);
            """
        )
        try:
            conn.execute(
                "ALTER TABLE organizations ADD COLUMN parent_org_id TEXT "
                "REFERENCES organizations(id) ON DELETE SET NULL"
            )
        except sqlite3.OperationalError:
            pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_organizations_parent ON organizations(parent_org_id)"
        )


def _row_to_org(row: sqlite3.Row) -> dict:
    keys = row.keys()
    meta = parse_org_metadata(row["metadata"])
    out = {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "status": row["status"],
        "metadata": meta,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if "parent_org_id" in keys:
        out["parent_org_id"] = row["parent_org_id"]
    return out


def _row_to_user(row: sqlite3.Row, *, include_hash: bool = False) -> dict:
    out = {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "is_superadmin": bool(row["is_superadmin"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_hash:
        out["password_hash"] = row["password_hash"]
    return out


def _row_to_membership(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "org_id": row["org_id"],
        "role": row["role"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def ensure_bootstrap() -> None:
    """Create default org and env-based superadmin on first run."""
    init_db()
    now = _utc_now()
    env_user = (get_secret("dashboard_username") or "admin").strip()
    env_pass = get_secret("dashboard_password") or "change-me-now"
    email = env_user if "@" in env_user else f"{env_user}@local.speaksy"

    with _conn() as conn:
        org = conn.execute(
            "SELECT id FROM organizations WHERE id = ?", (DEFAULT_ORG_ID,)
        ).fetchone()
        if not org:
            conn.execute(
                """
                INSERT INTO organizations (id, name, slug, status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, 'active', '{}', ?, ?)
                """,
                (DEFAULT_ORG_ID, "Default Workspace", "default", now, now),
            )
            logger.info("Created default organisation %s", DEFAULT_ORG_ID)

        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ? COLLATE NOCASE",
            (email,),
        ).fetchone()
        if not user:
            user_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, display_name, is_superadmin, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (user_id, email, hash_password(env_pass), env_user, now, now),
            )
            conn.execute(
                """
                INSERT INTO memberships (id, user_id, org_id, role, created_at, updated_at)
                VALUES (?, ?, ?, 'owner', ?, ?)
                """,
                (str(uuid.uuid4()), user_id, DEFAULT_ORG_ID, now, now),
            )
            logger.info("Bootstrapped superadmin %s", email)
        else:
            membership = conn.execute(
                "SELECT id FROM memberships WHERE user_id = ? AND org_id = ?",
                (user["id"], DEFAULT_ORG_ID),
            ).fetchone()
            if not membership:
                conn.execute(
                    """
                    INSERT INTO memberships (id, user_id, org_id, role, created_at, updated_at)
                    VALUES (?, ?, ?, 'owner', ?, ?)
                    """,
                    (str(uuid.uuid4()), user["id"], DEFAULT_ORG_ID, now, now),
                )


def get_user_by_email(email: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()
        return _row_to_user(row, include_hash=True) if row else None


def get_user_by_id(user_id: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None


def resolve_membership_user_id(user_id: str) -> str | None:
    """Map dev/local auth actors to a real users row for FK-safe membership inserts."""
    if user_id and user_id != "dev-user" and get_user_by_id(user_id):
        return user_id
    ensure_bootstrap()
    env_user = (get_secret("dashboard_username") or "admin").strip()
    email = env_user if "@" in env_user else f"{env_user}@local.speaksy"
    user = get_user_by_email(email)
    return user["id"] if user else None


def authenticate(email: str, password: str) -> dict | None:
    user = get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user.pop("password_hash", "")):
        return None
    return user


def create_user(
    *,
    email: str,
    password: str,
    display_name: str = "",
    is_superadmin: bool = False,
) -> dict:
    init_db()
    now = _utc_now()
    user_id = str(uuid.uuid4())
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ).fetchone()
        if existing:
            raise ValueError("Email already registered")
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, display_name, is_superadmin, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                email.strip().lower(),
                hash_password(password),
                display_name or email.split("@")[0],
                1 if is_superadmin else 0,
                now,
                now,
            ),
        )
    return get_user_by_id(user_id) or {}


def list_user_orgs(user_id: str) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT o.*, m.role AS membership_role
            FROM memberships m
            JOIN organizations o ON o.id = m.org_id
            WHERE m.user_id = ?
            ORDER BY o.name
            """,
            (user_id,),
        ).fetchall()
    out = []
    for row in rows:
        org = _row_to_org(row)
        org["role"] = row["membership_role"]
        out.append(org)
    return out


def get_membership(user_id: str, org_id: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM memberships WHERE user_id = ? AND org_id = ?",
            (user_id, org_id),
        ).fetchone()
        return _row_to_membership(row) if row else None


def resolve_auth_org(user_id: str, org_id: str | None, *, is_superadmin: bool) -> tuple[str, str]:
    """Return (org_id, role) for JWT — validates membership."""
    orgs = list_user_orgs(user_id)
    if not orgs:
        raise ValueError("User has no organisation membership")

    if org_id:
        for org in orgs:
            if org["id"] == org_id:
                return org_id, org["role"]
        if is_superadmin:
            org = get_organization(org_id)
            if org:
                return org_id, "admin"
        raise ValueError("Not a member of this organisation")

    default = orgs[0]
    return default["id"], default["role"]


def create_organization(
    *,
    name: str,
    created_by_user_id: str,
    slug: str | None = None,
    parent_org_id: str | None = None,
    metadata: dict | None = None,
    membership_role: str = "owner",
) -> dict:
    init_db()
    now = _utc_now()
    org_id = str(uuid.uuid4())
    base_slug = _slugify(slug or name)
    slug_candidate = base_slug
    meta_json = json.dumps(metadata or {})
    with _conn() as conn:
        for i in range(20):
            try:
                conn.execute(
                    """
                    INSERT INTO organizations (
                        id, name, slug, status, metadata, parent_org_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (org_id, name.strip(), slug_candidate, meta_json, parent_org_id, now, now),
                )
                break
            except sqlite3.IntegrityError:
                slug_candidate = f"{base_slug}-{i + 1}"
        else:
            raise ValueError("Could not allocate unique slug")

        member_user_id = resolve_membership_user_id(created_by_user_id)
        if member_user_id:
            conn.execute(
                """
                INSERT INTO memberships (id, user_id, org_id, role, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), member_user_id, org_id, membership_role, now, now),
            )
    return get_organization(org_id) or {}


def create_org_workspace(
    *,
    parent_org_id: str,
    name: str,
    role_slug: str,
    features: list[str],
    created_by_user_id: str,
) -> dict:
    from org_portal import PORTAL_FEATURES

    parent = get_organization(parent_org_id)
    if not parent:
        raise ValueError("Parent organisation not found")
    slug = _slugify(role_slug)
    if not slug:
        raise ValueError("Invalid role slug")
    clean_features = [f for f in features if f in PORTAL_FEATURES]
    if not clean_features:
        raise ValueError("Select at least one feature")
    metadata = {
        "portal_type": "vk-realty-hme-workspace",
        "parent_org_id": parent_org_id,
        "features": clean_features,
        "role_name": name.strip(),
    }
    return create_organization(
        name=name.strip(),
        created_by_user_id=created_by_user_id,
        slug=slug,
        parent_org_id=parent_org_id,
        metadata=metadata,
        membership_role="admin",
    )


def list_org_workspaces(parent_org_id: str) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM organizations
            WHERE parent_org_id = ?
            ORDER BY name
            """,
            (parent_org_id,),
        ).fetchall()
    return [_row_to_org(r) for r in rows]


def update_org_workspace_features(org_id: str, features: list[str]) -> dict | None:
    from org_portal import PORTAL_FEATURES

    org = get_organization(org_id)
    if not org:
        return None
    meta = parse_org_metadata(org.get("metadata"))
    clean = [f for f in features if f in PORTAL_FEATURES]
    if not clean:
        raise ValueError("Select at least one feature")
    meta["features"] = clean
    now = _utc_now()
    with _conn() as conn:
        conn.execute(
            "UPDATE organizations SET metadata = ?, updated_at = ? WHERE id = ?",
            (json.dumps(meta), now, org_id),
        )
    return get_organization(org_id)


def list_organizations(*, include_all: bool = False, user_id: str | None = None) -> list[dict]:
    init_db()
    with _conn() as conn:
        if include_all:
            rows = conn.execute(
                "SELECT * FROM organizations ORDER BY name"
            ).fetchall()
            return [_row_to_org(r) for r in rows]
        if not user_id:
            return []
        rows = conn.execute(
            """
            SELECT o.* FROM organizations o
            JOIN memberships m ON m.org_id = o.id
            WHERE m.user_id = ?
            ORDER BY o.name
            """,
            (user_id,),
        ).fetchall()
        return [_row_to_org(r) for r in rows]


def get_organization(org_id: str) -> dict | None:
    init_db()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
        return _row_to_org(row) if row else None


def get_organization_by_slug(slug: str) -> dict | None:
    init_db()
    normalized = _slugify(slug.strip())
    if not normalized:
        return None
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM organizations WHERE slug = ?", (normalized,)
        ).fetchone()
        return _row_to_org(row) if row else None


def update_organization(org_id: str, *, name: str | None = None, status: str | None = None) -> dict | None:
    init_db()
    now = _utc_now()
    fields: list[str] = []
    values: list[Any] = []
    if name is not None:
        fields.append("name = ?")
        values.append(name.strip())
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if not fields:
        return get_organization(org_id)
    fields.append("updated_at = ?")
    values.append(now)
    values.append(org_id)
    with _conn() as conn:
        conn.execute(
            f"UPDATE organizations SET {', '.join(fields)} WHERE id = ?",
            values,
        )
    return get_organization(org_id)


def list_org_members(org_id: str) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.email, u.display_name, u.is_superadmin, m.role, m.created_at AS joined_at
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            WHERE m.org_id = ?
            ORDER BY m.role, u.email
            """,
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_org_member(
    *,
    org_id: str,
    email: str,
    password: str | None = None,
    role: str = "member",
    display_name: str = "",
) -> dict:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    init_db()
    user = get_user_by_email(email)
    if not user:
        if not password:
            raise ValueError("Password required for new users")
        user = create_user(email=email, password=password, display_name=display_name)
    existing = get_membership(user["id"], org_id)
    if existing:
        raise ValueError("User is already a member of this organisation")
    now = _utc_now()
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO memberships (id, user_id, org_id, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), user["id"], org_id, role, now, now),
        )
    return {"user": user, "role": role, "org_id": org_id}


def update_member_role(org_id: str, user_id: str, role: str) -> bool:
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    init_db()
    now = _utc_now()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE memberships SET role = ?, updated_at = ? WHERE org_id = ? AND user_id = ?",
            (role, now, org_id, user_id),
        )
        return cur.rowcount > 0


def remove_org_member(org_id: str, user_id: str) -> bool:
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM memberships WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        )
        return cur.rowcount > 0


def register_user_with_org(
    *,
    email: str,
    password: str,
    display_name: str = "",
    org_name: str | None = None,
) -> tuple[dict, dict]:
    """Self-serve signup: new user + new org (or join default if org_name omitted)."""
    init_db()
    user = create_user(email=email, password=password, display_name=display_name)
    if org_name:
        org = create_organization(name=org_name, created_by_user_id=user["id"])
    else:
        now = _utc_now()
        with _conn() as conn:
            conn.execute(
                """
                INSERT INTO memberships (id, user_id, org_id, role, created_at, updated_at)
                VALUES (?, ?, ?, 'member', ?, ?)
                """,
                (str(uuid.uuid4()), user["id"], DEFAULT_ORG_ID, now, now),
            )
        org = get_organization(DEFAULT_ORG_ID) or {}
    return user, org