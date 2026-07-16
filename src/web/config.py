"""Single source of truth for the web layer's swappable bits.

The product name is a placeholder (handoff §1) — keep the wordmark here so
renaming is a one-line change, never a find-and-replace across templates.
"""

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Load the repo-root .env so web-only startup (no rag import) still sees settings
# like MERAKI_API_KEY. Anchored on the repo root, not CWD; override=False so a real
# exported env var wins and a missing .env is a no-op. (rag also loads it lazily.)
load_dotenv(WEB_DIR.parents[1] / ".env")

# "casewright" is a placeholder name — change it here only (handoff §1, mockups
# README).
WORDMARK = "casewright"

# Meraki Dashboard API base (handoff G10). The API *key* is a secret and is NOT
# read here — it's stored per-user, encrypted at rest (web.services.store +
# web.services.crypto), set via Settings and never returned to the browser.
MERAKI_BASE_URL = os.environ.get("MERAKI_BASE_URL", "https://api.meraki.com/api/v1").rstrip("/")

# The pinned Meraki OpenAPI snapshot, read by the coverage tree (web.services.coverage).
# Anchored on the repo root, not CWD — same idiom as the .env load above. Shares
# AUTOTEST_SPECS_DIR with rag.ingest.split so both resolve to one place.
SPECS_DIR = Path(
    os.environ.get("AUTOTEST_SPECS_DIR", str(WEB_DIR.parents[1] / "data" / "specs"))
).expanduser()
SPEC_PATH = SPECS_DIR / "meraki_open_api_spec.json"


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base).expanduser() / "casewright"


# --- auth / sessions -------------------------------------------------------------
# Development mode. When set, insecure-but-convenient fallbacks are tolerated (a
# source-controlled dev JWT secret, an auto-generated encryption key). It MUST be
# unset in production: with it unset the app fails closed at startup on a missing or
# sentinel secret rather than signing forgeable session cookies (issue #10).
DEV_MODE = os.environ.get("CASEWRIGHT_DEV", "").strip().lower() in ("1", "true", "yes", "on")

def _dev_jwt_secret() -> str:
    """A stable, machine-local JWT signing secret for development.

    Mirrors how the at-rest encryption key is handled (web.services.crypto): generate
    once, persist to a 0600 file outside the repo, reuse forever. Stable across
    restarts, so dev sessions survive a reload — but unlike a source-controlled
    sentinel, it isn't knowable by anyone who can read the repo.

    Only ever called in DEV_MODE. Production must supply JWT_SECRET explicitly: we
    deliberately do NOT auto-generate there, both so the secret can live in a real
    secret store and because a container inventing its own key would silently log
    every user out each time it was recreated. See validate_startup_secrets.
    """
    path = _config_dir() / "jwt.key"
    if path.exists():
        return path.read_text().strip()
    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret


# Signing secret for JWTs. MUST be set in production (env var / Docker secret); in
# DEV_MODE we generate and persist a real random one rather than shipping a known
# value. The sentinel is retained only so an explicitly-configured legacy value is
# still refused in production.
JWT_DEV_SENTINEL = "dev-insecure-change-me"
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip() or (_dev_jwt_secret() if DEV_MODE else "")
JWT_ALG = "HS256"
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 days
COOKIE_NAME = "cw_session"
# Secure flag off for local http dev; set COOKIE_SECURE=1 in production behind TLS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")

# SQLite database (users + per-user data). In Docker, point this at a mounted
# volume, e.g. CASEWRIGHT_DB_PATH=/data/casewright.db.
DB_PATH = Path(os.environ.get("CASEWRIGHT_DB_PATH", str(_config_dir() / "casewright.db"))).expanduser()


def validate_startup_secrets() -> None:
    """Fail closed on insecure secrets (issue #10).

    Outside DEV_MODE, refuse to start if the JWT signing secret is missing or the
    known dev sentinel (sessions would be forgeable), or if CASEWRIGHT_ENC_KEY is
    unset (the at-rest key for the Meraki API key would fall back to an
    auto-generated local file). In DEV_MODE these fallbacks are tolerated. Raise
    ``RuntimeError`` so startup aborts rather than serving with a forgeable key.
    """
    if DEV_MODE:
        return
    problems = []
    if not JWT_SECRET.strip() or JWT_SECRET == JWT_DEV_SENTINEL:
        problems.append("JWT_SECRET is unset or the insecure dev default")
    if not os.environ.get("CASEWRIGHT_ENC_KEY", "").strip():
        problems.append("CASEWRIGHT_ENC_KEY is unset")
    if problems:
        raise RuntimeError(
            "Refusing to start — " + "; ".join(problems) + ". Set real secrets in the "
            "environment (e.g. Docker secrets), or set CASEWRIGHT_DEV=1 for local development."
        )
