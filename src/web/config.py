"""Single source of truth for the web layer's swappable bits.

The product name is a placeholder (handoff §1) — keep the wordmark here so
renaming is a one-line change, never a find-and-replace across templates.
"""

import os
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


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base).expanduser() / "casewright"


# --- auth / sessions -------------------------------------------------------------
# Signing secret for JWTs. MUST be set in production (env var / Docker secret); the
# dev fallback is fine locally but invalidates on change.
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-insecure-change-me")
JWT_ALG = "HS256"
TOKEN_TTL_SECONDS = int(os.environ.get("TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 days
COOKIE_NAME = "cw_session"
# Secure flag off for local http dev; set COOKIE_SECURE=1 in production behind TLS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on")

# SQLite database (users + per-user data). In Docker, point this at a mounted
# volume, e.g. CASEWRIGHT_DB_PATH=/data/casewright.db.
DB_PATH = Path(os.environ.get("CASEWRIGHT_DB_PATH", str(_config_dir() / "casewright.db"))).expanduser()
