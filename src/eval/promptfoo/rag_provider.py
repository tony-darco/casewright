"""Promptfoo Python provider for the AutoTestLLM retrieval pipeline.

promptfoo calls ``call_api(prompt, options, context)``; ``prompt`` is the
rendered question (expected_endpoints never reach here). We run the pipeline and
return the retrieved endpoint ids as a list of "METHOD path" strings — exactly
what score_retrieval.parse_retrieved consumes, so no scorer changes are needed.

The pipeline reads the Chroma store at the repo-relative path ``data/chroma``, so
we resolve and chdir to the repo root before building it (promptfoo runs this
file from wherever promptfooconfig.yaml lives).
"""

import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SRC = os.path.join(_REPO_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        os.chdir(_REPO_ROOT)  # make data/chroma resolve regardless of promptfoo's CWD
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
