"""The product app (handoff G2/G3): the working UI, real generate call, and the
per-user test library (persisted).

``GET  /app``                        composer/workspace shell + the user's saved tests.
``POST /app/generate/start``         creates a 'generating' test and kicks off the
                                     background run (#11), returning its id + sidebar item.
``GET  /app/generate/{id}/stream``   attaches to a run (replay + live), or serves the
                                     persisted result once it's finished.
``GET  /app/tests/{id}``             loads a finished test back into the workspace.
``POST /app/tests/{id}/code``        persists edits made in the Code tab.
``POST /app/tests/{id}/rename``      renames a saved test (returns the updated item).
``GET  /runs``                       dashboard shell (empty state).
``GET  /coverage``                   the spec's endpoints as a tree, with per-user coverage.
``GET  /coverage/endpoint``          the tests using one endpoint (detail panel).
"""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from web.auth import require_user
from web.deps import templates
from web.services import (
    coverage, generate, gen_registry, kb_store, logs_store, meraki, network_provision,
    provider_store, run_logs_store, runs_store, store, tests_store,
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


def _default_name(prompt: str) -> str:
    p = " ".join((prompt or "").split())
    return (p[:60] + "…") if len(p) > 60 else (p or "Untitled test")


def _gen_meta(user_id: int, devices: list) -> dict:
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


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


_HW_LABELS = {"wireless": "Wireless AP", "security_appliance": "Security appliance",
              "camera": "Camera"}


def _claimable_devices(user_id: int):
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


def output_ctx(user_id: int, run: dict) -> dict:
    """Output-tab context for one run: the run, its logs, and its code version's
    output-nav (browse "run i of N" within the version). ``run`` None is the empty
    state. Shared with the runs router so a fresh run and a re-render match."""
    if not run:
        return {"run": None, "logs": [], "version_runs": [], "run_total": 0,
                "run_index": -1, "prev_run_id": None, "next_run_id": None, "latest_run_id": None}
    ctx = {"run": run, "logs": run_logs_store.logs_for_run(run["id"])}
    ctx.update(runs_store.output_nav(user_id, run["test_id"], run["version_no"], run["id"]))
    return ctx


def _run_context(user_id: int, test: dict, version_no: int = None) -> dict:
    """Run-configuration context for the Test Configuration + Output tabs: the account's
    networks + default, the hardware it can pin to, this test's saved run config, and —
    scoped to the code version being viewed — that version's latest run and output-nav.

    ``version_no`` None means the latest version. Each run is tagged with the code
    version it executed, so browsing an old version shows the outputs produced from it."""
    claimable, claimable_error = _claimable_devices(user_id)
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


@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, user: dict = Depends(require_user)):
    return templates.TemplateResponse(
        request, "app.html", {"tests": tests_store.list_tests(user["id"])}
    )


def _run_generation(job, uid, test_id, name, prompt, dev, devices_raw, language, meta, prov,
                    repair=None, endpoints=None, keep_hardware=None, is_new=True,
                    hardware=None):
    """Background worker (#11): run the pipeline, emit stream events onto ``job``, and
    persist the finished test (or drop the placeholder on failure). Runs off the
    request, so it survives the client navigating away or disconnecting.

    ``repair`` re-enters the pipeline with a failed run's code + output to fix it,
    grounded on ``endpoints`` (what the first pass retrieved) rather than retrieving
    again. ``keep_hardware`` is the test's existing hardware, preserved verbatim: a
    repair edits code, never the run config. ``hardware`` is the user's upfront hardware
    selection (from the composer/Prompt tab), which is authoritative over the model's
    guess."""
    for ev in generate.stream_events(prompt, dev, language, meta, prov, repair, endpoints,
                                     hardware=hardware):
        if ev["type"] != "final":
            job.emit(ev)                       # stage / token / error passthrough
            continue
        vm = ev["vm"]
        vm["devices"] = devices_raw            # echoed to the panel so regenerate reuses grounding
        if repair:
            vm["hardware"] = keep_hardware or []
        ok = not vm.get("error") and not vm.get("empty")
        if ok:
            tests_store.finish_test(uid, test_id, vm["file_name"], vm["code"],
                                    vm["endpoints"], vm.get("validation"), "done",
                                    hardware=vm.get("hardware"), gen_meta=meta,
                                    dep_endpoints=vm.get("dep_endpoints"))
            # snapshot this result as the next version (#12)
            n = tests_store.add_version(test_id, vm["prompt"], vm["file_name"], vm["code"],
                                        language, vm["endpoints"], vm.get("validation"))
            logs_store.record(uid, test_id, vm.get("_log"))
            vm["t"] = {"id": test_id, "name": name}
            vm["version_no"], vm["version_count"], vm["is_latest"] = n, n + 1, True
            vm.update(_run_context(uid, tests_store.get_test(uid, test_id)))
            item = templates.get_template("partials/test_item.html").render(
                {"t": {"id": test_id, "name": name, "status": "done"}})
        elif is_new:
            tests_store.delete_test(uid, test_id)   # failed/empty: don't leave a placeholder
            item = ""
        else:
            # A regenerate/repair that fails must not take the existing test with it —
            # deleting here would destroy the stored code and every prior version.
            tests_store.abandon_generation(uid, test_id)
            item = templates.get_template("partials/test_item.html").render(
                {"t": {"id": test_id, "name": name, "status": "done"}})
        panel = templates.get_template("partials/workspace.html").render(vm)
        job.emit({"type": "done", "panel_html": panel, "item_html": item,
                  "status": "done" if ok else "error", "test_id": test_id})


