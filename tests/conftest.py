"""Test bootstrap: put src/ on the path, point the app DB at a throwaway file so
tests never touch a real casewright.db, and empty that DB between tests."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ["CASEWRIGHT_DB_PATH"] = str(Path(tempfile.gettempdir()) / f"cw_test_{uuid.uuid4().hex}.db")
# Point the config dir at a throwaway too: in dev mode web.config generates a
# jwt.key (and crypto an enc.key) on import, and tests must not touch ~/.config.
os.environ["XDG_CONFIG_HOME"] = str(Path(tempfile.gettempdir()) / f"cw_cfg_{uuid.uuid4().hex}")
os.environ.setdefault("AUTOTEST_DATA_DIR", str(ROOT / "data" / "chroma"))
# Tests run in dev mode so the fail-closed secret check doesn't abort startup; the
# check itself is exercised directly in test_config_secrets.
os.environ.setdefault("CASEWRIGHT_DEV", "1")


@pytest.fixture(autouse=True)
def empty_db():
    """Give every test an empty database.

    Tests used to isolate themselves by creating their own user and letting the
    per-user scoping in every query keep them apart. There are no users any more, so
    isolation comes from the store instead: emptying it between tests is what stops
    one test's rows from satisfying another's assertions (several assert on exact
    version lists and counts)."""
    from web import db

    db.init()
    with db.cursor() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
        # reset AUTOINCREMENT so ids restart at 1 (the table only exists once a row has)
        conn.execute("DELETE FROM sqlite_sequence WHERE 1")
    yield
