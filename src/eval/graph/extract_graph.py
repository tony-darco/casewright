#!/usr/bin/env python3
"""
Dependency-graph extractor (Stage 1 of 2).

Parses an OpenAPI spec and emits a deterministic path-parameter dependency DAG
over its endpoints: nodes are `METHOD path` operations, edges are
`producer -> consumer [param]` links derived purely from spec structure
(path templates, `in: path` parameters, and response schemas).

No LLM, no network, no fuzzy matching. Every edge is confirmed against the
producer's response schema; every param that resolves to no producer is
tagged `orphan: true` rather than silently dropped.

Stops at the graph -- does not sample or emit eval records (that's Stage 2).
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HTTP_METHODS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
PATH_TOKEN_RE = re.compile(r"\{(\w+)\}")


def load_spec(path):
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def singularize(word):
    """Deterministic plural -> singular heuristic for collection segment names."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def response_schema(op):
    """First of 200/201 JSON response schemas, or None."""
    responses = op.get("responses", {}) or {}
    for code in ("200", "201"):
        resp = responses.get(code)
        if not resp:
            continue
        schema = resp.get("content", {}).get("application/json", {}).get("schema")
        if schema:
            return schema
    return None


@dataclass
class Operation:
    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    tags: list              # OpenAPI operation tags, verbatim (e.g. ["wireless", "configure"])
    path_params: list       # confirmed path param names, in path order
    segments: list          # path split on '/', literal segments or '{param}' tokens
    is_array_response: bool
    item_fields: set        # field names available on the response item for confirmation

    @property
    def endpoint_id(self):
        return f"{self.method.upper()} {self.path}"


def parse_operations(spec):
    operations = []
    for path, methods in (spec.get("paths") or {}).items():
        segments = path.strip("/").split("/")
        path_tokens = PATH_TOKEN_RE.findall(path)
        for method, op in methods.items():
            if method.lower() not in HTTP_METHODS:
                continue
            params = op.get("parameters", []) or []
            path_param_decls = {p["name"] for p in params if p.get("in") == "path"}

            # Step 1 non-negotiable: cross-check {token} against in:path parameters.
            confirmed_params = [t for t in path_tokens if t in path_param_decls]
            missing = set(path_tokens) - path_param_decls
            if missing:
                print(
                    f"WARNING: {method.upper()} {path} has path token(s) {sorted(missing)} "
                    f"with no matching 'in: path' parameter declaration; ignoring.",
                    file=sys.stderr,
                )

            schema = response_schema(op) or {}
            is_array = schema.get("type") == "array"
            item_fields = set()
            if is_array:
                items = schema.get("items", {}) or {}
                item_fields = set((items.get("properties") or {}).keys())
            elif schema.get("type") == "object":
                item_fields = set((schema.get("properties") or {}).keys())

            operations.append(Operation(
                method=method.lower(),
                path=path,
                operation_id=op.get("operationId", ""),
                summary=op.get("summary", "") or "",
                description=op.get("description", "") or "",
                tags=op.get("tags", []) or [],
                path_params=confirmed_params,
                segments=segments,
                is_array_response=is_array,
                item_fields=item_fields,
            ))
    return operations


def build_producer_index(operations):
    """Raw collection segment (exact text) -> list of lister Operations.

    A lister is a GET whose path terminates at a literal (non-param) segment
    and whose response is a JSON array. Matched wherever the segment appears
    as ANY endpoint's terminal segment, never by path-prefix.
    """
    index = {}
    for op in operations:
        if op.method != "get" or not op.is_array_response:
            continue
        terminal = op.segments[-1]
        if terminal.startswith("{"):
            continue
        index.setdefault(terminal, []).append(op)
    return index


def build_singular_index(producer_index):
    """Singularized collection name -> flattened list of lister Operations.

    Used by Rule 1 ({xId} convention), which reasons about the singular noun.
    """
    index = {}
    for segment, ops in producer_index.items():
        key = singularize(segment)
        index.setdefault(key, []).extend(ops)
    return index


@dataclass
class Edge:
    producer: str
    consumer: str
    param: str
    rule: int


