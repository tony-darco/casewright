"""Unit tests for the Run-feature Meraki client methods.

No live calls: ``requests.request`` is mocked so we assert the HTTP method, URL,
and body shape the client sends, and the mapped shape it returns.
"""

from unittest import mock

from web.services import meraki


def _resp(status=200, json_body=None, content=b"{}"):
    r = mock.Mock()
    r.status_code = status
    r.content = content
    r.json.return_value = json_body if json_body is not None else {}
    return r


def _capture():
    """Patch requests.request, returning (mock, calls-list)."""
    return mock.patch("web.services.meraki.requests.request")


def test_list_networks_maps_shape():
    with _capture() as req:
        req.return_value = _resp(json_body=[
            {"id": "L_1", "organizationId": "O_1", "name": "Net A", "url": "u"},
        ])
        nets = meraki.list_networks("O_1", "key")
    method, url = req.call_args.args
    assert method == "GET"
    assert url.endswith("/organizations/O_1/networks")
    assert nets == [{"id": "L_1", "orgId": "O_1", "name": "Net A", "url": "u"}]


def test_list_org_inventory_unclaimed_filter_and_claimed_flag():
    with _capture() as req:
        req.return_value = _resp(json_body=[
            {"serial": "Q2-A", "model": "MR33", "productType": "wireless", "networkId": None},
            {"serial": "Q2-B", "model": "MX64", "productType": "appliance", "networkId": "L_9"},
        ])
        inv = meraki.list_org_inventory("O_1", "key")
    _, url = req.call_args.args
    assert url.endswith("/organizations/O_1/inventoryDevices?usedState=unused")
    assert inv[0]["claimed"] is False and inv[0]["networkId"] == ""
    assert inv[1]["claimed"] is True and inv[1]["networkId"] == "L_9"


def test_create_network_includes_copy_from_when_cloning():
    with _capture() as req:
        req.return_value = _resp(json_body={"id": "L_new", "organizationId": "O_1", "name": "run-12345678"})
        net = meraki.create_network("O_1", "run-12345678", ["wireless"], "key",
                                    copy_from_network_id="L_example")
    method, url = req.call_args.args
    body = req.call_args.kwargs["json"]
    assert method == "POST" and url.endswith("/organizations/O_1/networks")
    assert body == {"name": "run-12345678", "productTypes": ["wireless"],
                    "copyFromNetworkId": "L_example"}
    assert net["id"] == "L_new"


def test_create_network_scratch_omits_copy_from():
    with _capture() as req:
        req.return_value = _resp(json_body={"id": "L_new", "organizationId": "O_1", "name": "x"})
        meraki.create_network("O_1", "x", ["appliance"], "key")
    body = req.call_args.kwargs["json"]
    assert "copyFromNetworkId" not in body


def test_delete_network_tolerates_empty_204():
    with _capture() as req:
        req.return_value = _resp(status=204, content=b"")
        assert meraki.delete_network("L_1", "key") is None
    method, url = req.call_args.args
    assert method == "DELETE" and url.endswith("/networks/L_1")


def test_claim_and_remove_device_bodies():
    with _capture() as req:
        req.return_value = _resp(json_body={"serials": ["Q2-A"]})
        meraki.claim_device("L_1", ["Q2-A"], "key")
    method, url = req.call_args.args
    assert method == "POST" and url.endswith("/networks/L_1/devices/claim")
    assert req.call_args.kwargs["json"] == {"serials": ["Q2-A"]}

    with _capture() as req:
        req.return_value = _resp(status=204, content=b"")
        meraki.remove_device("L_1", "Q2-A", "key")
    _, url = req.call_args.args
    assert url.endswith("/networks/L_1/devices/remove")
    assert req.call_args.kwargs["json"] == {"serial": "Q2-A"}


def test_config_writes_use_expected_verbs_and_paths():
    with _capture() as req:
        req.return_value = _resp(json_body={})
        meraki.update_ssid("L_1", 0, {"name": "test"}, "key")
        assert req.call_args.args[0] == "PUT"
        assert req.call_args.args[1].endswith("/networks/L_1/wireless/ssids/0")

        meraki.create_appliance_vlan("L_1", {"id": "10"}, "key")
        assert req.call_args.args[0] == "POST"
        assert req.call_args.args[1].endswith("/networks/L_1/appliance/vlans")

        meraki.update_appliance_firewall_rules("L_1", [{"policy": "deny"}], "key")
        assert req.call_args.args[0] == "PUT"
        assert req.call_args.kwargs["json"] == {"rules": [{"policy": "deny"}]}

        meraki.update_camera_quality_retention("L_1", {"name": "hi"}, "key")
        assert req.call_args.args[1].endswith("/networks/L_1/camera/qualityRetentionProfiles")
