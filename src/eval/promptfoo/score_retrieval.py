"""
Promptfoo custom python assertion: set precision/recall/F1 of retrieved vs
expected endpoints. Read-only, deterministic. Entry point: get_assert.

Contract (promptfoo python assertion):
    get_assert(output, context) -> dict (GradingResult-shaped)
  context["test"]["metadata"]["expected_endpoints"] : list[str]  ground truth, "METHOD path"
    (kept in metadata, not vars: a list-valued var would be expanded by promptfoo
     into one test case per element.)

Scores-only: always passes; retrieval quality is judged by the precision/recall/f1
metrics, not a pass rate.
"""

import json
import re

_METHOD_PATH_RE = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)", re.IGNORECASE)


def parse_retrieved(output):
    """Extract the retrieved set of 'METHOD path' strings from the RAG
    pipeline's output.

    THIS IS THE INTEGRATION SEAM. Adapt this function to however your
    pipeline actually returns retrieved endpoints. It currently handles,
    in order:
      1. output is already a list/tuple of strings (or dicts with
         'method'/'path' keys).
      2. output is a JSON string encoding either of the above.
      3. output is free text containing 'METHOD /path' substrings
         (newline-, comma-, or prose-delimited) -- extracted via regex.
    Every other shape (e.g. a custom envelope with a 'results' key, or
    endpoints keyed by operationId instead of path) needs a branch added
    here; do not guess at the real pipeline's format beyond what's below.
    """
    items = None

    if isinstance(output, (list, tuple)):
        items = output
    elif isinstance(output, dict):
        # Common envelope shapes: {"retrieved": [...]}, {"results": [...]}
        for key in ("retrieved", "results", "endpoints"):
            if key in output and isinstance(output[key], (list, tuple)):
                items = output[key]
                break

    if items is None and isinstance(output, str):
        text = output.strip()
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (list, tuple)):
            items = parsed
        elif isinstance(parsed, dict):
            for key in ("retrieved", "results", "endpoints"):
                if key in parsed and isinstance(parsed[key], (list, tuple)):
                    items = parsed[key]
                    break

    if items is not None:
        retrieved = set()
        for item in items:
            if isinstance(item, str):
                retrieved.add(_normalize(item))
            elif isinstance(item, dict):
                method = item.get("method", "")
                path = item.get("path", "")
                if method and path:
                    retrieved.add(_normalize(f"{method} {path}"))
        return retrieved

    # Fallback: regex-extract "METHOD /path" substrings from free text.
    text = output if isinstance(output, str) else json.dumps(output)
    return {_normalize(f"{m.group(1)} {m.group(2)}") for m in _METHOD_PATH_RE.finditer(text)}


def _normalize(endpoint_str):
    method, _, path = endpoint_str.strip().partition(" ")
    return f"{method.upper()} {path.strip()}"


def get_assert(output, context):
    metadata = context.get("test", {}).get("metadata", {}) or {}
    expected = set(metadata.get("expected_endpoints", []))
    retrieved = parse_retrieved(output)

    true_positives = retrieved & expected
    precision = (len(true_positives) / len(retrieved)) if retrieved else 0.0
    recall = (len(true_positives) / len(expected)) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    reason = (f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f} "
              f"(expected {len(expected)}, retrieved {len(retrieved)}, "
              f"matched {len(true_positives)})")

    # Scores-only: no binary pass/fail. Always "pass" so retrieval quality is read
    # from the aggregated precision/recall/f1 metrics, not a pass rate. Partial
    # retrieval is reflected proportionally (e.g. 1 of 2 expected -> recall 0.5).
    return {
        "pass": True,
        "score": f1,
        "reason": reason,
        "namedScores": {"precision": precision, "recall": recall, "f1": f1},
    }
