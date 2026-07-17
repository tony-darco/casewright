"""Deterministic traversal of the API endpoint dependency graph (graph.json).

graph.json (produced by src/eval/graph/extract_graph.py) records, over the whole
OpenAPI spec, which endpoints must run before others: an edge
``producer -> consumer [param]`` means the producer supplies a path parameter the
consumer requires. This module answers, for a given endpoint id ("METHOD path"):

  * upstream   — the ordered chain of endpoints to call first (its dependencies)
  * downstream — the endpoints that depend on it (its dependents / impact set)

Pure Python, no LLM, no network. The upstream closure mirrors the algorithm in
src/eval/sampling/sample_to_promptfoo.py (build_producer_of + compute_closure) and
the forward walk in src/eval/graph/audit_graph.py; it is reimplemented here so the
runtime rag/ layer stays decoupled from the eval tooling.
"""

import json
from collections import defaultdict
from pathlib import Path

from rag import GRAPH_JSON

# Path to the dependency graph, anchored on the package (see rag/__init__.py) so
# lookups work regardless of the process CWD.
DEFAULT_GRAPH_PATH = GRAPH_JSON

DOWNSTREAM_RENDER_CAP = 10  # dependents can fan out widely; keep prompts bounded
UPSTREAM_CLOSURE_CAP = 10   # prerequisite producers to add to the retrieved set (upstream is shallow)

# Path params the app always supplies as concrete values (services.generate injects
# them; the Run feature swaps networkId for the ephemeral network at run time). They
# are treated like caller-supplied (orphan) params: never chase a producer for them,
# so the rendered call-order never tells the model to list orgs/networks to
# "discover" an id it was already handed.
SUPPLIED_PARAMS = frozenset({"organizationId", "networkId"})


