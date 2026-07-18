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
    # `row` is the 1-based hardware row this device satisfies — the index a
    # {{DEVICE_SERIAL_N}} token in the generated code resolves against.
    assert res.claimed_devices == [
        {"serial": "Q2-A", "model": "MR33", "hardwareType": "wireless", "row": 1}]


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


# --- hardware pinned to a specific device -----------------------------------------
# A hardware row can name one real device by serial (from the prompt's @-mentions, or
# picked in the Config tab). That serial is baked into the generated code, so claiming
# a different device of the same type would silently run an MR42's test on an MR16.

def _pin(serial, model, name=""):
    return {"type": np.hardware_type_for_model(model), "count": 1, "serial": serial,
            "model": model, "name": name, "reason": "referenced in the prompt"}


def test_pinned_device_is_claimed_by_serial_not_by_type():
    inv = _inventory(_dev("Q2-MR16", "MR16", "wireless"), _dev("Q2-MR42", "MR42", "wireless"))
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=inv), \
         mock.patch.object(np.meraki, "claim_device") as claim:
        res = np.provision(1, "12345678", "O1", [_pin("Q2-MR42", "MR42", "AP2")],
                           "example", "L_example", "key")
    # the MR16 sorts first: type-matching alone would have claimed the wrong AP
    claim.assert_called_once_with("L_new", ["Q2-MR42"], "key")
    assert res.claimed_devices == [
        {"serial": "Q2-MR42", "model": "MR42", "hardwareType": "wireless", "row": 1}]


def test_pinned_and_generic_rows_claim_distinct_devices():
    """A pinned MR42 plus 'any wireless AP' must claim two different devices."""
    inv = _inventory(_dev("Q2-MR16", "MR16", "wireless"), _dev("Q2-MR42", "MR42", "wireless"))
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=inv), \
         mock.patch.object(np.meraki, "claim_device"):
        res = np.provision(1, "12345678", "O1",
                           [_pin("Q2-MR42", "MR42"), {"type": "wireless", "count": 1}],
                           "example", "L_example", "key")
    assert [d["serial"] for d in res.claimed_devices] == ["Q2-MR42", "Q2-MR16"]


def test_pinned_device_not_unclaimed_errors_and_tears_down():
    """The reported case: the named AP sits in a network, so it isn't claimable. That
    must fail loudly rather than quietly substituting a different AP."""
    inv = _inventory(_dev("Q2-MR16", "MR16", "wireless"))   # the MR42 is not unclaimed
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}), \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=inv), \
         mock.patch.object(np.meraki, "claim_device"), \
         mock.patch.object(np.meraki, "delete_network") as delete:
        with pytest.raises(ProvisionError) as exc:
            np.provision(1, "12345678", "O1", [_pin("Q2-MR42", "MR42", "AP2")],
                         "example", "L_example", "key")
    assert "AP2" in str(exc.value) and "Q2-MR42" in str(exc.value)
    delete.assert_called_once_with("L_new", "key")


def test_network_product_types_follow_a_pinned_devices_model():
    """A network only holds devices whose product type it was created with, so a pinned
    AP must force a wireless network even though the row declares no generic type."""
    inv = _inventory(_dev("Q2-MR42", "MR42", "wireless"))
    with mock.patch.object(np.meraki, "create_network", return_value={"id": "L_new", "orgId": "O1", "name": "run-1"}) as create, \
         mock.patch.object(np.meraki, "list_org_inventory", return_value=inv), \
         mock.patch.object(np.meraki, "claim_device"):
        np.provision(1, "12345678", "O1", [_pin("Q2-MR42", "MR42")], "example", "L_example", "key")
    assert create.call_args[0][2] == ["wireless"]


def test_hardware_type_for_model():
    assert np.hardware_type_for_model("MR16") == "wireless"
    assert np.hardware_type_for_model("CW9164") == "wireless"
    assert np.hardware_type_for_model("MX75") == "security_appliance"
    assert np.hardware_type_for_model("Z4C") == "security_appliance"
    assert np.hardware_type_for_model("MV12N") == "camera"
    assert np.hardware_type_for_model("MS220-8P") == ""   # switches aren't a run type
    assert np.hardware_type_for_model("") == ""
