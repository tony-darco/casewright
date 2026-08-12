"""End-to-end orchestration test (Meraki + Docker mocked): a full run goes
queued -> provisioning -> running -> success/error, teardown always fires, and the
single-run guard rejects a second concurrent run.
"""

from unittest import mock

from web.services import network_provision as np
from web.services import run_orchestrator as orch
from web.services import runs_store, tests_store
from web.services.gen_registry import Job
from web.services.runners import base as runner_base


def _dev(serial, model, product_type):
    return {"serial": serial, "model": model, "productType": product_type, "name": serial,
            "mac": "", "networkId": "", "claimed": False}


def _fake_container(exit_code=0):
    c = mock.Mock()
    c.logs.return_value = iter([b"ok\n"])
    c.wait.return_value = {"StatusCode": exit_code}
    return c


def test_full_run_success_and_teardown():
    orch._release()  # ensure clean slate regardless of test order

    t = tests_store.create_test("T", "prompt", "test_generated.py", "NET='L_old'\n",
                                "py", [], [])
    tests_store.finish_test(t["id"], "test_generated.py", "NET='L_old'\n", [], None, "done",
                            hardware=[{"type": "wireless", "count": 1}],
                            gen_meta={"network_ids": ["L_old"]})
    test = tests_store.get_test(t["id"])
    run = runs_store.create_run(t["id"], "example", "L_example")

    job = Job(test_id=run["id"])

    docker_client = mock.Mock()
    docker_client.containers.run.return_value = _fake_container(exit_code=0)

    with mock.patch.object(np.meraki, "create_network",
                           return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory",
                           return_value=[_dev("Q2-A", "MR33", "wireless")]), \
         mock.patch.object(np.meraki, "claim_device") as claim, \
         mock.patch.object(np.meraki, "remove_device") as remove, \
         mock.patch.object(np.meraki, "delete_network") as delete, \
         mock.patch.object(runner_base, "_client", return_value=docker_client):
        assert orch.begin(run["id"]) is True
        assert orch.is_busy() is True
        orch.start_run(job, test, run["id"], run["run_code"], "O1",
                       "example", "L_example", "key")

    claim.assert_called_once_with("L_new", ["Q2-A"], "key")
    remove.assert_called_once_with("L_new", "Q2-A", "key")
    delete.assert_called_once_with("L_new", "key")
    assert orch.is_busy() is False  # released in finally

    # the fresh network + org reach the container through the environment, not baked
    # into the code — so the test runs against the network provisioned for this run
    container_env = docker_client.containers.run.call_args.kwargs["environment"]
    assert container_env["MERAKI_NETWORK_ID"] == "L_new"
    assert container_env["MERAKI_ORG_ID"] == "O1"
    assert container_env["MERAKI_API_KEY"] == "key"
    # the device serial reaches the code as a substituted {{DEVICE_SERIAL_N}} token,
    # not through the environment
    assert "MERAKI_DEVICE_SERIAL" not in container_env

    events = job._events
    assert events[0]["type"] == "status" and events[0]["status"] == "provisioning"
    assert any(e["type"] == "status" and e["status"] == "running" for e in events)
    assert any(e["type"] == "status" and e["status"] == "success" for e in events)
    assert events[-1]["type"] == "done"  # teardown logs (if any) precede it

    persisted = runs_store.get_run(run["id"])
    assert persisted["status"] == "success" and persisted["network_id"] == "L_new"


def test_token_resolved_into_container_code():
    """The type-only path end-to-end: the stored test carries {{DEVICE_SERIAL_1}} (no
    device existed when it was written) and the code handed to the container carries the
    serial of the device this run actually claimed."""
    orch._release()
    code = 'SERIAL = "{{DEVICE_SERIAL_1}}"\n'
    t = tests_store.create_test("T", "prompt", "test_generated.py", code, "py", [], [])
    tests_store.finish_test(t["id"], "test_generated.py", code, [], None, "done",
                            hardware=[{"type": "wireless", "count": 1}], gen_meta={})
    test = tests_store.get_test(t["id"])
    run = runs_store.create_run(t["id"], "example", "L_example")
    job = Job(test_id=run["id"])

    docker_client = mock.Mock()
    docker_client.containers.run.return_value = _fake_container(exit_code=0)
    with mock.patch.object(np.meraki, "create_network",
                           return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory",
                           return_value=[_dev("Q2-REAL", "MR33", "wireless")]), \
         mock.patch.object(np.meraki, "claim_device"), \
         mock.patch.object(np.meraki, "remove_device"), \
         mock.patch.object(np.meraki, "delete_network"), \
         mock.patch.object(runner_base, "_client", return_value=docker_client):
        assert orch.begin(run["id"]) is True
        orch.start_run(job, test, run["id"], run["run_code"], "O1",
                       "example", "L_example", "key")

    sent = docker_client.containers.run.call_args.kwargs
    written = job._events  # the run reached the container at all
    assert any(e.get("status") == "success" for e in written if e["type"] == "status")
    assert runs_store.get_run(run["id"])["status"] == "success"
    # no token may survive into the executed code
    assert "DEVICE_SERIAL" not in str(sent)


def test_unresolved_token_fails_the_run():
    """A token with no device behind it must stop the run. Executing it would send a
    literal '{{DEVICE_SERIAL_2}}' in the URL and 404 — the silent-wrong-serial failure
    the token contract exists to eliminate."""
    orch._release()
    code = 'A = "{{DEVICE_SERIAL_1}}"\nB = "{{DEVICE_SERIAL_2}}"\n'   # asks for 2 devices
    t = tests_store.create_test("T", "prompt", "test_generated.py", code, "py", [], [])
    tests_store.finish_test(t["id"], "test_generated.py", code, [], None, "done",
                            hardware=[{"type": "wireless", "count": 1}],  # ...only 1 provided
                            gen_meta={})
    test = tests_store.get_test(t["id"])
    run = runs_store.create_run(t["id"], "example", "L_example")
    job = Job(test_id=run["id"])

    docker_client = mock.Mock()
    with mock.patch.object(np.meraki, "create_network",
                           return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory",
                           return_value=[_dev("Q2-REAL", "MR33", "wireless")]), \
         mock.patch.object(np.meraki, "claim_device"), \
         mock.patch.object(np.meraki, "remove_device"), \
         mock.patch.object(np.meraki, "delete_network"), \
         mock.patch.object(runner_base, "_client", return_value=docker_client):
        assert orch.begin(run["id"]) is True
        orch.start_run(job, test, run["id"], run["run_code"], "O1",
                       "example", "L_example", "key")

    docker_client.containers.run.assert_not_called()   # never executed
    persisted = runs_store.get_run(run["id"])
    assert persisted["status"] == "error"
    assert "DEVICE_SERIAL_2" in persisted["error_message"]


def test_second_concurrent_run_rejected():
    orch._release()
    assert orch.begin(100) is True
    assert orch.begin(101) is False   # guard rejects while 100 is active
    orch._release()
    assert orch.begin(101) is True
    orch._release()