def common_prefix_len(segments_a, segments_b):
    n = 0
    for a, b in zip(segments_a, segments_b):
        if a != b:
            break
        n += 1
    return n


def pick_producer(confirmed, consumer):
    """Deterministic tie-break for >1 confirmed producer candidates.

    Primary key: longest shared leading path-segment prefix with the consumer
    (exact segment/text match, including '{param}' tokens) -- i.e. prefer the
    producer whose scope (network-level, org-level, its own direct parent
    collection, ...) matches the consumer's own scope. This is spec-structural,
    not name-fuzzy: it directly compares path segments.

    Secondary keys (only when scope-affinity ties): fewest unresolved path
    params, then shortest path string, then alphabetical -- i.e. the most
    general/canonical lister, echoing the 'always pick the lister over
    get-by-id' convention. Fully deterministic; no fuzzy scoring.
    """
    ranked = sorted(confirmed, key=lambda o: (
        -common_prefix_len(consumer.segments, o.segments),
        len(o.path_params),
        len(o.path),
        o.path,
    ))
    return ranked[0], ranked[1:]


def resolve_param(consumer, param_name, producer_index, singular_index, ambiguity_log, orphan_log):
    """Resolve one path param to a producer.

    Rule 1 and Rule 2 candidates are gathered together (not short-circuited)
    and disambiguated as a single pool by scope-affinity. This matters:
    a same-named plural collection can exist in two unrelated domains (e.g.
    two different '.../profiles' resources), one using a generic 'id' field
    (satisfies Rule 1) and the other a domain-specific '<x>Id' field that
    matches the consumer's own literal param name (satisfies Rule 2). Trying
    Rule 1 to exhaustion before ever considering Rule 2 would let the
    unrelated-domain 'id' match win by sheer precedence -- exactly the
    phantom-edge-from-name-coincidence the confirmation step exists to
    prevent. Pooling both rules' confirmed candidates and picking by scope
    affinity closes that gap while keeping every decision deterministic and
    auditable (each candidate is still individually confirmed against its
    own response schema; only the tie-break span changed).
    """
    candidates = []  # list of (rule_number, Operation), each already confirmed

    # Rule 1: {xId} convention, confirmed via literal 'id' field.
    base = None
    if param_name.endswith("Id") and len(param_name) > 2:
        base = param_name[:-2]
        for c in singular_index.get(base, []):
            if "id" in c.item_fields:
                candidates.append((1, c))

    # Rule 2: literal-field fallback on the immediately preceding collection segment.
    token = "{" + param_name + "}"
    if token not in consumer.segments:
        orphan_log.append({"consumer": consumer.endpoint_id, "param": param_name,
                            "reason": "param token not found in own segment list (unexpected)"})
        return None
    seg_index = consumer.segments.index(token)
    preceding = consumer.segments[seg_index - 1] if seg_index > 0 else None
    if preceding is not None:
        for c in producer_index.get(preceding, []):
            if param_name in c.item_fields:
                candidates.append((2, c))

    if not candidates:
        reason = (f"no collection-name match for '{base}' (Rule 1)" if base else "not an {xId}-style param")
        if preceding is not None:
            reason += f"; no lister for '{preceding}' confirms field '{param_name}' (Rule 2)"
        else:
            reason += "; no preceding path segment (Rule 2 inapplicable)"
        orphan_log.append({"consumer": consumer.endpoint_id, "param": param_name, "reason": reason})
        return None

    # Dedupe by endpoint (an op could in principle satisfy both rules); prefer the Rule 1 label.
    best_by_endpoint = {}
    for rule, op in candidates:
        prior = best_by_endpoint.get(op.endpoint_id)
        if prior is None or rule < prior[0]:
            best_by_endpoint[op.endpoint_id] = (rule, op)

    confirmed_ops = [op for _, op in best_by_endpoint.values()]
    chosen, discarded = pick_producer(confirmed_ops, consumer)
    chosen_rule = best_by_endpoint[chosen.endpoint_id][0]
    if discarded:
        ambiguity_log.append({
            "consumer": consumer.endpoint_id, "param": param_name, "rule": chosen_rule,
            "chosen": chosen.endpoint_id,
            "discarded": [d.endpoint_id for d in discarded],
            "reason": "multiple confirmed producer candidates (Rule 1 and/or Rule 2); "
                      "picked by closest scope match to consumer path "
                      "(longest shared leading segment prefix)",
        })
    return Edge(chosen.endpoint_id, consumer.endpoint_id, param_name, rule=chosen_rule)


