"""App startup: prepare the datastore and the config files.

Creates the SQLite schema, seeds config.yaml (from the pre-move settings tables on
the first run after settings left SQLite, from the defaults otherwise), installs the
app-wide log ring buffer (surfaced in Settings -> Logs), and adopts an
already-embedded Chroma collection as a knowledge base on a fresh database.
"""

from web import db, settings
from web.services import kb_bootstrap, logs_store


def startup() -> None:
    """Initialise storage and config. Idempotent."""
    db.init()
    if not settings.migrate_from_db():
        settings.ensure_config_file()
    logs_store.install_app_log()
    kb_bootstrap.adopt_existing_collection()
