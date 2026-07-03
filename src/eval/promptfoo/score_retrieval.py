"""
Promptfoo custom python assertion: set precision/recall/F1 of retrieved vs
expected endpoints. Read-only, deterministic. Entry point: get_assert.

Contract (promptfoo python assertion):
    get_assert(output, context) -> dict (GradingResult-shaped)
  context["vars"]["expected_endpoints"] : list[str]  ground truth, "METHOD path"
  context["test"]["metadata"]["control"]: bool        True for with_endpoint_control cell
  context["config"]                     : dict        assertion config (e.g. threshold override)
"""

import json
import re

# Default pass/fail rule for non-control (multi-hop/one-hop) cells: every
# expected endpoint must be retrieved. Override per-run via the assertion's
# `config: {recall_threshold_for_pass: <float>}` in promptfooconfig.yaml.
DEFAULT_RECALL_THRESHOLD_FOR_PASS = 1.0

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
    expected = set(context["vars"]["expected_endpoints"])
    retrieved = parse_retrieved(output)
    metadata = context.get("test", {}).get("metadata", {}) or {}
    is_control = bool(metadata.get("control", False))

    true_positives = retrieved & expected

    precision = (len(true_positives) / len(retrieved)) if retrieved else 0.0
    recall = (len(true_positives) / len(expected)) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    config = context.get("config", {}) or {}

    if is_control:
        score = precision
        passed = precision == 1.0
        reason = (f"control cell: precision={precision:.3f} "
                  f"(retrieved {len(retrieved)}, {len(retrieved - expected)} extraneous); "
                  f"pass iff precision == 1.0")
    else:
        threshold = config.get("recall_threshold_for_pass", DEFAULT_RECALL_THRESHOLD_FOR_PASS)
        score = f1
        passed = recall >= threshold
        reason = (f"precision={precision:.3f} recall={recall:.3f} f1={f1:.3f} "
                  f"(expected {len(expected)}, retrieved {len(retrieved)}, "
                  f"matched {len(true_positives)}); pass iff recall >= {threshold}")

    return {
        "pass": passed,
        "score": score,
        "reason": reason,
        "namedScores": {"precision": precision, "recall": recall, "f1": f1},
        "componentResults": [
            {"pass": True, "score": precision, "reason": f"precision={precision:.3f}"},
            {"pass": True, "score": recall, "reason": f"recall={recall:.3f}"},
            {"pass": True, "score": f1, "reason": f"f1={f1:.3f}"},
        ],
    }
