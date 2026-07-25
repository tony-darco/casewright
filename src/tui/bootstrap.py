"""App startup: prepare the datastore and resolve the local user.

Mirrors what the web ``main.py`` did at import time — create the SQLite schema and
install the app-wide log ring buffer (surfaced in Settings → Logs) — minus anything
tied to JWT sessions or serving. The at-rest encryption key for the stored Meraki
key falls back to a local file when unset (web.services.crypto), which is the right
default for a local single-user tool, so we don't fail closed here.
"""

from web import db
from web.services import logs_store
from tui.identity import local_user


def startup() -> dict:
    """Initialise storage and return the local user. Idempotent."""
    db.init()
    logs_store.install_app_log()
    return local_user()
