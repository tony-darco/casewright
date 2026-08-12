"""Test bootstrap: put src/ on the path, point the app DB and config files at
throwaways so tests never touch a real casewright.db, config.yaml or .env, and empty
that DB between tests."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ["CASEWRIGHT_DB_PATH"] = str(Path(tempfile.gettempdir()) / f"cw_test_{uuid.uuid4().hex}.db")
# Point the per-user config dir at a throwaway too, so nothing in a test run can read
# or write the developer's own ~/.config/casewright.
os.environ["XDG_CONFIG_HOME"] = str(Path(tempfile.gettempdir()) / f"cw_cfg_{uuid.uuid4().hex}")
os.environ.setdefault("AUTOTEST_DATA_DIR", str(ROOT / "data" / "chroma"))
# Every test that exercises the pipeline mocks it, so nothing here should reach a model
# backend. Point the provider at a closed port: a test that accidentally starts a real
# generation then fails fast instead of quietly succeeding against whatever .env points
# at — which is slow, non-deterministic, and burns someone's GPU to assert on a menu.
os.environ["AUTOTEST_OLLAMA_URL"] = "http://127.0.0.1:1"


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


@pytest.fixture(autouse=True)
def settings_files(tmp_path, monkeypatch):
    """Redirect config.yaml / .env into a per-test tmp dir.

    web.settings writes to real files at the repo root, so without this a test run
    would clobber the developer's own configuration. Autouse and function-scoped:
    every test gets a clean, empty config (i.e. the defaults), and MERAKI_API_KEY is
    cleared so a value exported in the shell can't leak into an assertion.
    """
    from web import settings

    monkeypatch.setattr(settings, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(settings, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv(settings.MERAKI_API_KEY, raising=False)
    return tmp_path
