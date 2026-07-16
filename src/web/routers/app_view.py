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
``GET  /runs`` / ``/coverage``       dashboard shells (empty states).
"""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from web.auth import require_user
from web.deps import templates
from web.services import (
    generate, gen_registry, kb_store, logs_store, provider_store, run_logs_store,
    runs_store, store, tests_store,
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


def _default_name(prompt: str) -> str:
    p = " ".join((prompt or "").split())
    return (p[:60] + "…") if len(p) > 60 else (p or "Untitled test")


def _gen_meta(user_id: int, devices: list) -> dict:
    """Concrete identifiers to inject into the generated test (issue #9): base URL and
    the org that owns the first referenced device's network. The Run feature also
    persists this (plus the referenced network ids) so the runner can substitute the
    literals baked into the code for each run's ephemeral network/serials."""
    net_ids = [d["networkId"] for d in devices
               if isinstance(d, dict) and d.get("networkId")]
    return {"base_url": None, "org_id": store.org_id_for_network(user_id, net_ids[0] if net_ids else ""),
            "network_ids": list(dict.fromkeys(net_ids))}


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _run_context(user_id: int, test: dict) -> dict:
    """Run-configuration context for the Test Configuration tab: the account's networks
    + default, this test's saved run config, and its most recent run (for status/logs)."""
    runs = runs_store.list_runs_for_test(user_id, test["id"]) if test else []
    latest = runs_store.get_run(user_id, runs[0]["id"]) if runs else None
    return {
        "networks": store.verified_networks(user_id),
        "default_network_id": store.get_default_network_id(user_id),
        "run_source": test.get("run_source", "example") if test else "example",
        "source_network_id": test.get("source_network_id", "") if test else "",
        "run": latest,
        "logs": run_logs_store.logs_for_run(latest["id"]) if latest else [],
    }


@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, user: dict = Depends(require_user)):
    return templates.TemplateResponse(
        request, "app.html", {"tests": tests_store.list_tests(user["id"])}
    )


def _run_generation(job, uid, test_id, name, prompt, dev, devices_raw, language, meta, prov):
    """Background worker (#11): run the pipeline, emit stream events onto ``job``, and
    persist the finished test (or drop the placeholder on failure). Runs off the
    request, so it survives the client navigating away or disconnecting."""
    for ev in generate.stream_events(prompt, dev, language, meta, prov):
        if ev["type"] != "final":
            job.emit(ev)                       # stage / token / error passthrough
            continue
        vm = ev["vm"]
        vm["devices"] = devices_raw            # echoed to the panel so regenerate reuses grounding
        ok = not vm.get("error") and not vm.get("empty")
        if ok:
            tests_store.finish_test(uid, test_id, vm["file_name"], vm["code"],
                                    vm["endpoints"], vm.get("validation"), "done",
                                    hardware=vm.get("hardware"), gen_meta=meta)
            # snapshot this result as the next version (#12)
            n = tests_store.add_version(test_id, vm["prompt"], vm["file_name"], vm["code"],
                                        language, vm["endpoints"], vm.get("validation"))
            logs_store.record(uid, test_id, vm.get("_log"))
            vm["t"] = {"id": test_id, "name": name}
            vm["version_no"], vm["version_count"], vm["is_latest"] = n, n + 1, True
            vm.update(_run_context(uid, tests_store.get_test(uid, test_id)))
            item = templates.get_template("partials/test_item.html").render(
                {"t": {"id": test_id, "name": name, "status": "done"}})
        else:
            tests_store.delete_test(uid, test_id)   # failed/empty: don't leave a placeholder
            item = ""
        panel = templates.get_template("partials/workspace.html").render(vm)
        job.emit({"type": "done", "panel_html": panel, "item_html": item,
                  "status": "done" if ok else "error", "test_id": test_id})


@router.post("/app/generate/start")
def app_generate_start(
    prompt: str = Form(""),
    devices: str = Form(""),
    language: str = Form("py"),
    regen_of: str = Form(""),
    user: dict = Depends(require_user),
):
    """Kick off a generation in the background and return the (generating) test id plus
    its sidebar item. ``regen_of`` regenerates into an existing test as a new version
    (#12); otherwise a fresh test is created. The client then attaches to the stream."""
    dev = generate.parse_devices(devices)
    uid = user["id"]
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
    if t is None:                                    # new test (or regen target not found)
        t = tests_store.create_generating(uid, name, prompt, language, dev)
    test_id, name = t["id"], t["name"]

    gen_registry.start(test_id, uid, lambda job: _run_generation(
        job, uid, test_id, name, prompt, dev, devices, language, meta, prov))

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


@router.post("/app/tests/{test_id}/hardware", response_class=HTMLResponse)
def save_hardware(test_id: int, hwType: list[str] = Form(default=[]),
                  hwCount: list[str] = Form(default=[]), user: dict = Depends(require_user)):
    """Persist the user's edited hardware requirements from the Test Configuration tab."""
    hardware = []
    for t, c in zip(hwType, hwCount):
        try:
            count = max(1, min(4, int(c)))
        except (TypeError, ValueError):
            count = 1
        hardware.append({"type": t, "count": count, "reason": ""})
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
    vm.update(_run_context(user["id"], test))
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


@router.get("/runs", response_class=HTMLResponse)
def runs_dashboard(request: Request):
    return templates.TemplateResponse(request, "runs.html")


@router.get("/coverage", response_class=HTMLResponse)
def coverage_matrix(request: Request):
    return templates.TemplateResponse(request, "coverage.html")
