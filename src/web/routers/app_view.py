"""The product app (handoff G2/G3): the working UI, real generate call, and the
per-user test library (persisted).

``GET  /app``                    composer/workspace shell + the user's saved tests.
``POST /app/generate``           runs the pipeline, persists the test, returns the
                                 workspace partial + an OOB sidebar item. ``def`` (not
                                 ``async``) so the blocking pipeline runs in a thread.
``GET  /app/tests/{id}``         loads a saved test back into the workspace.
``POST /app/tests/{id}/rename``  renames a saved test (returns the updated item).
``GET  /runs`` / ``/coverage``   dashboard shells (empty states).
"""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from web.auth import require_user
from web.deps import templates
from web.services import generate, logs_store, provider_store, store, tests_store

router = APIRouter()


def _default_name(prompt: str) -> str:
    p = " ".join((prompt or "").split())
    return (p[:60] + "…") if len(p) > 60 else (p or "Untitled test")


def _gen_meta(user_id: int, devices: list) -> dict:
    """Concrete identifiers to inject into the generated test (issue #9): base URL and
    the org that owns the first referenced device's network."""
    net_id = next((d.get("networkId") for d in devices if isinstance(d, dict) and d.get("networkId")), "")
    return {"base_url": None, "org_id": store.org_id_for_network(user_id, net_id)}


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request, user: dict = Depends(require_user)):
    return templates.TemplateResponse(
        request, "app.html", {"tests": tests_store.list_tests(user["id"])}
    )


@router.post("/app/generate", response_class=HTMLResponse)
def app_generate(
    request: Request,
    prompt: str = Form(""),
    devices: str = Form(""),
    language: str = Form("py"),
    user: dict = Depends(require_user),
):
    dev = generate.parse_devices(devices)
    vm = generate.build_view_model(prompt, dev, language, _gen_meta(user["id"], dev),
                                   provider_store.overrides(user["id"]))
    vm["devices"] = devices  # raw JSON, echoed to the panel so a regenerate reuses the same @device grounding
    # persist only real generations (not the pipeline-unavailable / empty states)
    if not vm.get("error") and not vm.get("empty"):
        vm["t"] = tests_store.create_test(
            user["id"], _default_name(prompt), vm["prompt"], vm["file_name"],
            vm["code"], language, vm["endpoints"], dev, vm.get("validation"),
        )
        logs_store.record(user["id"], vm["t"]["id"], vm.get("_log"))
    return templates.TemplateResponse(request, "partials/generate_result.html", vm)


@router.post("/app/generate/stream")
def app_generate_stream(
    request: Request,
    prompt: str = Form(""),
    devices: str = Form(""),
    language: str = Form("py"),
    user: dict = Depends(require_user),
):
    """Live generation over SSE: stage-progress + token-by-token code. On the final
    event, persist the test and stream a ``done`` with the rendered panel + sidebar
    item. Sync def so the blocking pipeline stream runs in Starlette's threadpool."""
    dev = generate.parse_devices(devices)
    uid = user["id"]
    meta = _gen_meta(uid, dev)
    prov = provider_store.overrides(uid)

    def event_stream():
        for ev in generate.stream_events(prompt, dev, language, meta, prov):
            if ev["type"] != "final":
                yield _sse(ev)
                continue
            vm = ev["vm"]
            vm["devices"] = devices  # raw JSON, echoed to the panel for regenerate (see /app/generate)
            t = None
            if not vm.get("error") and not vm.get("empty"):
                t = tests_store.create_test(
                    uid, _default_name(prompt), vm["prompt"], vm["file_name"],
                    vm["code"], language, vm["endpoints"], dev, vm.get("validation"),
                )
                logs_store.record(uid, t["id"], vm.get("_log"))
                vm["t"] = t  # so the rendered panel carries the test id (editable code saves to it)
            panel = templates.get_template("partials/workspace.html").render(vm)
            item = templates.get_template("partials/test_item.html").render({"t": t}) if t else ""
            yield _sse({"type": "done", "panel_html": panel, "item_html": item})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/app/tests/{test_id}", response_class=HTMLResponse)
def load_test(request: Request, test_id: int, user: dict = Depends(require_user)):
    test = tests_store.get_test(user["id"], test_id)
    if not test:
        return HTMLResponse("<div class='gen-error'>Test not found.</div>", status_code=404)
    return templates.TemplateResponse(
        request, "partials/workspace.html", generate.view_model_from_test(test)
    )


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
