"""Framework-agnostic app orchestration shared by any front-end.

These helpers used to live in the web routers (app_view.py); they were lifted into
the services layer verbatim so the TUI can call them without importing anything from
``web.routers`` (which no longer exists). Every function here calls only other
services — no request/response objects, no templates.
"""

from web import config
from web.services import (
    logs_store, meraki, network_provision, runs_store, run_logs_store, store,
    tests_store,
)

_HW_LABELS = {"wireless": "Wireless AP", "security_appliance": "Security appliance",
              "camera": "Camera"}


def gen_meta(user_id: int, devices: list) -> dict:
    """Concrete identifiers to inject into the generated test (issue #9).

    @-mentioned devices are unclaimed inventory, so they carry an org but no network —
    the network a test targets doesn't exist until a run provisions it. We bake in the
    account's default example network (the one a run clones) so the model emits a real
    id instead of a YOUR_NETWORK_ID placeholder, and the Run feature substitutes it for
    the ephemeral network's id at run time (services.runners.inject)."""
    org_ids = [d["orgId"] for d in devices if isinstance(d, dict) and d.get("orgId")]
    org_id = org_ids[0] if org_ids else store.org_id_for_network(user_id, "")
    net_id = store.default_or_first_network_id(user_id, org_id)
    return {"base_url": None, "org_id": org_id,
            "network_ids": [net_id] if net_id else []}


def claimable_devices(user_id: int):
    """Unclaimed inventory across the user's orgs, for the Hardware picker — the only
    devices a run can actually claim. Returns (devices, error): a Meraki failure yields
    an error string rather than an empty list, so the picker never implies "you have no
    hardware" when it simply couldn't ask."""
    key = store.get_meraki_key(user_id)
    if not key:
        return [], "Add a Meraki API key in Settings to pick specific hardware."
    devices, errors = [], []
    for org in store.list_orgs(user_id):
        try:
            inv = meraki.list_org_inventory(org["id"], key, unclaimed_only=True)
        except meraki.MerakiError as exc:
            errors.append(f"{org.get('name') or org['id']}: {exc}")
            continue
        for d in inv:
            hw_type = network_provision.hardware_type_for_model(d.get("model", ""))
            devices.append({
                "serial": d["serial"], "model": d.get("model", ""), "name": d.get("name", ""),
                "label": f"{_HW_LABELS.get(hw_type, 'Device')} {d.get('model', '')} · {d['serial']}",
            })
    return devices, "; ".join(errors)


def parse_hardware_rows(user_id: int, rows: list) -> list:
    """Turn hardware-picker rows (``serial:<serial>`` | ``type:<hw_type>``) into stored
    hardware requirements. One row is one device; a pinned row's model/type are resolved
    from live inventory rather than trusted from the form."""
    known = {d["serial"]: d for d in claimable_devices(user_id)[0]}
    hardware = []
    for value in rows or []:
        kind, _, rest = (str(value) or "").partition(":")
        if kind == "serial" and rest:
            dev = known.get(rest, {})
            hardware.append({
                "type": network_provision.hardware_type_for_model(dev.get("model", "")),
                "count": 1, "serial": rest, "model": dev.get("model", ""),
                "name": dev.get("name", ""), "reason": "pinned to a specific device",
            })
        elif kind == "type" and rest in _HW_LABELS:
            hardware.append({"type": rest, "count": 1, "reason": ""})
    return hardware


def output_ctx(user_id: int, run: dict) -> dict:
    """Output context for one run: the run, its logs, and its code version's output-nav
    (browse "run i of N" within the version). ``run`` None is the empty state."""
    if not run:
        return {"run": None, "logs": [], "version_runs": [], "run_total": 0,
                "run_index": -1, "prev_run_id": None, "next_run_id": None, "latest_run_id": None}
    ctx = {"run": run, "logs": run_logs_store.logs_for_run(run["id"])}
    ctx.update(runs_store.output_nav(user_id, run["test_id"], run["version_no"], run["id"]))
    return ctx


def run_context(user_id: int, test: dict, version_no: int = None) -> dict:
    """Run-configuration context for the Test Configuration + Output tabs: the account's
    networks + default, the hardware it can pin to, this test's saved run config, and —
    scoped to the code version being viewed — that version's latest run and output-nav.

    ``version_no`` None means the latest version."""
    claimable, claimable_error = claimable_devices(user_id)
    ctx = {
        "networks": store.verified_networks(user_id),
        "default_network_id": store.get_default_network_id(user_id),
        "claimable": claimable,
        "claimable_error": claimable_error,
        "run_source": test.get("run_source", "example") if test else "example",
        "source_network_id": test.get("source_network_id", "") if test else "",
    }
    if not test:
        ctx.update(output_ctx(user_id, None))
        return ctx
    if version_no is None:
        version_no = max(len(tests_store.list_versions(user_id, test["id"])) - 1, 0)
    runs = runs_store.version_runs(user_id, test["id"], version_no)
    latest = runs_store.get_run(user_id, runs[-1]["id"]) if runs else None   # newest of this version
    ctx.update(output_ctx(user_id, latest))
    return ctx


def persist_generation_result(user_id: int, test_id: int, vm: dict, name: str,
                              language: str, is_new: bool, repair) -> str:
    """Persist a finished generation's view model (lifted from app_view._run_generation).

    Returns the resulting test status: "done" on success, "error" when the generation
    failed or produced no code. On success the test row is updated, a new version is
    snapshotted (#12), and the per-test log is recorded. On failure a brand-new test is
    dropped (no placeholder left behind), while a regenerate/repair keeps the existing
    test and its prior versions (only the in-flight placeholder is abandoned)."""
    ok = not vm.get("error") and not vm.get("empty")
    if ok:
        tests_store.finish_test(user_id, test_id, vm["file_name"], vm["code"],
                                vm["endpoints"], vm.get("validation"), "done",
                                hardware=vm.get("hardware"), gen_meta=vm.get("_gen_meta"),
                                dep_endpoints=vm.get("dep_endpoints"))
        tests_store.add_version(test_id, vm["prompt"], vm["file_name"], vm["code"],
                                language, vm["endpoints"], vm.get("validation"))
        logs_store.record(user_id, test_id, vm.get("_log"))
        return "done"
    if is_new:
        tests_store.delete_test(user_id, test_id)   # failed/empty: don't leave a placeholder
        return "error"
    # A regenerate/repair that fails must not take the existing test with it — deleting
    # here would destroy the stored code and every prior version.
    tests_store.abandon_generation(user_id, test_id)
    return "error"
