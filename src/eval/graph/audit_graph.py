#!/usr/bin/env python3
"""
Read-only audit of a Stage-1 dependency graph (graph.json).

Does not assume graph.json's field names. Detects where nodes, edges, and
per-param orphan flags live, prints that mapping so a human can confirm it,
then prints three verification views:

  View 1 - edge sample + the known org->network->ssid->ssid-update chain
  View 2 - full orphan-param report (which IDs are caller-supplied)
  View 3 - dependency-closure size histogram + explicit depth-3 supply verdict

No writes, no network, no LLM. Usage: python audit_graph.py graph.json
"""

import json
import sys
from collections import defaultdict

NODE_HINT_FIELDS = ("method", "path", "params", "operation_id", "summary", "parameters")
NODE_ID_FIELDS = ("id", "endpoint", "endpoint_id", "node_id")
EDGE_FIELD_PAIRS = [
    ("producer", "consumer"),
    ("source", "target"),
    ("from", "to"),
    ("supplier", "consumer"),
    ("parent", "child"),
]
EDGE_PARAM_FIELDS = ("param", "param_name", "name")
NODE_PARAM_LIST_FIELDS = ("params", "path_params", "parameters")
PARAM_NAME_FIELDS = ("name", "param", "param_name")
PARAM_ORPHAN_FIELDS = ("orphan", "is_orphan", "orphan_flag")

KNOWN_CHAIN = [
    "GET /organizations",
    "GET /organizations/{organizationId}/networks",
    "GET /networks/{networkId}/wireless/ssids",
    "PUT /networks/{networkId}/wireless/ssids/{number}",
]


def load_graph(path):
    with open(path) as f:
        return json.load(f)


def find_nodes(graph):
    """Return (top_level_key, {node_id: node_dict}) or (None, None)."""
    for key, value in graph.items():
        if isinstance(value, dict) and value:
            sample = next(iter(value.values()))
            if isinstance(sample, dict) and any(k in sample for k in NODE_HINT_FIELDS):
                return key, value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sample = value[0]
            if any(k in sample for k in NODE_HINT_FIELDS):
                id_field = next((f for f in NODE_ID_FIELDS if f in sample), None)
                if id_field:
                    return key, {n[id_field]: n for n in value}
    return None, None


def find_edges(graph):
    """Return (top_level_key, edges_list, producer_field, consumer_field, param_field, confidence)."""
    for key, value in graph.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            sample = value[0]
            for i, (pf, cf) in enumerate(EDGE_FIELD_PAIRS):
                if pf in sample and cf in sample:
                    param_field = next((f for f in EDGE_PARAM_FIELDS if f in sample), None)
                    confidence = "high (explicit producer/consumer field names)" if i == 0 else \
                        "low -- field names are generic; direction below is a guess, verify manually"
                    return key, value, pf, cf, param_field, confidence
    return None, None, None, None, None, None


def find_param_orphan_schema(nodes):
    """Return (list_field, name_field, orphan_field) for per-node param dicts, or (None, None, None)."""
    for node in nodes.values():
        for list_field in NODE_PARAM_LIST_FIELDS:
            plist = node.get(list_field)
            if isinstance(plist, list) and plist and isinstance(plist[0], dict):
                name_field = next((f for f in PARAM_NAME_FIELDS if f in plist[0]), None)
                orphan_field = next((f for f in PARAM_ORPHAN_FIELDS if f in plist[0]), None)
                if name_field and orphan_field:
                    return list_field, name_field, orphan_field
    return None, None, None


def print_schema_report(graph, nodes_key, nodes, edges_key, edges, pf, cf, param_field, confidence,
                         plist_field, pname_field, porphan_field):
    print("=" * 78)
    print("STEP 0: SCHEMA DETECTION")
    print("=" * 78)
    print(f"Top-level keys: {list(graph.keys())}")
    print()

    print("--- Sample node (raw) ---")
    if nodes:
        sample_id = next(iter(nodes))
        print(f"node id: {sample_id!r}")
        print(json.dumps(nodes[sample_id], indent=2))
    else:
        print("NOT FOUND: no top-level field looked like a node collection.")
    print()

    print("--- Sample edge (raw) ---")
    if edges:
        print(json.dumps(edges[0], indent=2))
    else:
        print("NOT FOUND: no top-level field looked like an edge list.")
    print()

    print("--- Concept mapping ---")
    if nodes_key:
        print(f"Node       = value under top-level key '{nodes_key}', keyed by 'METHOD path' string "
              f"(e.g. {next(iter(nodes))!r}).")
    else:
        print("Node       = NOT FOUND. Cannot proceed with views that need nodes.")
    if edges_key:
        print(f"Edge       = items in top-level list '{edges_key}'.")
        print(f"Direction  = edge['{pf}'] -> edge['{cf}']  "
              f"(producer supplies a path param the consumer requires). Confidence: {confidence}")
        print(f"Param      = edge['{param_field}']" if param_field else
              "Param      = NOT FOUND on edge objects.")
    else:
        print("Edge       = NOT FOUND. Cannot proceed with views that need edges.")
    if plist_field and pname_field and porphan_field:
        print(f"Orphan flag = node['{plist_field}'][i]['{porphan_field}'], keyed by name "
              f"node['{plist_field}'][i]['{pname_field}'] (per-param, stored on each node).")
    else:
        print("Orphan flag = NOT FOUND as a per-node param field. "
              "View 2 will be skipped or degraded -- see below.")
    print()


