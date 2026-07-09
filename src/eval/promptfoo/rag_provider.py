"""Promptfoo Python provider for the AutoTestLLM retrieval pipeline.

promptfoo calls ``call_api(prompt, options, context)``; ``prompt`` is the
rendered question (expected_endpoints never reach here). We run the pipeline and
return the split as a structured object — ``{"endpoints": [...], "dependency_endpoints":
[...]}`` — where ``endpoints`` are the retrieved targets (from the grader) and
``dependency_endpoints`` are the upstream prerequisites the dependency graph adds.
score_retrieval scores their union (the ground truth is target + closure) but keeps
them apart to label each endpoint's provenance ([grader] vs [dep]) in its reason.

Bootstraps ``src/`` onto sys.path so ``rag`` imports; the pipeline then resolves
the Chroma store and dependency-graph paths via rag/__init__.py (anchored on the
package location, not the process CWD), so no chdir is needed even though promptfoo
runs this file from wherever promptfooconfig.yaml lives.
"""

import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]  # src/eval/promptfoo/rag_provider.py -> src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        os.environ.setdefault("AUTOTEST_EVAL_MODE", "1")  # skip generation; eval only scores retrieval
        from rag.pipeline import AutoTestLLM
        _pipeline = AutoTestLLM()
    return _pipeline


def call_api(prompt, options, context):
    question = prompt if isinstance(prompt, str) else str(prompt)
    state = _get_pipeline().run(question)
    # Structured split so the scorer can score the union AND label provenance.
    return {"output": {
        "endpoints": state.get("endpoints", []) or [],
        "dependency_endpoints": state.get("dependency_endpoints", []) or [],
    }}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "List the organizations"
    print(call_api(q, {}, {}))
