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

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from web.auth import require_user
from web.deps import templates
from web.services import generate, tests_store

router = APIRouter()


def _default_name(prompt: str) -> str:
    p = " ".join((prompt or "").split())
    return (p[:60] + "…") if len(p) > 60 else (p or "Untitled test")


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
    language: str = Form("ts"),
    user: dict = Depends(require_user),
):
    dev = generate.parse_devices(devices)
    vm = generate.build_view_model(prompt, dev, language)
    # persist only real generations (not the pipeline-unavailable / empty states)
    if not vm.get("error") and not vm.get("empty"):
        vm["t"] = tests_store.create_test(
            user["id"], _default_name(prompt), vm["prompt"], vm["file_name"],
            vm["code"], language, vm["endpoints"], dev,
        )
    return templates.TemplateResponse(request, "partials/generate_result.html", vm)


@router.get("/app/tests/{test_id}", response_class=HTMLResponse)
def load_test(request: Request, test_id: int, user: dict = Depends(require_user)):
    test = tests_store.get_test(user["id"], test_id)
    if not test:
        return HTMLResponse("<div class='gen-error'>Test not found.</div>", status_code=404)
    return templates.TemplateResponse(
        request, "partials/workspace.html", generate.view_model_from_test(test)
    )


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
