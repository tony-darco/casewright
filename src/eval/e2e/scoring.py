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
