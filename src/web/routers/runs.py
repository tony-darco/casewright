"""Run endpoints (Run feature): start a run, stream its logs/status, snapshot it.

Kept separate from app_view given the new surface. A run executes a saved test's code
against a freshly-provisioned ephemeral Meraki network; the orchestrator does the work
in a background thread and this router only starts it and relays progress over SSE
(reusing the run_registry Job buffer, so a client that reloads mid-run reattaches).
"""

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from web.auth import require_user
from web.deps import templates
from web.services import (
    run_logs_store, run_orchestrator, run_registry, runs_store, store, tests_store,
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

router = APIRouter()


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


def _status_partial(request, user_id, run):
    logs = run_logs_store.logs_for_run(run["id"]) if run else []
    return templates.TemplateResponse(request, "partials/run_status.html", {"run": run, "logs": logs})


@router.post("/app/tests/{test_id}/run", response_class=HTMLResponse)
def start_run(request: Request, test_id: int, runSource: str = Form("example"),
              networkId: str = Form(""), user: dict = Depends(require_user)):
    uid = user["id"]
    test = tests_store.get_test(uid, test_id)
    if not test:
        return HTMLResponse("<div class='gen-error'>Test not found.</div>", status_code=404)

    source = "scratch" if runSource == "scratch" else "example"
    tests_store.set_run_config(uid, test_id, source, networkId.strip())

    key = store.get_meraki_key(uid)
    if not key:
        return templates.TemplateResponse(request, "partials/run_status.html",
                                          {"run_error": "Add a Meraki API key in Settings first."})

    if source == "example":
        example_network_id = networkId.strip() or store.get_default_network_id(uid)
        if not example_network_id:
            return templates.TemplateResponse(request, "partials/run_status.html",
                                              {"run_error": "Select an example network (or set a default in Settings)."})
        org_id = store.org_id_for_network(uid, example_network_id)
    else:
        example_network_id = ""
        org_id = store.org_id_for_network(uid, store.get_default_network_id(uid))
    if not org_id:
        return templates.TemplateResponse(request, "partials/run_status.html",
                                          {"run_error": "Connect a Meraki organization in Settings first."})

    # MVP single-run guard: reject rather than queue a second concurrent run.
    run = runs_store.create_run(uid, test_id, source, example_network_id)
    if not run_orchestrator.begin(run["id"]):
        runs_store.update_status(run["id"], "error", "A run is already in progress. Wait for it to finish.")
        return templates.TemplateResponse(request, "partials/run_status.html",
                                          {"run": runs_store.get_run(uid, run["id"]), "logs": []})

    run_registry.start(run["id"], uid, lambda job: run_orchestrator.start_run(
        job, uid, test, run["id"], run["run_code"], org_id, source, example_network_id, key))

    return _status_partial(request, uid, runs_store.get_run(uid, run["id"]))


@router.get("/app/tests/{test_id}/runs/{run_id}/stream")
def stream_run(test_id: int, run_id: int, user: dict = Depends(require_user)):
    """SSE: replay this run's buffered events then stream live ones. Falls back to the
    persisted status/logs if the job is gone (finished long ago or server restarted)."""
    uid = user["id"]
    job = run_registry.get(run_id, uid)
    if job is not None:
        return StreamingResponse((_sse(ev) for ev in job.subscribe()),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)

    run = runs_store.get_run(uid, run_id)

    def fallback():
        if not run:
            yield _sse({"type": "status", "status": "error", "error": "Run not found."})
        else:
            for e in run_logs_store.logs_for_run(run_id):
                yield _sse({"type": "log", "stage": e["stage"], "level": e["level"], "message": e["message"]})
            yield _sse({"type": "status", "status": run["status"], "error": run["error_message"]})
        yield _sse({"type": "done"})

    return StreamingResponse(fallback(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/app/tests/{test_id}/runs/{run_id}", response_class=HTMLResponse)
def run_snapshot(request: Request, test_id: int, run_id: int, user: dict = Depends(require_user)):
    """Current status + log backlog for a run (used to re-render after navigating back)."""
    run = runs_store.get_run(user["id"], run_id)
    if not run:
        return HTMLResponse("<div class='gen-error'>Run not found.</div>", status_code=404)
    return _status_partial(request, user["id"], run)
