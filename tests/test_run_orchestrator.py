"""End-to-end orchestration test (Meraki + Docker mocked): a full run goes
queued -> provisioning -> running -> success/error, teardown always fires, and the
single-run guard rejects a second concurrent run.
"""

from unittest import mock

from web import db
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
    db.init()

    uid = db.create_user(f"orch-{id(object())}", "hash")["id"]
    t = tests_store.create_test(uid, "T", "prompt", "test_generated.py", "NET='L_old'\n",
                                "py", [], [])
    tests_store.finish_test(uid, t["id"], "test_generated.py", "NET='L_old'\n", [], None, "done",
                            hardware=[{"type": "wireless", "count": 1}],
                            gen_meta={"network_ids": ["L_old"]})
    test = tests_store.get_test(uid, t["id"])
    run = runs_store.create_run(uid, t["id"], "example", "L_example")

    job = Job(test_id=run["id"], user_id=uid)

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
        orch.start_run(job, uid, test, run["id"], run["run_code"], "O1",
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
    # the device serial is baked into the code (a pinned device), not passed by env
    assert "MERAKI_DEVICE_SERIAL" not in container_env

    events = job._events
    assert events[0]["type"] == "status" and events[0]["status"] == "provisioning"
    assert any(e["type"] == "status" and e["status"] == "running" for e in events)
    assert any(e["type"] == "status" and e["status"] == "success" for e in events)
    assert events[-1]["type"] == "done"  # teardown logs (if any) precede it

    persisted = runs_store.get_run(uid, run["id"])
    assert persisted["status"] == "success" and persisted["network_id"] == "L_new"


def test_second_concurrent_run_rejected():
    orch._release()
    assert orch.begin(100) is True
    assert orch.begin(101) is False   # guard rejects while 100 is active
    orch._release()
    assert orch.begin(101) is True
    orch._release()
