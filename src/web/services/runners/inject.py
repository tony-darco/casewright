"""Rewrite a generated test's baked-in identifiers for one run (Run feature).

Generated code deliberately bakes literal org/network ids and device serials in (only
the API key is read from the environment — see rag.graph.prompts). A run executes
against a *fresh* ephemeral network with *different* serials, so just before running
we substitute the generation-time literals for this run's actual values. Org id and
base URL don't change between generation and run, so they're left alone.

Known MVP limitation: when a test references several devices of the *same* hardware
type, original->claimed serial pairing is order-based/best-effort, not guaranteed.
"""


def inject_run_values(code, gen_meta, orig_devices, claimed_devices, new_network_id) -> str:
    """Return ``code`` with generation-time network ids and device serials replaced by
    this run's ephemeral network id and claimed serials."""
    code = code or ""
    gen_meta = gen_meta or {}

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
