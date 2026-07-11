"""Test bootstrap: put src/ on the path and point the app DB at a throwaway file so
tests never touch a real casewright.db."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ["CASEWRIGHT_DB_PATH"] = str(Path(tempfile.gettempdir()) / f"cw_test_{uuid.uuid4().hex}.db")
os.environ.setdefault("AUTOTEST_DATA_DIR", str(ROOT / "data" / "chroma"))
# Tests run in dev mode so the fail-closed secret check (issue #10) doesn't abort
# importing web.main; the check itself is exercised directly in test_config_secrets.
os.environ.setdefault("CASEWRIGHT_DEV", "1")