@router.post("/app/generate/start")
def app_generate_start(
    prompt: str = Form(""),
    devices: str = Form(""),
    language: str = Form("py"),
    regen_of: str = Form(""),
    hardware: str = Form(""),
    user: dict = Depends(require_user),
):
    """Kick off a generation in the background and return the (generating) test id plus
    its sidebar item. ``regen_of`` regenerates into an existing test as a new version
    (#12); otherwise a fresh test is created. ``hardware`` is the user's upfront picker
    selection (a JSON list of ``serial:``/``type:`` rows), authoritative over the model's
    guess. The client then attaches to the stream."""
    uid = user["id"]
    dev = generate.parse_devices(devices)
    user_hardware = _parse_hardware_rows(uid, generate.parse_devices(hardware))
    # Resolve plain-text @-mentions the composer didn't send as chips (e.g. a bare
    # "@MR42"): look them up in the org's claimable inventory so the test gets a real
    # serial instead of the model name. Best-effort — skip if inventory is unavailable.
    if generate.has_unresolved_mentions(prompt, dev):
        inventory, _ = _claimable_devices(uid)
        dev = generate.resolve_prompt_mentions(prompt, dev, inventory)
        devices = json.dumps(dev)   # persist the resolved set (regenerate/repair reuse it)
    name = _default_name(prompt)
    meta = _gen_meta(uid, dev)
    prov = provider_store.overrides(uid)
    active_kb = kb_store.get_active(uid)   # only ever a 'done' version (see kb_store.get_active)
    if active_kb:
        prov = {**prov, "collection_name": active_kb["collection_name"]}
        storage = kb_store.get_storage(uid)
        if storage["storage_kind"] == "remote" and storage["storage_url"]:
            prov["chroma_url"] = storage["storage_url"]

    t = None
    if regen_of.strip().isdigit():
        t = tests_store.restart_generation(uid, int(regen_of), prompt, language)
    is_new = t is None
    if is_new:                                       # new test (or regen target not found)
        t = tests_store.create_generating(uid, name, prompt, language, dev)
    test_id, name = t["id"], t["name"]

    gen_registry.start(test_id, uid, lambda job: _run_generation(
        job, uid, test_id, name, prompt, dev, devices, language, meta, prov, is_new=is_new,
        hardware=user_hardware))

    item = templates.get_template("partials/test_item.html").render({"t": t})
    return JSONResponse({"test_id": test_id, "item_html": item})


