"""App startup: prepare the datastore and config files, and resolve the local user.

Mirrors what the web ``main.py`` did at import time — create the SQLite schema and
install the app-wide log ring buffer (surfaced in Settings → Logs) — minus anything
tied to JWT sessions or serving.

Config files come next: settings used to live in per-user database rows, so on the
first run after that change we seed config.yaml from them (web.settings) rather than
from the defaults, and an existing install keeps its provider, containers, and API
key. A fresh install just gets the defaults written out.
"""

from web import db, settings
from web.services import logs_store
from tui.identity import local_user


def startup() -> dict:
    """Initialise storage and config, and return the local user. Idempotent."""
    db.init()
    user = local_user()
    if not settings.migrate_from_db(user["id"]):
        settings.ensure_config_file()
    logs_store.install_app_log()
    return user
