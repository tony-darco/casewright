"""Network provisioning/teardown unit tests (meraki client mocked).

Covers: example-clone happy path, scratch build, the no-inventory failure that must
raise ProvisionError, and that a failure after the network exists still tears the
network (and any already-claimed devices) back down.
"""

from unittest import mock

import pytest

from web.services import network_provision as np
from web.services.network_provision import ProvisionError, ProvisionResult


def _inventory(*items):
    return list(items)


def _dev(serial, model, product_type):
    return {"serial": serial, "model": model, "productType": product_type, "name": serial,
            "mac": "", "networkId": "", "claimed": False}


def test_example_clone_claims_and_returns():
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}) as create, \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=_inventory(_dev("Q2-A", "MR33", "wireless"))) as inv, \
         mock.patch.object(np.meraki, "claim_device") as claim:
        res = np.provision(1, "12345678", "O1", [{"type": "wireless", "count": 1}],
                           "example", "L_example", "key")
    assert isinstance(res, ProvisionResult) and res.network_id == "L_new"
    assert create.call_args.kwargs["copy_from_network_id"] == "L_example"
    claim.assert_called_once_with("L_new", ["Q2-A"], "key")
    assert res.claimed_devices == [{"serial": "Q2-A", "model": "MR33", "hardwareType": "wireless"}]


def test_scratch_invokes_agent():
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=_inventory(_dev("Q2-X", "MX64", "appliance"))), \
         mock.patch.object(np.meraki, "claim_device"), \
         mock.patch("rag.graph.network_agent.configure_scratch_network") as agent:
        res = np.provision(1, "12345678", "O1", [{"type": "security_appliance", "count": 1}],
                           "scratch", "", "key")
    agent.assert_called_once()
    assert res.network_id == "L_new"


def test_no_matching_inventory_raises_and_tears_down():
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=_inventory(_dev("Q2-X", "MX64", "appliance"))), \
         mock.patch.object(np.meraki, "claim_device"), \
         mock.patch.object(np.meraki, "delete_network") as delete, \
         mock.patch.object(np.meraki, "remove_device"):
        with pytest.raises(ProvisionError):
            np.provision(1, "12345678", "O1", [{"type": "wireless", "count": 1}],
                         "example", "L_example", "key")
    # network was created then torn back down
    delete.assert_called_once_with("L_new", "key")


def test_partial_claim_failure_releases_prior_devices():
    # two requirements; the second claim raises after the first succeeded
    inv = _inventory(_dev("Q2-A", "MR33", "wireless"), _dev("Q2-X", "MX64", "appliance"))
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=inv), \
         mock.patch.object(np.meraki, "claim_device", side_effect=[None, np.meraki.MerakiError("boom")]), \
         mock.patch.object(np.meraki, "delete_network") as delete, \
         mock.patch.object(np.meraki, "remove_device") as remove:
        with pytest.raises(np.meraki.MerakiError):
            np.provision(1, "12345678", "O1",
                         [{"type": "wireless", "count": 1}, {"type": "security_appliance", "count": 1}],
                         "scratch", "", "key")
    remove.assert_called_once_with("L_new", "Q2-A", "key")  # first (claimed) device released
    delete.assert_called_once_with("L_new", "key")


def test_teardown_is_best_effort():
    # a remove failure must not stop the network delete
    with mock.patch.object(np.meraki, "remove_device", side_effect=np.meraki.MerakiError("x")), \
         mock.patch.object(np.meraki, "delete_network") as delete:
        np.teardown("O1", "L_new", [{"serial": "Q2-A"}], "key")
    delete.assert_called_once_with("L_new", "key")
