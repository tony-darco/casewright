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
    """Product types the ephemeral network must support — a network can only hold a
    device whose product type it was created with. A pinned row's model is authoritative
    over its declared type."""
    types = []
    for req in hardware_reqs or []:
        hw_type = hardware_type_for_model(req.get("model", "")) if req.get("serial") else req.get("type")
        pt = _PRODUCT_TYPE.get(hw_type)
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


def hardware_type_for_model(model) -> str:
    """Hardware type for a Meraki model (e.g. MR16 -> 'wireless'). Shared so a pinned
    device and a generic requirement agree on what type a device satisfies."""
    model = (model or "").upper()
    for hw_type, prefixes in _MODEL_PREFIXES.items():
        if model.startswith(prefixes):
            return hw_type
    return ""


def _claim_pinned(available, req, network_id, key, on_log, claimed, row) -> None:
    """Claim one specific device by serial (a hardware row pinned to real hardware).

    A pinned serial is the one baked into the generated code, so substituting another
    device of the same type would silently run an MR42's test on an MR16. If it isn't
    claimable, that's an error — never something to paper over.

    ``row`` is the 1-based hardware-row index this device satisfies, recorded so the
    generated code's {{DEVICE_SERIAL_row}} token resolves to exactly this device."""
    serial = (req.get("serial") or "").strip()
    match = next((d for d in available if d.get("serial") == serial), None)
    if match is None:
        raise ProvisionError(
            f"{req.get('name') or serial} ({req.get('model') or 'unknown model'}, {serial}) "
            f"is not available to claim — it's assigned to a network, or not in this "
            f"organization's inventory. Free it up, or pick different hardware for this test."
        )
    available.remove(match)
    meraki.claim_device(network_id, [serial], key)
    claimed.append({"serial": serial, "model": match.get("model", ""),
                    "hardwareType": hardware_type_for_model(match.get("model", "")),
                    "row": row})
    _log(on_log, "provision", f"claimed {match.get('model', '')} {serial} (pinned by this test)")


def _claim_hardware(org_id, network_id, hardware_reqs, key, on_log, claimed) -> None:
    """Claim this run's hardware, appending each success to ``claimed`` so the caller can
    release partial progress if a later claim fails.

    A requirement either names a specific device (``serial``) — claimed exactly — or is
    generic (``type`` + ``count``) and filled from whatever unclaimed inventory is left.
    Raises ProvisionError if the inventory can't satisfy a requirement (never
    fabricates).

    Each claimed device records the 1-based ``row`` of the requirement it satisfies. The
    claim order here is pinned-first (deliberately — see below), which is NOT the row
    order, so the index has to be carried explicitly rather than inferred from position:
    it's what maps a {{DEVICE_SERIAL_N}} token in the code to the right device."""
    if not hardware_reqs:
        return
    available = list(meraki.list_org_inventory(org_id, key, unclaimed_only=True))

    # pinned first: they're specific, so they must not lose a device to a generic row
    for row, req in enumerate(hardware_reqs, start=1):
        if req.get("serial"):
            _claim_pinned(available, req, network_id, key, on_log, claimed, row)

    for row, req in enumerate(hardware_reqs, start=1):
        if req.get("serial"):
            continue
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
            claimed.append({"serial": d["serial"], "model": d.get("model", ""),
                            "hardwareType": hw_type, "row": row})
        _log(on_log, "provision", f"claimed {count} {hw_type} device(s): {', '.join(serials)}")


def provision(user_id, run_code, org_id, hardware_reqs, source, example_network_id,
              key, on_log=None) -> ProvisionResult:
    """Create the run's ephemeral network and claim the hardware the test asks for. On
    any failure after the network exists, best-effort tears down what was created before
    re-raising.

    Cloning an example network copies its *configuration* only; devices stay in the
    source network, so every run claims its hardware here regardless of ``source``."""
    name = f"run-{run_code}"
    network_id = ""
    claimed = []
    product_types = _product_types(hardware_reqs)
    try:
        if source == "example":
            if not example_network_id:
                raise ProvisionError("No example network selected (set one on the test or in Settings).")
            _log(on_log, "provision", f"cloning example network {example_network_id} -> {name}")
            net = meraki.create_network(org_id, name, product_types, key,
                                        copy_from_network_id=example_network_id)
        else:
            _log(on_log, "provision", f"creating network {name} from scratch")
            net = meraki.create_network(org_id, name, product_types, key)
        network_id = net["id"]

        _claim_hardware(org_id, network_id, hardware_reqs, key, on_log, claimed)

        if source == "scratch":
            from rag.graph.network_agent import configure_scratch_network
            _log(on_log, "provision", "agent configuring the new network…")
            configure_scratch_network(network_id, hardware_reqs, claimed, key,
                                      provider_overrides=None, on_log=on_log)
        return ProvisionResult(network_id=network_id, org_id=org_id, claimed_devices=claimed)
    except Exception:
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