@router.post("/app/tests/{test_id}/repair/start")
def app_repair_start(test_id: int, user: dict = Depends(require_user)):
    """Send a failed run back through the pipeline to fix the code.

    The payoff of keeping everything: the prompt, the endpoints the first pass grounded
    on, the code, and the run's own output all still exist, so a repair re-enters at the
    generate step with the full picture and lands as a new version — the prior code stays
    reachable if the fix is worse. Retrieval is skipped (the endpoints are already known)
    and the hardware config is carried over untouched."""
    uid = user["id"]
    test = tests_store.get_test(uid, test_id)
    if not test:
        return JSONResponse({"error": "Test not found."}, status_code=404)

    runs = runs_store.list_runs_for_test(uid, test_id)
    run = runs_store.get_run(uid, runs[0]["id"]) if runs else None
    if not run or run["status"] not in ("failed", "error"):
        return JSONResponse({"error": "There's no failed run to learn from. Run the test first."},
                            status_code=400)

    repair = generate.repair_context(test, run, run_logs_store.logs_for_run(run["id"]))
    dev = generate.parse_devices(test.get("devices_json"))
    endpoints = generate.parse_devices(test.get("endpoints_json"))   # forgiving JSON list parse
    keep_hardware = generate.parse_devices(test.get("hardware_json"))
    prompt, language = test.get("prompt", ""), test.get("language") or "py"

    prov = provider_store.overrides(uid)
    active_kb = kb_store.get_active(uid)
    if active_kb:
        prov = {**prov, "collection_name": active_kb["collection_name"]}
        storage = kb_store.get_storage(uid)
        if storage["storage_kind"] == "remote" and storage["storage_url"]:
            prov["chroma_url"] = storage["storage_url"]

    t = tests_store.restart_generation(uid, test_id, prompt, language)
    if t is None:
        return JSONResponse({"error": "Test not found."}, status_code=404)
    name = t["name"]
    meta = _gen_meta(uid, dev)

    gen_registry.start(test_id, uid, lambda job: _run_generation(
        job, uid, test_id, name, prompt, dev, test.get("devices_json") or "[]", language,
        meta, prov, repair=repair, endpoints=endpoints, keep_hardware=keep_hardware,
        is_new=False))

    item = templates.get_template("partials/test_item.html").render({"t": t})
    return JSONResponse({"test_id": test_id, "item_html": item})


