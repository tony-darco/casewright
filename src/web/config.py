"""Single source of truth for the app's swappable bits.

The product name is a placeholder (handoff §1) — keep the wordmark here so
renaming is a one-line change, never a find-and-replace across the UI.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

WEB_DIR = Path(__file__).resolve().parent

# Load the repo-root .env so startup sees settings like MERAKI_API_KEY. Anchored on
# the repo root, not CWD; override=False so a real exported env var wins and a
# missing .env is a no-op. (rag also loads it lazily.)
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


# --- runtime mode ----------------------------------------------------------------
# Development mode. When set, an insecure-but-convenient fallback is tolerated: the
# at-rest encryption key (for the stored Meraki API key) may be auto-generated to a
# local file instead of being supplied explicitly. It MUST be unset in production,
# where the app fails closed at startup on a missing key rather than inventing one.
DEV_MODE = os.environ.get("CASEWRIGHT_DEV", "").strip().lower() in ("1", "true", "yes", "on")

# SQLite database (users + per-user data). In Docker, point this at a mounted
# volume, e.g. CASEWRIGHT_DB_PATH=/data/casewright.db.
DB_PATH = Path(os.environ.get("CASEWRIGHT_DB_PATH", str(_config_dir() / "casewright.db"))).expanduser()


def validate_startup_secrets() -> None:
    """Fail closed on an insecure at-rest key.

    Outside DEV_MODE, refuse to start if CASEWRIGHT_ENC_KEY is unset — otherwise the
    at-rest key for the Meraki API key falls back to an auto-generated local file
    (web.services.crypto), which is convenient for local use but wrong for a shared
    deployment. In DEV_MODE the fallback is tolerated. Raise ``RuntimeError`` so
    startup aborts rather than silently encrypting with an ephemeral key.
    """
    if DEV_MODE:
        return
    if not os.environ.get("CASEWRIGHT_ENC_KEY", "").strip():
        raise RuntimeError(
            "Refusing to start — CASEWRIGHT_ENC_KEY is unset. Set it in the environment, "
            "or set CASEWRIGHT_DEV=1 for local development."
        )
