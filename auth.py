"""JWT authentication middleware for dashboard API endpoints."""
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from secrets_manager import get_secret
from tenancy import AuthContext, DEFAULT_ORG_ID, set_request_org

bearer = HTTPBearer(auto_error=False)
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
API_SETTINGS_TOKEN_MINUTES = 60

AUTH_COOKIE_NAME = "speaksy_auth"


def is_auth_enabled() -> bool:
    return not _is_auth_disabled()


def _is_auth_disabled() -> bool:
    return os.environ.get("DISABLE_AUTH", "").lower() == "true"


def _jwt_secret() -> str:
    return get_secret("jwt_secret") or "dev-secret-change-in-prod"


def create_access_token(
    subject: str,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    role: str = "member",
    is_superadmin: bool = False,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if user_id:
        payload["uid"] = user_id
    if org_id:
        payload["oid"] = org_id
    if role:
        payload["role"] = role
    if is_superadmin:
        payload["sa"] = True
    return jwt.encode(payload, _jwt_secret(), algorithm=ALGORITHM)


def decode_token_payload(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc


def payload_to_auth_context(payload: dict[str, Any]) -> AuthContext:
    subject = payload.get("sub") or ""
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid token")
    return AuthContext(
        user_id=payload.get("uid") or subject,
        email=subject,
        org_id=payload.get("oid"),
        role=payload.get("role") or "member",
        is_superadmin=bool(payload.get("sa")),
    )


def _dev_auth_context() -> AuthContext:
    return AuthContext(
        user_id="dev-user",
        email="dev@local.speaksy",
        org_id=DEFAULT_ORG_ID,
        role="owner",
        is_superadmin=True,
    )


def _token_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if credentials and credentials.credentials:
        return credentials.credentials
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "").strip()
    if cookie_token:
        return cookie_token
    return None


def get_auth_context_from_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = None,
) -> AuthContext:
    token = _token_from_request(request, credentials)
    if token:
        try:
            ctx = payload_to_auth_context(decode_token_payload(token))
            set_request_org(ctx.org_id)
            return ctx
        except HTTPException:
            if not _is_auth_disabled():
                raise
    if _is_auth_disabled():
        ctx = _dev_auth_context()
        set_request_org(ctx.org_id)
        return ctx
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def require_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> AuthContext:
    return get_auth_context_from_request(request, credentials)


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    """Backward-compatible: returns email/username string."""
    return get_auth_context_from_request(request, credentials).email


def _decode_access_token(token: str) -> str:
    return payload_to_auth_context(decode_token_payload(token)).email


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> str:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return _decode_access_token(credentials.credentials)


def require_auth_media(request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    """Auth for browser media tags — accepts Bearer header or ?token= JWT query param."""
    if _is_auth_disabled():
        set_request_org(DEFAULT_ORG_ID)
        return "dev-user"
    if credentials:
        ctx = payload_to_auth_context(decode_token_payload(credentials.credentials))
        set_request_org(ctx.org_id)
        return ctx.email
    token = request.query_params.get("token", "").strip()
    if token:
        ctx = payload_to_auth_context(decode_token_payload(token))
        set_request_org(ctx.org_id)
        return ctx.email
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def create_api_settings_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=API_SETTINGS_TOKEN_MINUTES)
    return jwt.encode(
        {"sub": username, "scope": "api_settings", "exp": expire},
        _jwt_secret(),
        algorithm=ALGORITHM,
    )


def verify_api_settings_token(token: str | None) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="API settings access required")
    try:
        payload = jwt.decode(token, _jwt_secret(), algorithms=[ALGORITHM])
        if payload.get("scope") != "api_settings":
            raise HTTPException(status_code=403, detail="Invalid API settings token")
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid API settings token")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="API settings session expired")


def require_api_settings_access(
    request: Request,
    user: str = Depends(require_auth),
) -> str:
    token = request.headers.get("X-API-Settings-Token", "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unlock API settings with panel password")
    verify_api_settings_token(token)
    return user


