#!/usr/bin/env python3
"""Smoke test for extract_graph.py against fixtures/fixture_spec.json.

Asserts the four behaviors called out in the spec:
1. org -> network -> ssid -> ssid-update chain resolves correctly.
2. {number} resolves via Rule 2 (literal field on the ssids lister's response).
3. {serial} is tagged orphan: true.
4. The ambiguous networkId resolves to the lister, not the get-by-id.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
EXTRACTOR = REPO_ROOT / "src" / "eval" / "graph" / "extract_graph.py"
SPEC = REPO_ROOT / "fixtures" / "fixture_spec.json"
OUT = Path(tempfile.gettempdir()) / "fixture_graph.json"

ORGS = "GET /organizations"
ORG_NETWORKS = "GET /organizations/{organizationId}/networks"
NETWORK_BY_ID = "GET /networks/{networkId}"
SSIDS = "GET /networks/{networkId}/wireless/ssids"
SSID_UPDATE = "PUT /networks/{networkId}/wireless/ssids/{number}"
DEVICE = "GET /devices/{serial}"


def edge_set(graph):
    return {(e["producer"], e["consumer"], e["param"]) for e in graph["edges"]}


def main():
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR),
         "--spec", str(SPEC), "--out", str(OUT), "--print-edges"],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(f"extract_graph.py exited {result.returncode}")

    graph = json.loads(OUT.read_text())
    edges = edge_set(graph)

    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    # 1. org -> network -> ssid -> ssid-update chain
    check((ORGS, ORG_NETWORKS, "organizationId") in edges,
          f"missing edge {ORGS} -> {ORG_NETWORKS} [organizationId]")
    check((ORG_NETWORKS, SSIDS, "networkId") in edges,
          f"missing edge {ORG_NETWORKS} -> {SSIDS} [networkId]")
    check((ORG_NETWORKS, SSID_UPDATE, "networkId") in edges,
          f"missing edge {ORG_NETWORKS} -> {SSID_UPDATE} [networkId]")

    # 2. {number} resolves via Rule 2 from the ssids lister
    number_edges = [e for e in graph["edges"]
                    if e["consumer"] == SSID_UPDATE and e["param"] == "number"]
    check(len(number_edges) == 1, f"expected exactly one edge for 'number' param, got {number_edges}")
    if number_edges:
        check(number_edges[0]["producer"] == SSIDS,
              f"'number' should be produced by {SSIDS}, got {number_edges[0]['producer']}")
        check(number_edges[0]["rule"] == 2, f"'number' should resolve via Rule 2, got rule {number_edges[0]['rule']}")

    # 3. {serial} tagged orphan: true, with no producer edge
    device_node = graph["nodes"][DEVICE]
    serial_param = next(p for p in device_node["params"] if p["name"] == "serial")
    check(serial_param["orphan"] is True, "'serial' should be tagged orphan: true")
    check(not any(e["consumer"] == DEVICE for e in graph["edges"]),
          f"{DEVICE} should have no incoming producer edges")

    # 4. ambiguous networkId resolves to the lister, not the decoy get-by-id
    check(not any(e["producer"] == NETWORK_BY_ID for e in graph["edges"]),
          f"{NETWORK_BY_ID} (get-by-id) must never be selected as a producer")
    network_id_producers = {e["producer"] for e in graph["edges"] if e["param"] == "networkId"}
    check(network_id_producers == {ORG_NETWORKS},
          f"networkId should be produced only by {ORG_NETWORKS}, got {network_id_producers}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        sys.exit(1)

    print("\nAll 4 fixture assertions passed.")


if __name__ == "__main__":
    main()