@router.get("/app/generate/{test_id}/stream")
def app_generate_attach(test_id: int, user: dict = Depends(require_user)):
    """Attach to a running generation (replay so far + live), or serve the persisted
    result if the job already finished. Lets a returning client see progress (#11)."""
    uid = user["id"]
    job = gen_registry.get(test_id, uid)
    if job is not None:
        return StreamingResponse((_sse(ev) for ev in job.subscribe()),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)

    test = tests_store.get_test(uid, test_id)

    def fallback():
        if not test:
            yield _sse({"type": "error", "message": "Generation not found."})
            panel = "<div class='panel'><div class='panel-body'><div class='gen-error'>" \
                    "<b>&#10007; Not found.</b></div></div></div>"
            yield _sse({"type": "done", "panel_html": panel, "status": "error", "test_id": test_id})
        elif test.get("status") == "generating":
            # job gone but row still generating -> the server was restarted mid-run
            msg = "This generation was interrupted (the server restarted). Regenerate to try again."
            yield _sse({"type": "error", "message": msg})
            panel = "<div class='panel'><div class='panel-body'><div class='gen-error'>" \
                    f"<b>&#10007; Generation interrupted.</b><div class='muted'>{msg}</div></div></div></div>"
            yield _sse({"type": "done", "panel_html": panel, "status": "error", "test_id": test_id})
        else:
            vm = generate.view_model_from_test(test)
            vm.update(_run_context(uid, test))
            panel = templates.get_template("partials/workspace.html").render(vm)
            yield _sse({"type": "done", "panel_html": panel, "status": test.get("status"), "test_id": test_id})

    return StreamingResponse(fallback(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _with_version_nav(vm, user_id, test_id):
    """Add version-history fields (#12) to a latest-version view model."""
    count = len(tests_store.list_versions(user_id, test_id))
    vm["version_count"] = count
    vm["version_no"] = count - 1        # the tests row mirrors the latest version
    vm["is_latest"] = True
    return vm


@router.get("/app/tests/{test_id}", response_class=HTMLResponse)
def load_test(request: Request, test_id: int, user: dict = Depends(require_user)):
    test = tests_store.get_test(user["id"], test_id)
    if not test:
        return HTMLResponse("<div class='gen-error'>Test not found.</div>", status_code=404)
    vm = _with_version_nav(generate.view_model_from_test(test), user["id"], test_id)
    vm.update(_run_context(user["id"], test))
    return templates.TemplateResponse(request, "partials/workspace.html", vm)


def _parse_hardware_rows(user_id: int, rows: list) -> list:
    """Turn hardware-picker rows (``serial:<serial>`` | ``type:<hw_type>``) into stored
    hardware requirements. One row is one device; a pinned row's model/type are resolved
    from live inventory rather than trusted from the form. Shared by the Test
    Configuration save and the upfront composer/Prompt-tab picker."""
    known = {d["serial"]: d for d in _claimable_devices(user_id)[0]}
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


@router.post("/app/tests/{test_id}/hardware", response_class=HTMLResponse)
def save_hardware(test_id: int, hwDevice: list[str] = Form(default=[]),
                  user: dict = Depends(require_user)):
    """Persist the user's edited hardware requirements from the Test Configuration tab.

    One row is one device: either ``serial:<serial>`` (pinned to a specific device) or
    ``type:<hw_type>`` (any device of that type). Two APs means two rows. A pinned row's
    model/type are resolved from inventory rather than trusted from the form."""
    hardware = _parse_hardware_rows(user["id"], hwDevice)
    ok = tests_store.update_hardware(user["id"], test_id, hardware)
    if not ok:
        return HTMLResponse("", status_code=404)
    return HTMLResponse("<b>&#10003; Saved.</b>")


@router.get("/app/tests/{test_id}/versions/{version_no}", response_class=HTMLResponse)
def load_version(request: Request, test_id: int, version_no: int, user: dict = Depends(require_user)):
    """Load a historical version of a test (#12), read-only unless it's the latest."""
    version = tests_store.get_version(user["id"], test_id, version_no)
    if not version:
        return HTMLResponse("<div class='gen-error'>Version not found.</div>", status_code=404)
    test = tests_store.get_test(user["id"], test_id)
    count = len(tests_store.list_versions(user["id"], test_id))
    vm = generate.view_model_from_version(version, test_id, test["name"] if test else "", count)
    vm.update(_run_context(user["id"], test, version_no))
    return templates.TemplateResponse(request, "partials/workspace.html", vm)


@router.post("/app/tests/{test_id}/code")
def save_test_code(test_id: int, code: str = Form(""), user: dict = Depends(require_user)):
    """Persist edits made in the Code tab. 204 on success, 404 if not the user's test."""
    ok = tests_store.update_code(user["id"], test_id, code)
    return HTMLResponse("", status_code=204 if ok else 404)


@router.post("/app/tests/{test_id}/rename", response_class=HTMLResponse)
def rename_test(request: Request, test_id: int, name: str = Form(""), user: dict = Depends(require_user)):
    test = tests_store.rename_test(user["id"], test_id, name)
    if not test:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "partials/test_item.html", {"t": test})


@router.delete("/app/tests/{test_id}")
def delete_test(test_id: int, user: dict = Depends(require_user)):
    """Permanently delete a test. Its versions, runs, and logs cascade via foreign keys
    (see web.db). 204 on success, 404 if it isn't the user's. The client removes the
    sidebar item and resets the workspace if the test was open."""
    ok = tests_store.delete_test(user["id"], test_id)
    return HTMLResponse("", status_code=204 if ok else 404)


@router.get("/runs", response_class=HTMLResponse)
def runs_dashboard(request: Request):
    return templates.TemplateResponse(request, "runs.html")


@router.get("/coverage", response_class=HTMLResponse)
def coverage_matrix(request: Request, user: dict = Depends(require_user)):
    return templates.TemplateResponse(request, "coverage.html", coverage.tree_view(user["id"]))


@router.get("/coverage/endpoint", response_class=HTMLResponse)
def coverage_endpoint(request: Request, ep: str, user: dict = Depends(require_user)):
    """The tests using one endpoint. ``ep`` is a query param, not a path segment:
    endpoint ids are "METHOD /path" and carry their own slashes and braces."""
    vm = coverage.endpoint_detail(user["id"], ep)
    if vm is None:
        return HTMLResponse("<div class='cov-empty'>Unknown endpoint.</div>", status_code=404)
    return templates.TemplateResponse(request, "partials/coverage_detail.html", vm)
