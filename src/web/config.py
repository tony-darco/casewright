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
# README). The small badge next to it flags that the name isn't final.
WORDMARK = "casewright"
WORDMARK_BADGE = "wip name"

# Meraki Dashboard API (handoff G10). The backend proxies these; the key is read
# from the environment. When unset, the proxy returns a clear "not configured"
# state so the Settings UI is still demoable (see services/meraki.py).
MERAKI_API_KEY = os.environ.get("MERAKI_API_KEY", "").strip()
MERAKI_BASE_URL = os.environ.get("MERAKI_BASE_URL", "https://api.meraki.com/api/v1").rstrip("/")
