"""Path params the caller supplies must not be routed to a producer endpoint.

This pins the fix for the list-and-filter bug: the dependency block is a *structured,
specific* instruction ("call first: 1. GET /networks/{networkId}/devices"), so it beat
the prose rule telling the model not to look devices up. With `serial` supplied by the
{{DEVICE_SERIAL_N}} token, the graph must stop emitting that producer step — otherwise
the model dutifully fetches a device list and filters it for the device it was handed,
which is how fabricated serials and 404s got in.

Built on a synthetic graph: data/graph is generated locally, not committed.
"""

import json

import pytest

from rag.depgraph import SUPPLIED_PARAMS, DependencyGraph

_LISTER = "GET /networks/{networkId}/devices"
_TARGET = "GET /devices/{serial}/wireless/status"


@pytest.fixture
def graph(tmp_path):
    data = {
        "nodes": {
            _LISTER: {"summary": "List the devices in a network",
                      "params": [{"name": "networkId", "orphan": False}]},
            _TARGET: {"summary": "Return the SSID statuses of an access point",
                      "params": [{"name": "serial", "orphan": False}]},
        },
        # the edge that used to drag a device lister into every device-scoped test
        "edges": [{"producer": _LISTER, "consumer": _TARGET, "param": "serial", "rule": 2}],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(data))
    return DependencyGraph(path)


def test_serial_is_a_supplied_param():
    assert {"organizationId", "networkId", "serial"} <= SUPPLIED_PARAMS


def test_supplied_serial_has_no_producer(graph):
    """`serial` resolves as caller-supplied, not as something to go fetch."""
    assert graph._needs(_TARGET) == [("serial", None, True)]


def test_device_endpoint_pulls_in_no_lister(graph):
    """The rendered dependency block must not tell the model to call the lister first."""
    rendered = graph.render(_TARGET)
    assert _LISTER not in rendered
    assert graph.upstream(_TARGET) == [_TARGET]


def test_unsupplied_param_still_resolves_to_its_producer(tmp_path):
    """The supplied-param carve-out is narrow: ordinary params keep their producer, so
    genuine call-order dependencies still reach the model."""
    data = {
        "nodes": {
            "GET /networks/{networkId}/things": {"summary": "list",
                                                 "params": [{"name": "networkId", "orphan": False}]},
            "GET /things/{thingId}": {"summary": "get",
                                      "params": [{"name": "thingId", "orphan": False}]},
        },
        "edges": [{"producer": "GET /networks/{networkId}/things",
                   "consumer": "GET /things/{thingId}", "param": "thingId", "rule": 2}],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(data))
    g = DependencyGraph(path)
    assert g._needs("GET /things/{thingId}") == [
        ("thingId", "GET /networks/{networkId}/things", False)]
