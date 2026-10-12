"""Organisation and client management API routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from auth import (
    AUTH_COOKIE_NAME,
    TOKEN_EXPIRE_HOURS,
    create_access_token,
    require_auth_context,
)
from org_portal import PORTAL_FEATURES, org_features, workspace_portal_path
from org_store import (
    ROLES,
    add_org_member,
    authenticate,
    create_org_workspace,
    create_organization,
    ensure_bootstrap,
    get_organization,
    get_organization_by_slug,
    get_user_by_email,
    list_org_members,
    list_org_workspaces,
    list_organizations,
    list_user_orgs,
    register_user_with_org,
    remove_org_member,
    resolve_auth_org,
    update_member_role,
    update_org_workspace_features,
    update_organization,
)
from secrets_manager import get_secret
from tenancy import AuthContext

logger = logging.getLogger("org_routes")

router = APIRouter(prefix="/api", tags=["organisations"])


class LoginBody(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str
    org_id: str | None = None
    org_code: str | None = None


class RegisterBody(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: str = ""
    org_name: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email address")
        return v


class SwitchOrgBody(BaseModel):
    org_id: str | None = None
    org_code: str | None = None


class CreateOrgBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class CreateWorkspaceBody(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    role_slug: str = Field(min_length=2, max_length=48)
    features: list[str] = Field(min_length=1)


class UpdateWorkspaceFeaturesBody(BaseModel):
    features: list[str] = Field(min_length=1)


class UpdateOrgBody(BaseModel):
    name: str | None = None
    status: str | None = None


class AddMemberBody(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Invalid email address")
        return v
    password: str | None = None
    role: str = "member"
    display_name: str = ""


class UpdateMemberRoleBody(BaseModel):
    role: str


def _login_identifier(body: LoginBody) -> str:
    raw = (body.email or body.username or "").strip()
    if not raw:
        raise HTTPException(status_code=401, detail="Email and password required")
    if "@" not in raw:
        return f"{raw}@local.speaksy"
    return raw.lower()


def _issue_token_for_user(user: dict, org_id: str | None = None) -> tuple[str, str, str]:
    resolved_org_id, role = resolve_auth_org(
        user["id"],
        org_id,
        is_superadmin=user.get("is_superadmin", False),
    )
    token = create_access_token(
        user["email"],
        user_id=user["id"],
        org_id=resolved_org_id,
        role=role,
        is_superadmin=user.get("is_superadmin", False),
    )
    return token, resolved_org_id, role


def _auth_me_payload(ctx: AuthContext) -> dict:
    orgs = list_user_orgs(ctx.user_id) if ctx.user_id and ctx.user_id != "dev-user" else []
    if ctx.is_superadmin:
        platform_orgs = list_organizations(include_all=True)
    else:
        platform_orgs = orgs
    current_org = get_organization(ctx.org_id) if ctx.org_id else None
    user = None
    if ctx.user_id and ctx.user_id != "dev-user":
        from org_store import get_user_by_id

        user = get_user_by_id(ctx.user_id)
    feats = org_features(current_org)
    if ctx.is_superadmin:
        from org_portal import FULL_VK_FEATURES

        feats = list(FULL_VK_FEATURES)
    return {
        "email": ctx.email,
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "role": ctx.role,
        "is_superadmin": ctx.is_superadmin,
        "display_name": (user or {}).get("display_name") or ctx.email.split("@")[0],
        "organizations": orgs,
        "current_organization": current_org,
        "can_manage_platform": ctx.can_manage_platform(),
        "can_manage_org": ctx.can_manage_org(),
        "platform_organizations": platform_orgs if ctx.is_superadmin else None,
        "features": feats,
        "portal_features": PORTAL_FEATURES,
    }


def _require_org_admin(ctx: AuthContext, org_id: str) -> None:
    if ctx.is_superadmin:
        return
    if ctx.org_id != org_id or not ctx.can_manage_org():
        raise HTTPException(status_code=403, detail="Organisation admin access required")


@router.post("/auth/register")
async def api_register(body: RegisterBody):
    ensure_bootstrap()
    try:
        user, org = register_user_with_org(
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            org_name=body.org_name,
        )
        token, org_id, role = _issue_token_for_user(user, org.get("id"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from fastapi.responses import JSONResponse
    import os

    response = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user["id"], "email": user["email"], "display_name": user.get("display_name")},
            "organization": org,
            "org_id": org_id,
            "role": role,
        }
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=os.environ.get("ENVIRONMENT", "development") == "production",
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@router.post("/auth/switch-org")
async def api_switch_org(body: SwitchOrgBody, ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    user = None
    from org_store import get_user_by_id

    user = get_user_by_id(ctx.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    org_id = body.org_id
    if body.org_code and not org_id:
        org = get_organization_by_slug(body.org_code)
        if not org:
            raise HTTPException(status_code=404, detail="Organisation not found")
        org_id = org["id"]
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id or org_code required")
    try:
        token, org_id, role = _issue_token_for_user(user, org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    from fastapi.responses import JSONResponse
    import os

    response = JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "org_id": org_id,
            "role": role,
            "organization": get_organization(org_id),
        }
    )
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=os.environ.get("ENVIRONMENT", "development") == "production",
        samesite="lax",
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        path="/",
    )
    return response


@router.get("/auth/me")
async def api_auth_me(ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    return _auth_me_payload(ctx)


@router.get("/organizations")
async def api_list_organizations(ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    if ctx.is_superadmin:
        orgs = list_organizations(include_all=True)
        for org in orgs:
            org["member_count"] = len(list_org_members(org["id"]))
        return {"organizations": orgs}
    return {"organizations": list_user_orgs(ctx.user_id)}


@router.get("/portal/features")
async def api_portal_features():
    return {"features": PORTAL_FEATURES}


def _can_access_org(ctx: AuthContext, org_id: str) -> bool:
    if ctx.is_superadmin:
        return True
    if ctx.org_id == org_id:
        return True
    return any(o["id"] == org_id for o in list_user_orgs(ctx.user_id))


@router.get("/organizations/{org_id}/workspaces")
async def api_list_workspaces(org_id: str, ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    if not _can_access_org(ctx, org_id):
        raise HTTPException(status_code=403, detail="Access denied")
    workspaces = list_org_workspaces(org_id)
    for ws in workspaces:
        ws["portal_path"] = workspace_portal_path(ws["slug"])
        ws["features"] = org_features(ws)
    return {"workspaces": workspaces}


@router.post("/organizations/{org_id}/workspaces")
async def api_create_workspace(
    org_id: str,
    body: CreateWorkspaceBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    _require_org_admin(ctx, org_id)
    invalid = [f for f in body.features if f not in PORTAL_FEATURES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown features: {', '.join(invalid)}")
    try:
        ws = create_org_workspace(
            parent_org_id=org_id,
            name=body.name,
            role_slug=body.role_slug,
            features=body.features,
            created_by_user_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ws["portal_path"] = workspace_portal_path(ws["slug"])
    ws["features"] = org_features(ws)
    logger.info("Workspace %s created under %s by %s", ws["slug"], org_id, ctx.email)
    return {"workspace": ws}


@router.patch("/organizations/{org_id}/workspaces/{workspace_id}")
async def api_update_workspace(
    org_id: str,
    workspace_id: str,
    body: UpdateWorkspaceFeaturesBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    _require_org_admin(ctx, org_id)
    ws = get_organization(workspace_id)
    if not ws or ws.get("parent_org_id") != org_id:
        raise HTTPException(status_code=404, detail="Workspace not found")
    try:
        updated = update_org_workspace_features(workspace_id, body.features)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated["portal_path"] = workspace_portal_path(updated["slug"])
    updated["features"] = org_features(updated)
    return {"workspace": updated}


@router.post("/organizations")
async def api_create_organization(body: CreateOrgBody, ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    if not ctx.is_superadmin:
        raise HTTPException(status_code=403, detail="Platform admin required to create clients")
    org = create_organization(name=body.name, created_by_user_id=ctx.user_id)
    logger.info("Organisation created: %s by %s", org["id"], ctx.email)
    return {"organization": org}


@router.get("/organizations/{org_id}")
async def api_get_organization(org_id: str, ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    org = get_organization(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    if not ctx.is_superadmin:
        memberships = list_user_orgs(ctx.user_id)
        if not any(o["id"] == org_id for o in memberships):
            raise HTTPException(status_code=403, detail="Access denied")
    members = list_org_members(org_id)
    return {"organization": org, "members": members}


@router.patch("/organizations/{org_id}")
async def api_update_organization(
    org_id: str,
    body: UpdateOrgBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    if not ctx.is_superadmin:
        _require_org_admin(ctx, org_id)
    org = update_organization(org_id, name=body.name, status=body.status)
    if not org:
        raise HTTPException(status_code=404, detail="Organisation not found")
    return {"organization": org}


@router.get("/organizations/{org_id}/members")
async def api_list_members(org_id: str, ctx: AuthContext = Depends(require_auth_context)):
    ensure_bootstrap()
    if not ctx.is_superadmin and ctx.org_id != org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {"members": list_org_members(org_id)}


@router.post("/organizations/{org_id}/members")
async def api_add_member(
    org_id: str,
    body: AddMemberBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    _require_org_admin(ctx, org_id)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {', '.join(sorted(ROLES))}")
    try:
        result = add_org_member(
            org_id=org_id,
            email=body.email,
            password=body.password,
            role=body.role,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.patch("/organizations/{org_id}/members/{user_id}")
async def api_update_member(
    org_id: str,
    user_id: str,
    body: UpdateMemberRoleBody,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    _require_org_admin(ctx, org_id)
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {', '.join(sorted(ROLES))}")
    if not update_member_role(org_id, user_id, body.role):
        raise HTTPException(status_code=404, detail="Member not found")
    return {"success": True, "role": body.role}


@router.delete("/organizations/{org_id}/members/{user_id}")
async def api_remove_member(
    org_id: str,
    user_id: str,
    ctx: AuthContext = Depends(require_auth_context),
):
    ensure_bootstrap()
    _require_org_admin(ctx, org_id)
    if user_id == ctx.user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    if not remove_org_member(org_id, user_id):
        raise HTTPException(status_code=404, detail="Member not found")
    return {"success": True}


def perform_login(body: LoginBody) -> dict:
    """Shared login logic for ui_server route."""
    ensure_bootstrap()
    identifier = _login_identifier(body)
    password = body.password

    user = authenticate(identifier, password)
    if not user:
        expected_user = (get_secret("dashboard_username") or "admin").strip()
        expected_pass = get_secret("dashboard_password") or "change-me-now"
        legacy_email = expected_user if "@" in expected_user else f"{expected_user}@local.speaksy"
        if identifier == legacy_email and password == expected_pass:
            user = get_user_by_email(legacy_email)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

    org_id = body.org_id
    if body.org_code and not org_id:
        org = get_organization_by_slug(body.org_code)
        if not org:
            raise HTTPException(status_code=401, detail="Invalid organisation code")
        org_id = org["id"]

    try:
        token, org_id, role = _issue_token_for_user(user, org_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    orgs = list_user_orgs(user["id"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "email": user["email"],
        "user_id": user["id"],
        "org_id": org_id,
        "role": role,
        "is_superadmin": user.get("is_superadmin", False),
        "organizations": orgs,
        "organization": get_organization(org_id),
    }