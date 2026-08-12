"""Versioned output (Run feature): each run is tagged with the code version it ran,
and the Output-tab context is scoped to that version — browsing an old version shows
the outputs produced from it, not the latest run.
"""

from web.services import runs_store, tests_store


def _test_with_two_versions():
    t = tests_store.create_generating("T", "prompt v0", "py", [])
    tid = t["id"]
    tests_store.finish_test(tid, "f.py", "code v0", [], None)
    tests_store.add_version(tid, "prompt v0", "f.py", "code v0", "py", [], None)
    tests_store.finish_test(tid, "f.py", "code v1", [], None)
    tests_store.add_version(tid, "prompt v1", "f.py", "code v1", "py", [], None)
    return tid


def test_runs_are_scoped_to_their_code_version():
    tid = _test_with_two_versions()

    a = runs_store.create_run(tid, "example", version_no=0)
    b = runs_store.create_run(tid, "example", version_no=0)
    c = runs_store.create_run(tid, "example", version_no=1)

    v0 = [r["run_code"] for r in runs_store.version_runs(tid, 0)]
    v1 = [r["run_code"] for r in runs_store.version_runs(tid, 1)]
    assert v0 == [a["run_code"], b["run_code"]]   # oldest-first, only v0's runs
    assert v1 == [c["run_code"]]                  # v1's run doesn't leak into v0


def test_output_nav_positions_and_neighbours():
    tid = _test_with_two_versions()
    a = runs_store.create_run(tid, "example", version_no=0)
    b = runs_store.create_run(tid, "example", version_no=0)

    # first of two: no prev, next+latest point at the second
    first = runs_store.output_nav(tid, 0, a["id"])
    assert (first["run_index"], first["run_total"]) == (0, 2)
    assert first["prev_run_id"] is None
    assert first["next_run_id"] == b["id"] == first["latest_run_id"]

    # last of two: prev points back, no next
    last = runs_store.output_nav(tid, 0, b["id"])
    assert (last["run_index"], last["run_total"]) == (1, 2)
    assert last["prev_run_id"] == a["id"]
    assert last["next_run_id"] is None


def test_output_nav_empty_version():
    tid = _test_with_two_versions()
    nav = runs_store.output_nav(tid, 1, None)   # v1 has no runs yet
    assert nav["run_total"] == 0
    assert nav["run_index"] == -1
    assert nav["latest_run_id"] is None
