"""Promptfoo Python provider for the AutoTestLLM retrieval pipeline.

promptfoo calls ``call_api(prompt, options, context)``; ``prompt`` is the
rendered question (expected_endpoints never reach here). We run the pipeline and
return, as a list of "METHOD path" strings, the union of the retrieved target
endpoints and the upstream prerequisites the dependency graph adds — because the
ground truth is target + closure, and the whole context (targets + call-order
producers) is what reaches the generation LLM. The two stay separate in pipeline
state; we combine them only here, at the eval boundary. This is exactly what
score_retrieval.parse_retrieved consumes, so no scorer changes are needed.

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
    endpoints = state.get("endpoints", []) or []
    deps = state.get("dependency_endpoints", []) or []
    # Retrieved targets first, then prerequisite producers the graph adds (deduped).
    combined = endpoints + [d for d in deps if d not in endpoints]
    return {"output": combined}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "List the organizations"
    print(call_api(q, {}, {}))
