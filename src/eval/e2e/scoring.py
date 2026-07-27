"""Endpoint scoring — precision/recall/F1 of the endpoints the pipeline grounded on
versus the case's ground truth.

Mirrors the semantics of ``eval.promptfoo.score_retrieval`` (same ``METHOD path``
normalization, recall as the pass gate) but stands alone so the e2e package has no
cross-eval import. Recall is the gate: a case passes on endpoints iff every expected
endpoint was grounded; extra endpoints are tolerated noise the generator can filter, so
precision/F1 are reported for ranking but do not gate.
"""

# A case passes on endpoints iff recall >= this. Extras don't gate (they're precision noise).
RECALL_GATE = 1.0


def normalize(endpoint: str) -> str:
    """``"get /foo"`` -> ``"GET /foo"``. Method upper-cased, single space, path as-is."""
    method, _, path = endpoint.strip().partition(" ")
    return f"{method.upper()} {path.strip()}"


def device_markers(devices) -> list:
    """The string each declared device should leave in the generated code: its serial
    when one is given (pinned row → serial baked in), else its 1-based serial token
    ``{{DEVICE_SERIAL_N}}`` (type-only row → token survives to run time)."""
    out = []
    for i, d in enumerate(devices or [], start=1):
        out.append((d.get("serial") or "").strip() or "{{DEVICE_SERIAL_%d}}" % i)
    return out


def score_devices(code, markers, hardware) -> dict:
    """Did the app add the requested device(s) to the test? A case with no declared
    devices is not applicable (passes trivially). Otherwise every marker must appear in
    the code AND the pipeline must have pinned hardware for the run."""
    if not markers:
        return {"applicable": False, "passed": True, "missing": [], "hardware_pinned": None}
    missing = [m for m in markers if m not in (code or "")]
    hardware_pinned = bool(hardware)
    return {
        "applicable": True,
        "passed": not missing and hardware_pinned,
        "missing": missing,
        "hardware_pinned": hardware_pinned,
    }


def score_endpoints(retrieved, expected) -> dict:
    """Score a grounded set against ground truth. Returns precision/recall/F1, the pass
    gate on recall, and the missing/extra diffs (sorted, normalized)."""
    retrieved = {normalize(e) for e in retrieved}
    expected = {normalize(e) for e in expected}
    hits = retrieved & expected
    precision = len(hits) / len(retrieved) if retrieved else 0.0
    recall = len(hits) / len(expected) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "passed": recall >= RECALL_GATE,
        "expected": sorted(expected),
        "retrieved": sorted(retrieved),
        "missing": sorted(expected - retrieved),
        "extra": sorted(retrieved - expected),
    }
