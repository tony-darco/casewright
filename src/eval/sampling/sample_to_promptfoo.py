#!/usr/bin/env python3
"""
Target sampler + promptfoo emitter (Stage 2 of 2).

Reads graph.json (produced by extract_graph.py) and emits a promptfoo eval
dataset: tests.yaml, score_retrieval.py, promptfooconfig.yaml. This tool
never parses the OpenAPI spec and never re-derives edges -- graph.json's
nodes/edges are trusted as the audited source of truth. All sampling is
seeded and deterministic.
"""

import argparse
import json
import os
import random
import sys
from collections import defaultdict

try:
    import yaml
except ImportError:
    yaml = None

GENERIC_PREFIX_SEGMENTS = {"networks", "organizations", "devices", "administered"}


# --------------------------------------------------------------------------
# Loading + canonical producer resolution
# --------------------------------------------------------------------------

def load_graph(path):
    with open(path) as f:
        return json.load(f)


def build_producer_of(nodes, edges):
    """(consumer, param) -> single canonical producer endpoint id.

    graph.json's edges are treated as a candidate pool (even though this
    extractor's own graph.json already narrows each (consumer, param) to one
    edge -- Stage 2 does not assume that and re-applies its OWN tie-break, so
    it stays correct against any graph.json shape): (1) prefer the lister
    (node['is_array_response'] is True), (2) shortest path (fewest
    segments), (3) lexicographically smallest 'METHOD path'.
    """
    candidates_by_cp = defaultdict(list)
    for e in edges:
        candidates_by_cp[(e["consumer"], e["param"])].append(e["producer"])

    def sort_key(producer_id):
        node = nodes[producer_id]
        is_lister_rank = 0 if node.get("is_array_response") else 1
        num_segments = len(node["path"].strip("/").split("/"))
        return (is_lister_rank, num_segments, producer_id)

    producer_of = {}
    tiebreak_log = []
    for cp, candidates in candidates_by_cp.items():
        ranked = sorted(set(candidates), key=sort_key)
        producer_of[cp] = ranked[0]
        if len(ranked) > 1:
            tiebreak_log.append({
                "consumer": cp[0], "param": cp[1],
                "chosen": ranked[0], "discarded": ranked[1:],
            })
    return producer_of, tiebreak_log


# --------------------------------------------------------------------------
# Canonical minimal-chain closure (root -> leaf order)
# --------------------------------------------------------------------------

def compute_closure(node_id, nodes, producer_of, memo, cycle_log):
    """Ordered (root -> leaf) list of endpoint ids for node_id's minimal chain.

    One canonical producer per param (via producer_of); stops at orphan
    roots. Memoized; guards against cycles (should not occur in a DAG).
    """
    if node_id in memo:
        return memo[node_id]

    order = []
    visited = set()
    stack_marker = set()

    def visit(nid):
        if nid in visited:
            return
        if nid in stack_marker:
            cycle_log.append(nid)
            return
        stack_marker.add(nid)
        node = nodes[nid]
        for param in sorted(node["params"], key=lambda p: p["name"]):
            if param["orphan"]:
                continue
            producer_id = producer_of.get((nid, param["name"]))
            if producer_id is None:
                continue
            visit(producer_id)
        stack_marker.discard(nid)
        visited.add(nid)
        order.append(nid)

    visit(node_id)
    memo[node_id] = order
    return order


# --------------------------------------------------------------------------
# Leaf pool construction
# --------------------------------------------------------------------------

def resource_area(path, target_tags):
    """Grouping key for stratified sampling: the first path segment past the
    generic scope prefix (networks/{id}, organizations/{id}, ...) and past
    the domain tag segment itself (wireless/camera), so sampling spreads
    across sub-resources (ssids, rfProfiles, roles, ...) not just the tag.
    """
    segments = [s for s in path.strip("/").split("/") if not s.startswith("{")]
    skip = GENERIC_PREFIX_SEGMENTS | set(target_tags)
    for seg in segments:
        if seg not in skip:
            return seg
    return segments[-1] if segments else path


