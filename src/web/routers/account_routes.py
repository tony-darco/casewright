"""Account settings: amend profile (name / username / email) and change password.

All routes are user-scoped and only ever touch the current user's row. Changing the
password bumps token_version (invalidating other sessions) and re-issues this
session's cookie so the current browser stays signed in.
"""

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from web import auth, db
from web.auth import require_user
from web.deps import templates

router = APIRouter()


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.rsplit("@", 1)[-1] and " " not in email


def _profile_response(request, user_id, msg=None, error=None):
    return templates.TemplateResponse(
        request, "partials/account_profile.html",
        {"account": db.get_user_by_id(user_id), "profile_msg": msg, "profile_error": error},
    )


def _password_response(request, msg=None, error=None):
    return templates.TemplateResponse(
        request, "partials/account_password.html", {"pw_msg": msg, "pw_error": error}
    )


@router.post("/account/profile", response_class=HTMLResponse)
def update_profile(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    username: str = Form(""),
    email: str = Form(""),
    user: dict = Depends(require_user),
):
    first_name, last_name = first_name.strip(), last_name.strip()
    username, email = username.strip(), email.strip()
    error = None
    if not first_name or not last_name:
        error = "Enter your first and last name."
    elif not username:
        error = "Enter a username."
    elif not _valid_email(email):
        error = "Enter a valid email address."
    else:
        clash_u = db.get_user_by_username(username)
        clash_e = db.get_user_by_email(email)
        if clash_u and clash_u["id"] != user["id"]:
            error = "That username is already taken."
        elif clash_e and clash_e["id"] != user["id"]:
            error = "That email is already in use."
    if error:
        return _profile_response(request, user["id"], error=error)
    try:
        db.update_profile(user["id"], first_name, last_name, username, email)
    except sqlite3.IntegrityError:
        return _profile_response(request, user["id"], error="That username or email is already in use.")
    return _profile_response(request, user["id"], msg="Profile updated.")


@router.post("/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    new_password2: str = Form(""),
    user: dict = Depends(require_user),
):
    if not auth.verify_password(current_password, db.get_password_hash(user["id"])):
        return _password_response(request, error="Current password is incorrect.")
    if len(new_password) < 8:
        return _password_response(request, error="New password must be at least 8 characters.")
    if len(new_password.encode("utf-8")) > 72:
        return _password_response(request, error="New password is too long (max 72 characters).")
    if new_password != new_password2:
        return _password_response(request, error="New passwords don't match.")
    new_ver = db.update_password(user["id"], auth.hash_password(new_password))
    resp = _password_response(request, msg="Password changed. Other sessions have been signed out.")
    # keep THIS session valid by re-issuing the cookie at the new token_version
    auth.set_session_cookie(resp, auth.create_access_token(user["id"], user["username"], new_ver))
    return resp
