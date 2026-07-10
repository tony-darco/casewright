"""Authentication: bcrypt password hashing + JWT stored in an httpOnly cookie.

The token lives in an httpOnly, SameSite=Lax cookie (config.COOKIE_NAME) so the
browser sends it automatically and JavaScript can't read it (XSS-safe). Route
protection is a FastAPI dependency (`require_user`); unauthenticated access raises
`AuthRedirect`, which main.py turns into a redirect to /login (or an HX-Redirect
for HTMX requests).
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Request

from web import config, db


class AuthRedirect(Exception):
    """Raised by require_user when there's no valid session."""


# --- passwords -------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A real bcrypt hash to compare against when a username doesn't exist, so login
# takes the same time whether or not the user is real (defeats timing-based
# username enumeration).
_DUMMY_HASH = bcrypt.hashpw(b"casewright-timing-equalizer", bcrypt.gensalt()).decode("utf-8")


def dummy_verify(password: str) -> None:
    """Constant-time no-op verify for the "user not found" path."""
    verify_password(password, _DUMMY_HASH)


# --- tokens ----------------------------------------------------------------------

def create_access_token(user_id: int, username: str, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "ver": token_version,  # bumped on password change -> old tokens stop validating
        "iat": now,
        "exp": now + timedelta(seconds=config.TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALG)


def decode_token(token: str):
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALG])
    except jwt.PyJWTError:
        return None


# --- request helpers -------------------------------------------------------------

def get_current_user(request: Request):
    """The logged-in user (dict with id, username) or None. Never raises."""
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        return None
    try:
        user = db.get_user_by_id(int(payload["sub"]))
    except (ValueError, TypeError):
        return None
    if not user:
        return None
    # Reject tokens issued before the last password change (session invalidation).
    if payload.get("ver", 0) != user.get("token_version", 0):
        return None
    request.state.user = user  # exposed to templates as request.state.user
    return user


def require_user(request: Request):
    """FastAPI dependency for protected routes. Redirects to /login if not authed."""
    user = get_current_user(request)
    if not user:
        raise AuthRedirect()
    return user


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=config.TOKEN_TTL_SECONDS,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(config.COOKIE_NAME, path="/")