def build_leaf_pool(nodes, producer_of, target_tags):
    """wireless/camera-tagged nodes -> closure info, bucketed by closure size."""
    memo = {}
    cycle_log = []
    leaves = {}
    for node_id, node in nodes.items():
        if not (set(node.get("tags", [])) & set(target_tags)):
            continue
        order = compute_closure(node_id, nodes, producer_of, memo, cycle_log)
        leaves[node_id] = {
            "closure": order,
            "size": len(order),
            "method": node["method"].upper(),
            "area": resource_area(node["path"], target_tags),
        }
    buckets = defaultdict(list)
    for node_id, info in leaves.items():
        bucket = info["size"] if info["size"] <= 3 else "4+"
        buckets[bucket].append(node_id)
    for bucket in buckets:
        buckets[bucket].sort()  # deterministic order before any RNG sampling
    return leaves, buckets, cycle_log


# --------------------------------------------------------------------------
# Stratified, deterministic sampling
# --------------------------------------------------------------------------

def stratified_sample(candidates, k, rng, leaves):
    """Pick up to k ids from candidates (already sorted), round-robining
    across (method, area) groups so selection spreads across methods and
    resource areas rather than draining one group first. Deterministic given
    the same rng state and candidate order.
    """
    groups = defaultdict(list)
    for cid in candidates:
        key = (leaves[cid]["method"], leaves[cid]["area"])
        groups[key].append(cid)
    group_keys = sorted(groups.keys())
    for key in group_keys:
        rng.shuffle(groups[key])

    picked = []
    idx = 0
    remaining = sum(len(v) for v in groups.values())
    while len(picked) < k and remaining > 0:
        key = group_keys[idx % len(group_keys)]
        if groups[key]:
            picked.append(groups[key].pop())
            remaining -= 1
        idx += 1
    return picked


# --------------------------------------------------------------------------
# Record assembly
# --------------------------------------------------------------------------

def build_target_detail(closure_ids, nodes, with_schema_excerpt):
    detail = []
    for nid in closure_ids:
        node = nodes[nid]
        entry = {
            "method": node["method"].upper(),
            "path": node["path"],
            "summary": node["summary"],
            "description": node["description"],
        }
        if with_schema_excerpt:
            # Trimmed to what graph.json exposes (field names, not full JSON
            # Schema types/enums) -- Stage 2 never re-parses the OpenAPI spec.
            entry["schema_excerpt"] = {
                "params": [{"name": p["name"], "orphan": p["orphan"]} for p in node["params"]],
                "is_array_response": node["is_array_response"],
                "response_fields": node["response_fields"],
            }
        detail.append(entry)
    return detail


def make_record(record_id, cell, closure_ids, nodes, control):
    return {
        "description": f"{record_id} | {cell}",
        "vars": {
            "question": "",
        },
        # expected_endpoints lives in metadata (NOT vars): promptfoo expands a
        # list-valued var into one test case per element, which would split a
        # single multi-endpoint record into many single-endpoint tests.
        "metadata": {
            "cell": cell,
            "record_id": record_id,
            "control": control,
            "expected_endpoints": list(closure_ids),
            "target_detail": build_target_detail(closure_ids, nodes, with_schema_excerpt=control),
        },
    }


# --------------------------------------------------------------------------
# Sampling plan
# --------------------------------------------------------------------------

CELL_PLAN = [
    ("one_hop_without", 120, 1),
    ("multihop_d2_without", 100, 2),
    ("multihop_d3_without", 50, 3),
    # control handled separately: mixed sizes, drawn last from remaining pool
]
CONTROL_CELL = "with_endpoint_control"
CONTROL_PLANNED = 30


def sample_all(leaves, buckets, seed):
    rng = random.Random(seed)
    used_leaves = set()
    used_target_sets = set()
    reuse_log = []
    cell_report = []
    records_by_cell = {}

    for cell, planned, size in CELL_PLAN:
        pool = [nid for nid in buckets.get(size, []) if nid not in used_leaves]
        available = len(pool)
        take = min(planned, available)
        picked = stratified_sample(pool, take, rng, leaves)
        chosen = []
        for nid in picked:
            target_set = tuple(leaves[nid]["closure"])
            if target_set in used_target_sets:
                reuse_log.append({"cell": cell, "leaf": nid, "reason": "target set already used"})
                continue
            used_target_sets.add(target_set)
            used_leaves.add(nid)
            chosen.append(nid)
        records_by_cell[cell] = chosen
        cell_report.append((cell, planned, available, len(chosen)))

    # Control: mixed one-hop + multi-hop shapes, smallest-size-first for
    # diversity, drawn from whatever wireless/camera leaves remain unused.
    remaining = [nid for nid in leaves if nid not in used_leaves]
    remaining.sort(key=lambda nid: (leaves[nid]["size"], leaves[nid]["method"], leaves[nid]["area"], nid))
    control_available = len(remaining)
    control_take = min(CONTROL_PLANNED, control_available)
    control_picked = stratified_sample(remaining, control_take, rng, leaves)
    # stratified_sample groups by (method, area) but we still want a
    # smallest-size-first bias overall, so re-sort the picked ids by size
    # while preserving determinism.
    control_picked.sort(key=lambda nid: (leaves[nid]["size"], nid))
    control_chosen = []
    for nid in control_picked:
        target_set = tuple(leaves[nid]["closure"])
        if target_set in used_target_sets:
            reuse_log.append({"cell": CONTROL_CELL, "leaf": nid, "reason": "target set already used"})
            continue
        used_target_sets.add(target_set)
        used_leaves.add(nid)
        control_chosen.append(nid)
    records_by_cell[CONTROL_CELL] = control_chosen
    cell_report.append((CONTROL_CELL, CONTROL_PLANNED, control_available, len(control_chosen)))

    return records_by_cell, cell_report, reuse_log, used_leaves


