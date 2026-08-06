"""Endpoint coverage: which spec endpoints have tests, and have those tests run.

Two layers, deliberately kept apart:

- The **spec tree** (groups -> subgroups -> endpoints) is global and immutable, so it
  is parsed once from the pinned OpenAPI snapshot and cached for the process.
- The **coverage overlay** (endpoint -> tests -> latest run status) changes whenever a
  test is generated or a run finishes, so it is rebuilt on every view. Caching it would
  show stale coverage, which is the one thing this page must get right.

Grouping is by Meraki product family, read off the operation's tags. ``tags[0]`` is the
scope and partitions all 957 operations cleanly (verified: no operation lands in two
families, none has empty tags); everything that isn't a hardware family falls into
"Other", sub-grouped by that same scope tag so it stays navigable.
"""

import logging

from eval.graph.extract_graph import load_spec, parse_operations
from web import config
from web.services import generate, runs_store, tests_store

logger = logging.getLogger("web.coverage")

_FAMILY = {"wireless": "Wireless", "switch": "Switch", "camera": "Camera",
           "sensor": "Sensor", "appliance": "Appliance"}
_GROUP_ORDER = ("Wireless", "Switch", "Camera", "Sensor", "Appliance", "Other")
_SKIP = {"configure", "monitor"}          # intent tags, not resource areas
_TERMINAL_FAIL = ("failed", "error")

_tree = None                              # lazy spec-tree cache; see _spec_tree


def _classify(tags):
    """(group, subgroup) for an operation's tags. Group is the hardware family, or
    'Other'; subgroup is the resource area within it (the scope tag itself for
    'Other', which would otherwise be one undifferentiated 497-endpoint list)."""
    group = _FAMILY.get(tags[0], "Other")
    rest = [t for t in tags[1:] if t not in _SKIP]
    subgroup = tags[0] if group == "Other" else (rest[0] if rest else "general")
    return group, subgroup


def _build_spec_tree():
    """Parse the pinned spec into groups -> subgroups -> endpoint ids (~30ms)."""
    # str(): extract_graph.load_spec does path.endswith(), which a Path lacks.
    ops = parse_operations(load_spec(str(config.SPEC_PATH)))
    groups, summaries = {}, {}
    for op in ops:
        if not op.tags:
            continue
        eid = op.endpoint_id
        summaries[eid] = op.summary
        group, subgroup = _classify(op.tags)
        groups.setdefault(group, {}).setdefault(subgroup, []).append(eid)
    return {
        "groups": [
            {"name": g, "subgroups": [
                {"name": s, "endpoints": sorted(groups[g][s])}
                for s in sorted(groups[g])
            ]}
            for g in _GROUP_ORDER if g in groups
        ],
        "summaries": summaries,
    }


def _spec_tree():
    """The cached spec tree. A missing or malformed spec degrades to an empty tree —
    the page then renders its empty state rather than 500ing the dashboard."""
    global _tree
    if _tree is None:
        try:
            _tree = _build_spec_tree()
        except Exception:
            logger.exception("could not load the OpenAPI spec at %s", config.SPEC_PATH)
            _tree = {"groups": [], "summaries": {}}
    return _tree


def _usage_index():
    """endpoint id -> [{test_id, name, role, run_status}] across every test.

    A test reaches an endpoint as a 'target' (the pipeline retrieved it as what the
    test is for) or a 'prereq' (an upstream producer it calls to set up). If both,
    target wins — it's the stronger claim.
    """
    latest = runs_store.latest_status_by_test()
    index = {}
    for t in tests_store.list_endpoint_usage():
        targets = generate.parse_devices(t["endpoints_json"])
        prereqs = generate.parse_devices(t["dep_endpoints_json"])
        for eid in dict.fromkeys(list(targets) + list(prereqs)):
            index.setdefault(eid, []).append({
                "test_id": t["id"],
                "name": t["name"],
                "role": "target" if eid in targets else "prereq",
                "run_status": latest.get(t["id"]),
            })
    return index


def _leaf_state(entries):
    """An endpoint's status across every test that uses it — worst wins, so one
    failing test isn't hidden behind a passing one."""
    if not entries:
        return "none"
    statuses = [e["run_status"] for e in entries]
    if any(s in _TERMINAL_FAIL for s in statuses):
        return "fail"
    if any(s == "success" for s in statuses):
        return "pass"
    return "covered"


def tree_view():
    """The whole tree with coverage overlaid, plus rollup counts."""
    index = _usage_index()
    groups = []
    for g in _spec_tree()["groups"]:
        subgroups, g_total, g_covered = [], 0, 0
        for s in g["subgroups"]:
            leaves = [{"id": eid, "state": _leaf_state(index.get(eid, []))}
                      for eid in s["endpoints"]]
            covered = sum(1 for leaf in leaves if leaf["state"] != "none")
            subgroups.append({"name": s["name"], "leaves": leaves,
                              "total": len(leaves), "covered": covered})
            g_total += len(leaves)
            g_covered += covered
        groups.append({"name": g["name"], "subgroups": subgroups,
                       "total": g_total, "covered": g_covered})
    return {
        "groups": groups,
        "total": sum(g["total"] for g in groups),
        "covered": sum(g["covered"] for g in groups),
    }


def endpoint_detail(ep):
    """Every test that uses ``ep``, or None if it isn't an endpoint in the spec —
    which also keeps arbitrary input from reaching the template."""
    tree = _spec_tree()
    if ep not in tree["summaries"]:
        return None
    entries = _usage_index().get(ep, [])
    return {
        "endpoint": ep,
        "summary": tree["summaries"][ep],
        "state": _leaf_state(entries),
        "tests": entries,
    }