def build_graph(spec):
    operations = parse_operations(spec)
    producer_index = build_producer_index(operations)
    singular_index = build_singular_index(producer_index)

    edges = []
    ambiguity_log = []
    orphan_log = []
    orphans_by_endpoint = {}  # endpoint_id -> set of orphan param names

    for op in operations:
        for param_name in op.path_params:
            edge = resolve_param(op, param_name, producer_index, singular_index,
                                  ambiguity_log, orphan_log)
            if edge:
                edges.append(edge)
            else:
                orphans_by_endpoint.setdefault(op.endpoint_id, set()).add(param_name)

    nodes = {}
    for op in operations:
        orphan_params = orphans_by_endpoint.get(op.endpoint_id, set())
        nodes[op.endpoint_id] = {
            "method": op.method,
            "path": op.path,
            "operation_id": op.operation_id,
            "summary": op.summary,
            "description": op.description,
            "tags": op.tags,
            "params": [
                {"name": p, "orphan": p in orphan_params}
                for p in op.path_params
            ],
            "is_array_response": op.is_array_response,
            "response_fields": sorted(op.item_fields),
        }

    standalone_candidates = sorted(orphans_by_endpoint.keys())
    orphan_param_count = sum(len(v) for v in orphans_by_endpoint.values())

    graph = {
        "nodes": nodes,
        "edges": [edge.__dict__ for edge in edges],
        "ambiguities": ambiguity_log,
        "orphans": orphan_log,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "orphan_param_count": orphan_param_count,
            "ambiguities_resolved": len(ambiguity_log),
            "standalone_candidate_count": len(standalone_candidates),
            "standalone_candidates": standalone_candidates,
        },
    }
    return graph


def print_report(graph, print_edges):
    stats = graph["stats"]

    if print_edges:
        print("=== Edge list (producer -> consumer [param]) ===")
        for e in sorted(graph["edges"], key=lambda e: (e["consumer"], e["param"])):
            print(f"{e['producer']} -> {e['consumer']} [{e['param']}] (rule {e['rule']})")
        print()

        print("=== Orphan params (no producer found; dependency roots) ===")
        orphans_by_endpoint = {
            endpoint_id: [p["name"] for p in node["params"] if p["orphan"]]
            for endpoint_id, node in graph["nodes"].items()
            if any(p["orphan"] for p in node["params"])
        }
        for endpoint_id, params in sorted(orphans_by_endpoint.items()):
            print(f"{endpoint_id}: {', '.join(params)}")
        print()

        if graph["ambiguities"]:
            print("=== Ambiguities resolved (lister chosen over discarded candidates) ===")
            for a in graph["ambiguities"]:
                print(f"{a['consumer']} [{a['param']}] (rule {a['rule']}): "
                      f"chose {a['chosen']}; discarded {a['discarded']} -- {a['reason']}")
            print()

    print("=== Summary stats ===")
    print(f"Nodes (endpoints):        {stats['node_count']}")
    print(f"Edges (resolved deps):    {stats['edge_count']}")
    print(f"Orphan params:            {stats['orphan_param_count']}")
    print(f"Ambiguities resolved:     {stats['ambiguities_resolved']}")
    print(f"Standalone candidates:    {stats['standalone_candidate_count']} "
          f"(endpoints with >=1 unmet/orphan path param)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, help="Path to OpenAPI spec (JSON or YAML)")
    parser.add_argument("--out", required=True, help="Path to write graph.json")
    parser.add_argument("--print-edges", action="store_true",
                         help="Print the full edge list, orphan list, and ambiguity log")
    args = parser.parse_args()

    spec = load_spec(args.spec)
    graph = build_graph(spec)

    Path(args.out).write_text(json.dumps(graph, indent=2))
    print_report(graph, args.print_edges)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
