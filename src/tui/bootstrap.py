"""App startup: prepare the datastore.

Creates the SQLite schema, installs the app-wide log ring buffer (surfaced in
Settings -> Logs), and adopts an already-embedded Chroma collection as a knowledge
base on a fresh database. The at-rest encryption key for the stored Meraki key
falls back to a local file when unset (web.services.crypto), which is the right
default for a local single-user tool, so we don't fail closed here.
"""

from web import db
from web.services import kb_bootstrap, logs_store


def startup() -> None:
    """Initialise storage. Idempotent."""
    db.init()
    logs_store.install_app_log()
    kb_bootstrap.adopt_existing_collection()