def assemble_records(records_by_cell, leaves, nodes):
    records = []
    next_id = 1
    cell_order = [c for c, _, _ in CELL_PLAN] + [CONTROL_CELL]
    id_ranges = {}
    for cell in cell_order:
        leaf_ids = records_by_cell.get(cell, [])
        start = next_id
        for leaf_id in leaf_ids:
            record_id = f"{next_id:03d}"
            closure = leaves[leaf_id]["closure"]
            records.append(make_record(record_id, cell, closure, nodes, control=(cell == CONTROL_CELL)))
            next_id += 1
        id_ranges[cell] = (start, next_id - 1) if leaf_ids else None
    return records, id_ranges


# --------------------------------------------------------------------------
# Self-check validation (before writing anything)
# --------------------------------------------------------------------------

def self_check(records, nodes, target_tags):
    problems = []

    if len(records) > 300:
        problems.append(f"emitted {len(records)} records, expected <= 300")

    ids = [r["metadata"]["record_id"] for r in records]
    if ids != sorted(ids):
        problems.append("record ids are not in sorted/grouped order")

    seen_target_sets = set()
    for r in records:
        expected = r["metadata"]["expected_endpoints"]
        for ep in expected:
            if ep not in nodes:
                problems.append(f"record {r['metadata']['record_id']}: '{ep}' not a node in graph.json")
        leaf = expected[-1] if expected else None
        if leaf is not None:
            leaf_tags = set(nodes[leaf].get("tags", []))
            if not (leaf_tags & set(target_tags)):
                problems.append(f"record {r['metadata']['record_id']}: leaf '{leaf}' not tagged {target_tags}")
        tset = tuple(expected)
        if tset in seen_target_sets:
            problems.append(f"record {r['metadata']['record_id']}: duplicate target set {tset}")
        seen_target_sets.add(tset)
        if r["vars"]["question"] != "":
            problems.append(f"record {r['metadata']['record_id']}: vars.question not empty")
        if set(r["vars"].keys()) != {"question"}:
            problems.append(f"record {r['metadata']['record_id']}: unexpected keys in vars: {r['vars'].keys()}")

    return problems


def consistency_check_graph(nodes, edges):
    """Cross-check graph.json's own internal consistency: a param is orphan
    iff no edge exists for it. Stage 2 trusts the graph but verifies it."""
    edge_pairs = {(e["consumer"], e["param"]) for e in edges}
    problems = []
    for node_id, node in nodes.items():
        for p in node["params"]:
            has_edge = (node_id, p["name"]) in edge_pairs
            if p["orphan"] and has_edge:
                problems.append(f"{node_id} param '{p['name']}' marked orphan but has an edge")
            if not p["orphan"] and not has_edge:
                problems.append(f"{node_id} param '{p['name']}' marked non-orphan but has no edge")
    return problems


# --------------------------------------------------------------------------
# Output writers
# --------------------------------------------------------------------------

class _Dumper(yaml.SafeDumper):
    """SafeDumper that force-quotes digit-only strings (e.g. record_id
    '001'/'109') so they round-trip as strings, not ints. PyYAML's default
    representer only quotes when its own implicit-resolver regex would
    misread the plain scalar -- which it does for '001' (-> int 1) and
    fails to do for '109' (-> int 109) or '059' (looks octal, left as str
    by luck). Left inconsistent, record_id types would vary across records.
    """