def view1_edge_sample(nodes, edges, pf, cf, param_field):
    print("=" * 78)
    print("VIEW 1: EDGE SAMPLE (spot-check correctness)")
    print("=" * 78)

    print("--- Known chain check: org -> network -> ssid -> ssid-update ---")
    missing_chain_nodes = [n for n in KNOWN_CHAIN if n not in nodes]
    if missing_chain_nodes:
        print(f"NOTE: these chain nodes are not present in this graph, skipping affected hops: "
              f"{missing_chain_nodes}")
    for a, b in zip(KNOWN_CHAIN, KNOWN_CHAIN[1:]):
        if a in missing_chain_nodes or b in missing_chain_nodes:
            continue
        matches = [e for e in edges if e[pf] == a and e[cf] == b]
        if matches:
            for e in matches:
                param = e.get(param_field, "?") if param_field else "?"
                print(f"  {a}  ->  {b}   [{param}]")
        else:
            print(f"  NO DIRECT EDGE FOUND: {a}  ->  {b}")
    print()

    print("--- General sample (up to 20 edges) ---")
    sample = sorted(edges, key=lambda e: (e[cf], e.get(param_field, "")))[:20]
    for e in sample:
        param = e.get(param_field, "?") if param_field else "?"
        print(f"  {e[pf]}  ->  {e[cf]}   [{param}]")
    print(f"({len(edges)} edges total)")
    print()


def view2_orphan_report(nodes, plist_field, pname_field, porphan_field):
    print("=" * 78)
    print("VIEW 2: ORPHAN-PARAM REPORT (which IDs are treated as caller-supplied)")
    print("=" * 78)
    if not (plist_field and pname_field and porphan_field):
        print("SKIPPED: no per-node orphan-flag field was detected in Step 0.")
        print()
        return

    counts = defaultdict(int)
    total_instances = 0
    for node in nodes.values():
        for p in node.get(plist_field, []):
            if p.get(porphan_field):
                counts[p.get(pname_field)] += 1
                total_instances += 1

    print(f"{len(counts)} distinct orphan param names, {total_instances} orphan param instances "
          f"across all endpoints.")
    print()
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {name} — {count} endpoint{'s' if count != 1 else ''}")
    print()


def view3_closure_histogram(nodes, edges, pf, cf):
    print("=" * 78)
    print("VIEW 3: DEPENDENCY-CLOSURE SIZE HISTOGRAM")
    print("=" * 78)

    direct_producers = defaultdict(set)
    for e in edges:
        direct_producers[e[cf]].add(e[pf])

    memo = {}
    in_progress = set()
    cycle_nodes = set()

    def closure(node_id):
        if node_id in memo:
            return memo[node_id]
        if node_id in in_progress:
            cycle_nodes.add(node_id)
            return {node_id}
        in_progress.add(node_id)
        result = {node_id}
        for producer_id in direct_producers.get(node_id, ()):
            result |= closure(producer_id)
        in_progress.discard(node_id)
        memo[node_id] = result
        return result

    sizes = {}
    for node_id in nodes:
        sizes[node_id] = len(closure(node_id))

    if cycle_nodes:
        print(f"WARNING: cycle(s) detected while walking backward through producer edges. "
              f"This should not happen in a DAG. Nodes involved in a detected cycle: "
              f"{sorted(cycle_nodes)}")
        print("Closure sizes for these nodes were truncated at the point the cycle was hit.")
        print()

    histogram = defaultdict(int)
    for size in sizes.values():
        bucket = size if size <= 3 else "4+"
        histogram[bucket] += 1

    print(f"closure size 1  : {histogram.get(1, 0):4d} endpoints   (one-hop candidates)")
    print(f"closure size 2  : {histogram.get(2, 0):4d}")
    print(f"closure size 3  : {histogram.get(3, 0):4d}")
    print(f"closure size 4+ : {histogram.get('4+', 0):4d}")
    print()

    exactly_2 = histogram.get(2, 0)
    exactly_3 = histogram.get(3, 0)
    at_least_3 = exactly_3 + histogram.get("4+", 0)

    print("--- Bottom line ---")
    print(f"Closure size exactly 2: {exactly_2} endpoints")
    print(f"Closure size exactly 3: {exactly_3} endpoints")
    print(f"Closure size >= 3:      {at_least_3} endpoints")
    print()
    print(f"Planned pool: 100 depth-2 records, 50 depth-3 records.")
    print(f"Depth-2 supply (closure size exactly 2): {exactly_2} available "
          f"{'-- MEETS' if exactly_2 >= 100 else '-- SHORT of'} the 100 needed.")
    if exactly_3 >= 50:
        print(f"Depth-3 supply (closure size exactly 3): {exactly_3} available -- MEETS the 50 needed.")
    else:
        print(f"Depth-3 supply (closure size exactly 3): only {exactly_3} available, "
              f"BELOW the 50 needed (closure size >= 3 gives {at_least_3}, "
              f"in case deeper chains are also acceptable substitutes).")
    print()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python audit_graph.py graph.json")

    graph = load_graph(sys.argv[1])

    nodes_key, nodes = find_nodes(graph)
    edges_key, edges, pf, cf, param_field, confidence = find_edges(graph)
    plist_field, pname_field, porphan_field = (None, None, None)
    if nodes:
        plist_field, pname_field, porphan_field = find_param_orphan_schema(nodes)

    print_schema_report(graph, nodes_key, nodes, edges_key, edges, pf, cf, param_field, confidence,
                         plist_field, pname_field, porphan_field)

    if not nodes or not edges:
        sys.exit("Cannot continue: nodes and/or edges collection was not found. See Step 0 output above.")

    view1_edge_sample(nodes, edges, pf, cf, param_field)
    view2_orphan_report(nodes, plist_field, pname_field, porphan_field)
    view3_closure_histogram(nodes, edges, pf, cf)


if __name__ == "__main__":
    main()