class DependencyGraph:
    def __init__(self, path=DEFAULT_GRAPH_PATH):
        data = json.loads(Path(path).read_text())
        self.nodes = data.get("nodes", {})
        self.edges = data.get("edges", [])
        self._build_indexes()

    def _build_indexes(self):
        # One canonical producer per (consumer, param): prefer the lister, then the
        # shortest path, then id. Re-applied here so we don't assume graph.json
        # already narrowed each param to a single edge.
        candidates = defaultdict(list)
        for e in self.edges:
            candidates[(e["consumer"], e["param"])].append(e["producer"])
        self.producer_of = {
            cp: sorted(set(producers), key=self._producer_sort_key)[0]
            for cp, producers in candidates.items()
        }

        self.direct_consumers = defaultdict(set)  # producer -> {consumers}
        for e in self.edges:
            self.direct_consumers[e["producer"]].add(e["consumer"])

    def _producer_sort_key(self, producer_id):
        node = self.nodes.get(producer_id, {})
        is_lister = 0 if node.get("is_array_response") else 1
        segments = len(node.get("path", producer_id).strip("/").split("/"))
        return (is_lister, segments, producer_id)

    def has(self, endpoint_id):
        return endpoint_id in self.nodes

    # -- traversal -----------------------------------------------------------

    def upstream(self, endpoint_id):
        """Ordered root->leaf closure of producers, ending at endpoint_id.

        One canonical producer per param; orphan (caller-supplied) params stop a
        branch. Cycle-guarded, though graph.json is a DAG.
        """
        order, visited, stack = [], set(), set()

        def visit(nid):
            if nid in visited or nid in stack:
                return
            stack.add(nid)
            for param in sorted(self.nodes.get(nid, {}).get("params", []), key=lambda p: p["name"]):
                if param.get("orphan") or param["name"] in SUPPLIED_PARAMS:
                    continue
                producer = self.producer_of.get((nid, param["name"]))
                if producer:
                    visit(producer)
            stack.discard(nid)
            visited.add(nid)
            order.append(nid)

        visit(endpoint_id)
        return order

    def downstream(self, endpoint_id):
        """Transitive consumers (BFS, nearest first), excluding endpoint_id."""
        seen, out, frontier = set(), [], [endpoint_id]
        while frontier:
            nxt = []
            for nid in frontier:
                for consumer in sorted(self.direct_consumers.get(nid, ())):
                    if consumer != endpoint_id and consumer not in seen:
                        seen.add(consumer)
                        out.append(consumer)
                        nxt.append(consumer)
            frontier = nxt
        return out

    def upstream_closure(self, endpoint_ids, limit=UPSTREAM_CLOSURE_CAP):
        """Deduped prerequisite producers (transitively) required by the given
        endpoints, excluding the inputs themselves, ordered producers-first.

        This is the call-order prerequisite set to surface *alongside* the
        retrieved target endpoints (e.g. GET /organizations, which supplies the
        organizationId a target needs). upstream() already ends at the endpoint
        itself, so we drop the inputs and dedupe across them. Capped at `limit`.
        """
        if isinstance(endpoint_ids, str):
            endpoint_ids = [endpoint_ids]
        targets = set(endpoint_ids)
        out, seen = [], set()
        for eid in endpoint_ids:
            if not self.has(eid):
                continue
            for nid in self.upstream(eid):        # root->leaf, ends at eid
                if nid in targets or nid in seen:
                    continue
                seen.add(nid)
                out.append(nid)
        return out[:limit]

    def _needs(self, endpoint_id):
        """[(param, producer_or_None, is_orphan)] for endpoint_id's path params."""
        needs = []
        for p in sorted(self.nodes.get(endpoint_id, {}).get("params", []), key=lambda p: p["name"]):
            if p.get("orphan") or p["name"] in SUPPLIED_PARAMS:
                needs.append((p["name"], None, True))
            else:
                needs.append((p["name"], self.producer_of.get((endpoint_id, p["name"])), False))
        return needs

    # -- output --------------------------------------------------------------

    def dependencies(self, endpoint_ids, direction="both"):
        """Structured view. direction: 'upstream' | 'downstream' | 'both'."""
        if isinstance(endpoint_ids, str):
            endpoint_ids = [endpoint_ids]
        result = {}
        for eid in endpoint_ids:
            entry = {"known": self.has(eid), "summary": self.nodes.get(eid, {}).get("summary", "")}
            if direction in ("upstream", "both"):
                entry["upstream"] = self.upstream(eid) if self.has(eid) else []
            if direction in ("downstream", "both"):
                entry["downstream"] = self.downstream(eid) if self.has(eid) else []
            result[eid] = entry
        return result

    def render(self, endpoint_ids, direction="both"):
        """LLM/human-readable text block for the given endpoints."""
        if isinstance(endpoint_ids, str):
            endpoint_ids = [endpoint_ids]
        blocks = []
        for eid in endpoint_ids:
            if not self.has(eid):
                blocks.append(f"{eid}\n  (not found in the dependency graph)")
                continue
            lines = [eid]
            summary = self.nodes.get(eid, {}).get("summary", "")
            if summary:
                lines.append(f"  summary: {summary}")

            if direction in ("upstream", "both"):
                chain = self.upstream(eid)
                if len(chain) > 1:
                    lines.append("  call first (in order):")
                    for i, nid in enumerate(chain, start=1):
                        lines.append(f"    {i}. {nid}{self._needs_suffix(nid)}")
                else:
                    lines.append("  call first: none (no upstream dependencies)")

            if direction in ("downstream", "both"):
                deps = self.downstream(eid)
                if deps:
                    shown = deps[:DOWNSTREAM_RENDER_CAP]
                    lines.append(f"  depended on by ({len(deps)}):")
                    lines.extend(f"    - {nid}" for nid in shown)
                    if len(deps) > len(shown):
                        lines.append(f"    ... and {len(deps) - len(shown)} more")
                else:
                    lines.append("  depended on by: none")

            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def _needs_suffix(self, endpoint_id):
        needs = self._needs(endpoint_id)
        if not needs:
            return ""
        parts = [
            f"{name}<-caller" if (orphan or producer is None) else f"{name}<-{producer}"
            for name, producer, orphan in needs
        ]
        return "  (needs: " + ", ".join(parts) + ")"


_DEFAULT = None


def get_default_graph():
    """Lazily loaded, shared DependencyGraph over DEFAULT_GRAPH_PATH."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DependencyGraph()
    return _DEFAULT
