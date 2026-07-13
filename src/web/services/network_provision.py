"""Deterministic network provisioning + teardown for a test run (Run feature).

Every run gets its own ephemeral Meraki network — never an existing one. This module
owns the real Meraki API calls that create it (cloned from an example, or built from
scratch and configured by an agent), claim the physical hardware the test needs from
the org's unclaimed inventory, and — always — tear it all back down afterwards.

Hardware is never fabricated: if the org has no unclaimed device of a required type,
provisioning raises ``ProvisionError`` and the run is reported as ``error``.
"""

import logging
from dataclasses import dataclass, field

from web.services import meraki

logger = logging.getLogger("web.network_provision")

# hardware type -> Meraki productType (network create) + inventory model prefixes (claim)
_PRODUCT_TYPE = {"wireless": "wireless", "security_appliance": "appliance", "camera": "camera"}
_MODEL_PREFIXES = {"wireless": ("MR", "CW"), "security_appliance": ("MX", "Z"), "camera": ("MV",)}
_DEFAULT_PRODUCT_TYPES = ["appliance"]   # fallback when a scratch build declares no hardware


class ProvisionError(Exception):
    """Provisioning could not complete (e.g. no matching unclaimed hardware). The
    message is safe to show as the run's error."""


@dataclass
class ProvisionResult:
    network_id: str
    org_id: str
    claimed_devices: list = field(default_factory=list)  # [{serial, model, hardwareType}]


def _log(on_log, stage, message, level="info"):
    if on_log:
        on_log({"stage": stage, "level": level, "message": message})


def _product_types(hardware_reqs) -> list:
    types = []
    for req in hardware_reqs or []:
        pt = _PRODUCT_TYPE.get(req.get("type"))
        if pt and pt not in types:
            types.append(pt)
    return types or list(_DEFAULT_PRODUCT_TYPES)


def _matches(device, hw_type) -> bool:
    """Whether an inventory device satisfies a hardware requirement (by Meraki
    productType, else by model prefix)."""
    if device.get("productType") == _PRODUCT_TYPE.get(hw_type):
        return True
    model = (device.get("model") or "").upper()
    return model.startswith(_MODEL_PREFIXES.get(hw_type, ()))


def _claim_hardware(org_id, network_id, hardware_reqs, key, on_log, claimed) -> None:
    """Claim one unclaimed device per required unit, matched by type, appending each
    to ``claimed`` as it succeeds (so the caller can release partial progress if a
    later claim fails). Raises ProvisionError if the inventory can't satisfy a
    requirement (never fabricates)."""
    if not hardware_reqs:
        return
    inventory = meraki.list_org_inventory(org_id, key, unclaimed_only=True)
    available = list(inventory)
    for req in hardware_reqs:
        hw_type, count = req.get("type"), int(req.get("count", 1) or 1)
        picks = [d for d in available if _matches(d, hw_type)][:count]
        if len(picks) < count:
            raise ProvisionError(
                f"Not enough unclaimed {hw_type} hardware in the organization's inventory "
                f"(need {count}, found {len(picks)}). Add devices to inventory and retry."
            )
        for d in picks:
            available.remove(d)
        serials = [d["serial"] for d in picks]
        meraki.claim_device(network_id, serials, key)
        for d in picks:
            claimed.append({"serial": d["serial"], "model": d.get("model", ""), "hardwareType": hw_type})
        _log(on_log, "provision", f"claimed {count} {hw_type} device(s): {', '.join(serials)}")


def provision(user_id, run_code, org_id, hardware_reqs, source, example_network_id,
              key, on_log=None) -> ProvisionResult:
    """Create the run's ephemeral network and claim its hardware. On any failure after
    the network exists, best-effort tears down what was created before re-raising."""
    name = f"run-{run_code}"
    network_id = ""
    claimed = []
    try:
        if source == "example":
            if not example_network_id:
                raise ProvisionError("No example network selected (set one on the test or in Settings).")
            _log(on_log, "provision", f"cloning example network {example_network_id} -> {name}")
            net = meraki.create_network(org_id, name, _product_types(hardware_reqs), key,
                                        copy_from_network_id=example_network_id)
        else:
            _log(on_log, "provision", f"creating network {name} from scratch")
            net = meraki.create_network(org_id, name, _product_types(hardware_reqs), key)
        network_id = net["id"]

        _claim_hardware(org_id, network_id, hardware_reqs, key, on_log, claimed)

        if source == "scratch":
            from rag.graph.network_agent import configure_scratch_network
            _log(on_log, "provision", "agent configuring the new network…")
            configure_scratch_network(network_id, hardware_reqs, claimed, key,
                                      provider_overrides=None, on_log=on_log)
        return ProvisionResult(network_id=network_id, org_id=org_id, claimed_devices=claimed)
    except Exception as exc:
        # Undo any partial provisioning so we never leak an ephemeral network/device.
        if network_id:
            _log(on_log, "provision", f"provisioning failed, tearing down {network_id}", "error")
            teardown(org_id, network_id, claimed, key, on_log)
        raise


def teardown(org_id, network_id, claimed_devices, key, on_log=None) -> None:
    """Best-effort release of every claimed device and deletion of the network. Always
    safe to call (in a run's ``finally``); individual failures are logged, not raised."""
    for d in claimed_devices or []:
        try:
            meraki.remove_device(network_id, d["serial"], key)
        except Exception as exc:  # keep releasing the rest
            logger.warning("teardown: could not remove device %s: %s", d.get("serial"), exc)
            _log(on_log, "teardown", f"could not release {d.get('serial')}: {exc}", "error")
    if network_id:
        try:
            meraki.delete_network(network_id, key)
            _log(on_log, "teardown", f"deleted network {network_id}")
        except Exception as exc:
            logger.warning("teardown: could not delete network %s: %s", network_id, exc)
            _log(on_log, "teardown", f"could not delete network {network_id}: {exc}", "error")
