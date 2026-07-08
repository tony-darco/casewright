"""autotest RAG package.

Single source of truth for filesystem locations, so modules don't each recompute
the repo root from ``__file__`` (previously done three different ways, at three
directory depths, in pathlib and os.path). Everything is anchored on this file:
``src/rag/__init__.py`` -> ``parents[2]`` is the repo root.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
CHROMA_DIR = DATA_DIR / "chroma"
GRAPH_JSON = DATA_DIR / "graph" / "graph.json"
SPECS_DIR = DATA_DIR / "specs"