def _represent_str(dumper, data):
    style = "'" if data.isdigit() else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(str, _represent_str)


def write_tests_yaml(records, out_path):
    with open(out_path, "w") as f:
        yaml.dump(records, f, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=100)


SCORE_RETRIEVAL_TEMPLATE = '''\
"""
Promptfoo custom python assertion: set precision/recall/F1 of retrieved vs
expected endpoints. Read-only, deterministic. Entry point: get_assert.

Contract (promptfoo python assertion):
    get_assert(output, context) -> dict (GradingResult-shaped)
  context["test"]["metadata"]["expected_endpoints"] : list[str]  ground truth, "METHOD path"
    (kept in metadata, not vars: a list-valued var would be expanded by promptfoo
     into one test case per element.)

Pass/fail is gated on recall (RECALL_MIN): a record passes iff every expected endpoint
was retrieved. Extra endpoints are tolerated noise the generator can filter, so
precision/f1 are reported as metrics for ranking but do not gate.
"""

import json
import re

_METHOD_PATH_RE = re.compile(r"\\b(GET|POST|PUT|DELETE|PATCH)\\s+(/\\S+)", re.IGNORECASE)

# Pass gate: recall only. Every expected endpoint must be retrieved -- if the context
# contains everything the generator needs, the record passes. Extra endpoints are
# noise the generation model can filter, so precision/f1 are reported for ranking
# (and to improve the grader later) but do NOT gate. Tune here.
RECALL_MIN = 1.0


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


def _split(output):
    """Return (targets, deps) as sets of normalized 'METHOD path' ids.

    The pipeline provider returns a structured split
    {"endpoints": [...targets...], "dependency_endpoints": [...graph prereqs...]},
    which lets us label each endpoint's provenance. Any other shape (flat list, JSON
    string, free text) carries no split, so everything parsed is treated as a target.
    """
    obj = output
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            obj = output
    if isinstance(obj, dict) and ("endpoints" in obj or "dependency_endpoints" in obj):
        targets = {_normalize(x) for x in (obj.get("endpoints") or []) if isinstance(x, str)}
        deps = {_normalize(x) for x in (obj.get("dependency_endpoints") or []) if isinstance(x, str)}
        return targets, deps
    return parse_retrieved(output), set()


def get_assert(output, context):
    metadata = context.get("test", {}).get("metadata", {}) or {}
    expected = set(metadata.get("expected_endpoints", []))
    targets, deps = _split(output)
    retrieved = targets | deps

    true_positives = retrieved & expected
    precision = (len(true_positives) / len(retrieved)) if retrieved else 0.0
    recall = (len(true_positives) / len(expected)) if expected else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    passed = recall >= RECALL_MIN

    # Surface expected + diff in the reason (expected_endpoints is in metadata, which
    # the promptfoo table hides). missing -> recall gap; extra -> precision noise, each
    # tagged with provenance: [grader] retrieved target vs [dep] graph prerequisite.
    def _tag(e):
        if e in targets and e in deps:
            return f"{e} [grader+dep]"
        return f"{e} [dep]" if e in deps else f"{e} [grader]"

    missing = sorted(expected - retrieved)
    extra = sorted(retrieved - expected)
    reason = (
        f"{'PASS' if passed else 'FAIL'}  recall={recall:.2f} (gate >={RECALL_MIN})  "
        f"precision={precision:.2f}  f1={f1:.2f}\\n"
        f"  expected ({len(expected)}): {', '.join(sorted(expected)) or '-'}\\n"
        f"  missing ({len(missing)}): {', '.join(missing) or '-'}\\n"
        f"  extra ({len(extra)}): {', '.join(_tag(e) for e in extra) or '-'}"
    )
    return {
        "pass": passed,
        "score": f1,
        "reason": reason,
        "namedScores": {"precision": precision, "recall": recall, "f1": f1},
    }
'''


PROMPTFOOCONFIG_TEMPLATE = """\
description: "Meraki wireless/camera retrieval eval (generated by sample_to_promptfoo.py)"

prompts:
  - "{{question}}"   # only the question reaches the pipeline -- expected_endpoints never leak

providers:
  # AutoTestLLM retrieval pipeline (src/rag/pipeline.py), wrapped for promptfoo.
  - id: "file://rag_provider.py"

defaultTest:
  assert:
    - type: python
      value: file://score_retrieval.py
      metric: retrieval

tests: file://tests.yaml
"""


