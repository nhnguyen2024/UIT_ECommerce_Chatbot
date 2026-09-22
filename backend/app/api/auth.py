"""Sign-in for the staff pages (Operations and Insights).

One shared admin password, set as ADMIN_PASSWORD (an Azure Container Apps
secret in production). Signing in returns a short-lived session token that the
browser sends as `Authorization: Bearer <token>` on every /api/admin request.

The token is `<expiry>.<signature>`, where the signature is an HMAC-SHA256 of
the expiry under a key derived from the password. Nothing is stored server
side, so scaling to zero or to two replicas does not log anyone out, and
changing the password invalidates every token already issued.

Deliberately small: a demo store has one staff role. Per-user accounts and
roles belong with a real identity provider (Microsoft Entra ID), not here.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings

router = APIRouter(prefix="/api/admin", tags=["admin-auth"])

# A wrong password costs the caller this long, which makes guessing slow
# without making a legitimate typo annoying.
FAILED_LOGIN_DELAY_S = 1.0


def _key(password: str) -> bytes:
    return hashlib.sha256(f"northlight-admin-session:{password}".encode()).digest()


def issue_token(password: str, lifetime_s: int, now: float | None = None) -> tuple[str, int]:
    expires = int((now or time.time()) + lifetime_s)
    signature = hmac.new(_key(password), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{signature}", expires


def token_is_valid(token: str, password: str, now: float | None = None) -> bool:
    expires, _, signature = token.partition(".")
    if not expires.isdigit() or not signature:
        return False
    expected = hmac.new(_key(password), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected) and int(expires) > (now or time.time())


def _configured_password() -> str:
    secret = get_settings().admin_password
    if secret is None or not secret.get_secret_value():
        # Fail closed: without a password the staff pages stay locked.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Admin sign-in is not configured")
    return secret.get_secret_value()


async def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Dependency on every /api/admin route except /login."""
    password = _configured_password()
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token_is_valid(token.strip(), password):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Sign in required", headers={"WWW-Authenticate": "Bearer"}
        )


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginRequest) -> dict:
    password = _configured_password()
    if not hmac.compare_digest(body.password.encode(), password.encode()):
        await asyncio.sleep(FAILED_LOGIN_DELAY_S)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password")
    token, expires = issue_token(password, get_settings().admin_session_hours * 3600)
    return {"token": token, "expires_at": expires}
