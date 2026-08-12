"""Start a run in the background.

Validates the run configuration and wires up the orchestrator, which does the
provision → run → teardown work in a daemon thread via ``run_registry``; the caller
subscribes to the returned job for live output.
"""

from web.services import (
    run_orchestrator, run_registry, runs_store, store, tests_store,
)


def start_run(test_id, run_source="example", network_id="", version_no=None):
    """Validate + launch a run. Returns ``(run, error, job)``.

    ``error`` non-None means nothing started (bad config); ``job`` is None when the run
    row exists but no orchestrator thread was launched (the single-run guard rejected a
    concurrent run — inspect ``run`` for the recorded error)."""
    test = tests_store.get_test(test_id)
    if not test:
        return None, "Test not found.", None

    # Which code version to run: the latest (editable row) unless an older version is
    # being viewed, in which case run that snapshot and tag the run to it.
    latest_no = max(len(tests_store.list_versions(test_id)) - 1, 0)
    run_version_no = latest_no
    if version_no is not None and version_no < latest_no:
        run_version_no = version_no
        snapshot = tests_store.get_version(test_id, run_version_no)
        if snapshot:
            test = {**test, "code": snapshot["code"], "language": snapshot["language"],
                    "file_name": snapshot["file_name"]}

    source = "scratch" if run_source == "scratch" else "example"
    tests_store.set_run_config(test_id, source, network_id.strip())

    key = store.get_meraki_key()
    if not key:
        return None, "Add a Meraki API key in Settings first.", None

    if source == "example":
        example_network_id = network_id.strip() or store.get_default_network_id()
        if not example_network_id:
            return None, "Select an example network (or set a default in Settings).", None
        org_id = store.org_id_for_network(example_network_id)
    else:
        example_network_id = ""
        org_id = store.org_id_for_network(store.get_default_network_id())
    if not org_id:
        return None, "Connect a Meraki organization in Settings first.", None

    run = runs_store.create_run(test_id, source, example_network_id, version_no=run_version_no)
    if not run_orchestrator.begin(run["id"]):
        runs_store.update_status(run["id"], "error",
                                 "A run is already in progress. Wait for it to finish.")
        return runs_store.get_run(run["id"]), None, None

    job = run_registry.start(run["id"], lambda job: run_orchestrator.start_run(
        job, test, run["id"], run["run_code"], org_id, source, example_network_id, key))
    return runs_store.get_run(run["id"]), None, job