def write_score_retrieval(out_path):
    with open(out_path, "w") as f:
        f.write(SCORE_RETRIEVAL_TEMPLATE)


def write_promptfooconfig(out_path):
    with open(out_path, "w") as f:
        f.write(PROMPTFOOCONFIG_TEMPLATE)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def print_report(leaves, buckets, cell_report, reuse_log, tiebreak_log, cycle_log,
                  used_leaves, target_tags, id_ranges):
    print("=" * 78)
    print(f"Leaf filter: tags = {target_tags}")
    print(f"Wireless/camera-tagged leaves available: {len(leaves)}")
    print()
    print("Closure-size distribution among those leaves:")
    for b in (1, 2, 3, "4+"):
        print(f"  size {b}: {len(buckets.get(b, []))}")
    print()

    if cycle_log:
        print(f"WARNING: cycle(s) encountered while computing closures, involving nodes: "
              f"{sorted(set(cycle_log))}")
        print()

    if tiebreak_log:
        print(f"Canonical-producer ties resolved: {len(tiebreak_log)} (see below)")
        for t in tiebreak_log[:10]:
            print(f"  {t['consumer']} [{t['param']}]: chose {t['chosen']}, discarded {t['discarded']}")
        if len(tiebreak_log) > 10:
            print(f"  ... and {len(tiebreak_log) - 10} more")
        print()

    print("--- Per-cell fill report ---")
    total_filled = 0
    for cell, planned, available, filled in cell_report:
        gap = planned - filled
        status = "OK" if gap == 0 else f"SHORTFALL of {gap}"
        print(f"  {cell:24s} planned={planned:4d}  pool_available={available:4d}  "
              f"filled={filled:4d}  [{status}]")
        total_filled += filled
    print()
    print(f"Total records emitted: {total_filled} / 300")
    if total_filled < 300:
        print(f"SHORTFALL: {300 - total_filled} fewer records than the planned 300. "
              f"Not padded -- see per-cell gaps above. Reallocate counts if desired.")
    print()

    print("--- Record id ranges by cell ---")
    for cell, rng_ in id_ranges.items():
        print(f"  {cell:24s} {rng_ if rng_ else '(empty)'}")
    print()

    if reuse_log:
        print(f"Forced-reuse / dedup skips logged: {len(reuse_log)}")
        for r in reuse_log:
            print(f"  {r}")
        print()

    print(f"Distinct endpoints used as leaves: {len(used_leaves)}")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph", required=True, help="Path to graph.json")
    parser.add_argument("--out-dir", required=True, help="Directory to write eval outputs into")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducible sampling")
    parser.add_argument("--target-tags", default="wireless,camera",
                         help="Comma-separated tags a leaf endpoint must carry")
    args = parser.parse_args()

    if yaml is None:
        sys.exit("pyyaml is required: pip install pyyaml")

    target_tags = [t.strip() for t in args.target_tags.split(",") if t.strip()]

    graph = load_graph(args.graph)
    nodes = graph["nodes"]
    edges = graph["edges"]

    consistency_problems = consistency_check_graph(nodes, edges)
    if consistency_problems:
        print("WARNING: graph.json internal consistency issues found (proceeding anyway):",
              file=sys.stderr)
        for p in consistency_problems[:20]:
            print(f"  {p}", file=sys.stderr)

    producer_of, tiebreak_log = build_producer_of(nodes, edges)
    leaves, buckets, cycle_log = build_leaf_pool(nodes, producer_of, target_tags)

    records_by_cell, cell_report, reuse_log, used_leaves = sample_all(leaves, buckets, args.seed)
    records, id_ranges = assemble_records(records_by_cell, leaves, nodes)

    problems = self_check(records, nodes, target_tags)
    if problems:
        print("SELF-CHECK FAILURES:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    write_tests_yaml(records, os.path.join(args.out_dir, "tests.yaml"))
    write_score_retrieval(os.path.join(args.out_dir, "score_retrieval.py"))
    write_promptfooconfig(os.path.join(args.out_dir, "promptfooconfig.yaml"))

    print_report(leaves, buckets, cell_report, reuse_log, tiebreak_log, cycle_log,
                 used_leaves, target_tags, id_ranges)
    print(f"Self-check passed ({len(records)} records). Wrote {args.out_dir}/"
          f"{{tests.yaml, score_retrieval.py, promptfooconfig.yaml}}")


if __name__ == "__main__":
    main()
