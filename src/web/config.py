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


# SQLite database — records only (tests, runs, knowledge-base versions, the cached
# Meraki org tree). Settings are not in here; they're in config.yaml and .env
# (web.settings). In Docker, point this at a mounted volume, e.g.
# CASEWRIGHT_DB_PATH=/data/casewright.db.
DB_PATH = Path(os.environ.get("CASEWRIGHT_DB_PATH", str(_config_dir() / "casewright.db"))).expanduser()
