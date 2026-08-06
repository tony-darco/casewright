"""Test bootstrap: put src/ on the path and point the app DB at a throwaway file so
tests never touch a real casewright.db."""

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
