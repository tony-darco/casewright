"""autotest RAG package.

Single source of truth for filesystem locations, so modules don't each recompute
the repo root from ``__file__`` (previously done three different ways, at three
directory depths, in pathlib and os.path). Everything is anchored on this file:
``src/rag/__init__.py`` -> ``parents[2]`` is the repo root.
"""

from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

# Load the repo-root .env as soon as the package is imported, so every entry
# point (the `python -m rag.pipeline` CLI, the promptfoo provider subprocess,
# tests) sees AUTOTEST_* without anyone having to `source .env` first. Anchored
# on REPO_ROOT, not the CWD -- promptfoo runs the provider from another dir.
# override=False: a real exported env var always wins over the .env file, and a
# missing .env is a silent no-op (env vars set directly still work).
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"
GRAPH_JSON = DATA_DIR / "graph" / "graph.json"
SPECS_DIR = DATA_DIR / "specs"
