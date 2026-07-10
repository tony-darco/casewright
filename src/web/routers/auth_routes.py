"""Registration, login, and logout (JWT in an httpOnly cookie)."""

import sqlite3

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web import auth, db
from web.deps import templates

router = APIRouter()


def _redirect_with_session(user: dict, to: str = "/app"):
    resp = RedirectResponse(to, status_code=303)
    token = auth.create_access_token(user["id"], user["username"], user.get("token_version", 0))
    auth.set_session_cookie(resp, token)
    return resp


def _valid_email(email: str) -> bool:
    return "@" in email and "." in email.rsplit("@", 1)[-1] and " " not in email


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if auth.get_current_user(request):
        return RedirectResponse("/app", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, username: str = Form(""), password: str = Form("")):
    user = db.get_user_by_username(username.strip())
    # Always run one bcrypt comparison so timing doesn't reveal whether the user
    # exists (username enumeration). Same generic error either way.
    if not user:
        auth.dummy_verify(password)
        ok = False
    else:
        ok = auth.verify_password(password, user["password_hash"])
    if not ok:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Incorrect username or password.", "username": username.strip()},
            status_code=401,
        )
    return _redirect_with_session(user)


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if auth.get_current_user(request):
        return RedirectResponse("/app", status_code=303)
    return templates.TemplateResponse(request, "signup.html", {})


@router.post("/signup", response_class=HTMLResponse)
def signup(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    username: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
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
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif len(password.encode("utf-8")) > 72:
        error = "Password is too long (max 72 characters)."
    elif password != password2:
        error = "Passwords don't match."
    elif db.get_user_by_username(username):
        error = "That username is already taken."
    elif db.get_user_by_email(email):
        error = "An account with that email already exists."
    if not error:
        try:
            user = db.create_user(username, auth.hash_password(password), first_name, last_name, email)
            return _redirect_with_session(user)
        except sqlite3.IntegrityError:  # race: username/email taken between check and insert
            error = "That username or email is already taken."
    return templates.TemplateResponse(
        request, "signup.html",
        {"error": error, "first_name": first_name, "last_name": last_name,
         "username": username, "email": email},
        status_code=400,
    )


@router.post("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    auth.clear_session_cookie(resp)
    return resp
