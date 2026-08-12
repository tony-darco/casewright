"""Test the endpoint coverage tree: spec grouping, and the usage overlay."""

from web.services import coverage, runs_store, tests_store


# --- spec tree -----------------------------------------------------------------

def test_grouping_partitions_the_whole_spec():
    """Every operation lands in exactly one group, with the counts the tree is built
    around. The rule reads tags positionally, so a spec bump that reorders tags would
    silently reshuffle the tree — this is the guard against that."""
    tree = coverage._spec_tree()
    counts = {g["name"]: sum(len(s["endpoints"]) for s in g["subgroups"]) for g in tree["groups"]}
    assert counts == {"Wireless": 136, "Switch": 102, "Camera": 46,
                      "Sensor": 19, "Appliance": 157, "Other": 497}
    assert sum(counts.values()) == 957 == len(tree["summaries"])

    # no endpoint appears under two groups
    seen = [eid for g in tree["groups"] for s in g["subgroups"] for eid in s["endpoints"]]
    assert len(seen) == len(set(seen)) == 957


def test_groups_are_ordered_families_then_other():
    names = [g["name"] for g in coverage._spec_tree()["groups"]]
    assert names == ["Wireless", "Switch", "Camera", "Sensor", "Appliance", "Other"]


def test_other_is_subgrouped_not_one_flat_list():
    other = next(g for g in coverage._spec_tree()["groups"] if g["name"] == "Other")
    subs = {s["name"]: len(s["endpoints"]) for s in other["subgroups"]}
    assert subs["organizations"] == 228
    assert subs["networks"] == 115
    assert len(other["subgroups"]) == 11


def test_classify_skips_intent_tags():
    assert coverage._classify(["wireless", "configure", "ssids"]) == ("Wireless", "ssids")
    assert coverage._classify(["wireless", "monitor"]) == ("Wireless", "general")
    # 'Other' subgroups by the scope tag itself, not the resource area
    assert coverage._classify(["organizations", "configure", "admins"]) == ("Other", "organizations")


# --- leaf state ----------------------------------------------------------------

def test_leaf_state_covers_the_four_states():
    assert coverage._leaf_state([]) == "none"
    assert coverage._leaf_state([{"run_status": None}]) == "covered"
    assert coverage._leaf_state([{"run_status": "success"}]) == "pass"
    assert coverage._leaf_state([{"run_status": "failed"}]) == "fail"
    assert coverage._leaf_state([{"run_status": "error"}]) == "fail"


def test_leaf_state_worst_wins():
    """One passing test must not hide a failing one on the same endpoint."""
    assert coverage._leaf_state([{"run_status": "success"}, {"run_status": "failed"}]) == "fail"
    assert coverage._leaf_state([{"run_status": "success"}, {"run_status": None}]) == "pass"


# --- overlay -------------------------------------------------------------------

def _finished_test(name, endpoints, deps=None):
    t = tests_store.create_generating(name, "p", "py", [])
    tests_store.finish_test(t["id"], "f.test.py", "code", endpoints, None,
                            dep_endpoints=deps)
    return t["id"]


def test_usage_index_separates_targets_from_prerequisites():
    _finished_test("T", ["GET /organizations/{organizationId}/networks"],
                   deps=["GET /organizations"])
    index = coverage._usage_index()
    assert index["GET /organizations/{organizationId}/networks"][0]["role"] == "target"
    assert index["GET /organizations"][0]["role"] == "prereq"


def test_target_wins_when_an_endpoint_is_both():
    _finished_test("T", ["GET /organizations"], deps=["GET /organizations"])
    entries = coverage._usage_index()["GET /organizations"]
    assert len(entries) == 1 and entries[0]["role"] == "target"


def test_generating_tests_are_not_counted():
    tests_store.create_generating("half-built", "p", "py", [])
    assert coverage._usage_index() == {}


def test_latest_terminal_run_wins_and_in_flight_is_ignored():
    """A queued run says nothing yet — it must not mask the last real result."""
    tid = _finished_test("T", ["GET /organizations"])
    r1 = runs_store.create_run(tid, "example")
    runs_store.update_status(r1["id"], "success")
    assert coverage._usage_index()["GET /organizations"][0]["run_status"] == "success"

    runs_store.create_run(tid, "example")          # queued, non-terminal
    assert runs_store.latest_status_by_test()[tid] == "success"

    r3 = runs_store.create_run(tid, "example")
    runs_store.update_status(r3["id"], "failed")
    assert runs_store.latest_status_by_test()[tid] == "failed"


def test_tree_view_rolls_up_counts():
    _finished_test("T", ["GET /organizations"])
    vm = coverage.tree_view()
    assert vm["total"] == 957 and vm["covered"] == 1
    other = next(g for g in vm["groups"] if g["name"] == "Other")
    assert other["covered"] == 1
    orgs = next(s for s in other["subgroups"] if s["name"] == "organizations")
    assert orgs["covered"] == 1 and orgs["total"] == 228
    assert next(l for l in orgs["leaves"] if l["id"] == "GET /organizations")["state"] == "covered"


# --- detail --------------------------------------------------------------------

def test_endpoint_detail_lists_tests():
    _finished_test("Net test", ["GET /organizations/{organizationId}/networks"],
                   deps=["GET /organizations"])
    vm = coverage.endpoint_detail("GET /organizations")
    assert vm["state"] == "covered"
    assert [t["name"] for t in vm["tests"]] == ["Net test"]
    assert vm["tests"][0]["role"] == "prereq"
    assert vm["summary"]


def test_endpoint_detail_rejects_unknown_endpoints():
    """Guards the template from arbitrary input: only real spec endpoints resolve."""
    assert coverage.endpoint_detail("GET /nope") is None
    assert coverage.endpoint_detail("<script>alert(1)</script>") is None
    assert coverage.endpoint_detail("") is None


def test_uncovered_endpoint_has_no_tests():
    vm = coverage.endpoint_detail("GET /organizations")
    assert vm["state"] == "none" and vm["tests"] == []
