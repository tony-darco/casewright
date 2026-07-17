"""Rewrite a generated test's baked-in identifiers for one run (Run feature).

The org id, network id, and API key normally reach the generated code through
environment variables the runner sets (see rag.graph.prompts + run_orchestrator) — the
network is a *fresh* ephemeral network per run, so new code reads MERAKI_NETWORK_ID and
carries no network literal at all. Device serials are the one identifier still baked in
(a test @-mentions specific hardware), and a run claims *different* physical devices, so
just before running we swap each generation-time serial for the serial actually claimed.

The network-id string-replace below is a compatibility fallback for *legacy* tests
generated before env injection existed: they baked a literal network id in, and without
the swap their writes would hit that real network instead of this run's ephemeral one.
New code has no such literal, so the replace is a harmless no-op there.

Known MVP limitation: when a test references several devices of the *same* hardware
type, original->claimed serial pairing is order-based/best-effort, not guaranteed.
"""


def inject_run_values(code, gen_meta, orig_devices, claimed_devices, new_network_id) -> str:
    """Return ``code`` with device serials swapped for this run's claimed serials, and —
    for legacy literal-based tests — any baked-in network id swapped for this run's
    ephemeral network. New code reads the network id from the environment instead."""
    code = code or ""
    gen_meta = gen_meta or {}

    # Legacy fallback: swap a baked-in literal network id for this run's ephemeral one,
    # so an older test never writes to the real network it was generated against.
    for old_net_id in gen_meta.get("network_ids", []):
        if old_net_id and new_network_id:
            code = code.replace(old_net_id, new_network_id)

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
