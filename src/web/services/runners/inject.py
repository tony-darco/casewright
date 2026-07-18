"""Rewrite a generated test's identifiers for one run (Run feature).

The org id, network id, and API key reach the generated code through environment
variables the runner sets (see rag.graph.prompts + run_orchestrator) — the network is a
*fresh* ephemeral network per run, so new code reads MERAKI_NETWORK_ID and carries no
network literal at all.

Device serials come through a third channel: a {{DEVICE_SERIAL_N}} token
(web.services.serial_tokens). Generation resolves the tokens whose device was already
pinned; the ones left belong to type-only hardware rows ("any wireless AP"), whose device
doesn't exist until this run claims it — so we resolve them here, mapping token index N
to the device claimed for hardware row N.

Two compatibility fallbacks cover tests generated before the token contract:
- a baked-in literal network id is swapped for this run's ephemeral network, so an older
  test never writes to the real network it was generated against;
- a baked-in literal serial is swapped for the claimed device of the same hardware type.
Newer code has neither literal, so both replaces are harmless no-ops there.

Known limitation of the *legacy* serial path only: with several devices of the same
hardware type, original->claimed pairing is order-based/best-effort. Token-based tests
don't have this problem — the row index makes the mapping explicit.
"""

from web.services import serial_tokens


def inject_run_values(code, gen_meta, orig_devices, claimed_devices, new_network_id) -> str:
    """Return ``code`` with device serials resolved for this run — {{DEVICE_SERIAL_N}}
    tokens filled from the claimed devices, plus the legacy literal swaps — and, for
    legacy literal-based tests, any baked-in network id swapped for this run's ephemeral
    network. New code reads the network id from the environment instead."""
    code = code or ""
    gen_meta = gen_meta or {}

    # Legacy fallback: swap a baked-in literal network id for this run's ephemeral one,
    # so an older test never writes to the real network it was generated against.
    for old_net_id in gen_meta.get("network_ids", []):
        if old_net_id and new_network_id:
            code = code.replace(old_net_id, new_network_id)

    # Resolve serial tokens from the claimed devices. Each claimed device records the
    # 1-based hardware row it satisfies (network_provision), which is the token's index.
    # Devices claimed before that field existed fall back to their position in the list.
    by_index = {}
    for pos, d in enumerate(claimed_devices or [], start=1):
        row = d.get("row") or pos
        if d.get("serial"):
            by_index.setdefault(int(row), d["serial"])
    code = serial_tokens.substitute(code, by_index)

    # Pair each originally-referenced device with a claimed device of the same hardware
    # type (best-effort, in order), then swap its serial.
    claimed_by_type = {}
    for d in claimed_devices or []:
        claimed_by_type.setdefault(d.get("hardwareType"), []).append(d.get("serial"))

    for orig in orig_devices or []:
        orig_serial = orig.get("serial")
        if not orig_serial:
            continue
        hw_type = _classify(orig.get("model", ""))
        pool = claimed_by_type.get(hw_type) or []
        if pool:
            new_serial = pool.pop(0)
            if new_serial:
                code = code.replace(orig_serial, new_serial)
    return code


# model prefix -> hardware type, mirroring network_provision's matching
_PREFIXES = (("MR", "wireless"), ("CW", "wireless"), ("MX", "security_appliance"),
             ("Z", "security_appliance"), ("MV", "camera"))


def _classify(model) -> str:
    model = (model or "").upper()
    for prefix, hw_type in _PREFIXES:
        if model.startswith(prefix):
            return hw_type
    return ""
