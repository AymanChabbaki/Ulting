"""
Login and session handling.

This dashboard reads ad accounts through a token carrying ads_management --
write access. So every data route requires a session, and login fails closed:
if DASHBOARD_USER / DASHBOARD_PASSWORD / SESSION_SECRET are unset the endpoint
returns 503 rather than defaulting to open.

The session is a JWT in an HttpOnly cookie, so the browser never holds the Meta
token and page JS cannot read the session either.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Cookie, HTTPException, Response, status

from .config import get_settings

COOKIE_NAME = "ultex_session"
ALGORITHM = "HS256"


def verify_credentials(username: str, password: str) -> bool:
    """Check against the configured pair.

    DASHBOARD_PASSWORD may be a bcrypt hash (preferred) or plain text for a
    local run. Both halves are always evaluated -- returning early on a bad
    username would leak which half was wrong through response timing.
    """
    settings = get_settings()
    if not settings.dashboard_user or not settings.dashboard_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is not configured. Set DASHBOARD_USER and DASHBOARD_PASSWORD "
                   "in .env.local, then restart the API.",
        )

    user_ok = hmac.compare_digest(username.strip(), settings.dashboard_user.strip())

    stored = settings.dashboard_password
    if stored.startswith("$2"):  # bcrypt hash
        try:
            pass_ok = bcrypt.checkpw(password.encode(), stored.encode())
        except ValueError:
            pass_ok = False
    else:
        pass_ok = hmac.compare_digest(password, stored)

    return user_ok and pass_ok


def hash_password(plain: str) -> str:
    """Helper for generating a DASHBOARD_PASSWORD hash; see README."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def create_session_token(username: str) -> str:
    settings = get_settings()
    if not settings.session_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SESSION_SECRET is not set. Generate one with: "
                   'python -c "import secrets; print(secrets.token_hex(32))"',
        )
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    return jwt.encode(
        {"sub": username, "exp": expires}, settings.session_secret, algorithm=ALGORITHM
    )


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.session_hours * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def require_session(ultex_session: str | None = Cookie(default=None)) -> str:
    """FastAPI dependency: every data route depends on this."""
    settings = get_settings()
    if not ultex_session or not settings.session_secret:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(ultex_session, settings.session_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as cause:
        raise HTTPException(status_code=401, detail="Session expired") from cause
    return payload.get("sub", "")
