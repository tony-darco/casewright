"""Promptfoo Python provider for the AutoTestLLM retrieval pipeline.

promptfoo calls ``call_api(prompt, options, context)``; ``prompt`` is the
rendered question (expected_endpoints never reach here). We run the pipeline and
return the retrieved endpoint ids as a list of "METHOD path" strings — exactly
what score_retrieval.parse_retrieved consumes, so no scorer changes are needed.

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
    return {"output": state.get("endpoints", [])}


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "List the organizations"
    print(call_api(q, {}, {}))
