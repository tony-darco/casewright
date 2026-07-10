"""The product app (handoff G2/G3/G5/G6): the working UI + the real generate call.

``GET /app``            renders the composer/workspace shell.
``POST /app/generate``  runs the RAG pipeline and returns the workspace partial
                        (an HTMX swap target). Defined ``def`` (not ``async``) so
                        Starlette runs the blocking pipeline in its threadpool.
``GET /runs``           runs dashboard shell (G5, empty state).
``GET /coverage``       spec-coverage matrix shell (G6, empty state).
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from web.deps import templates
from web.services import generate

router = APIRouter()


@router.get("/app", response_class=HTMLResponse)
def app_home(request: Request):
    return templates.TemplateResponse(request, "app.html")


@router.post("/app/generate", response_class=HTMLResponse)
def app_generate(
    request: Request,
    prompt: str = Form(""),
    devices: str = Form(""),
    language: str = Form("ts"),
):
    vm = generate.build_view_model(prompt, generate.parse_devices(devices), language)
    return templates.TemplateResponse(request, "partials/workspace.html", vm)


@router.get("/runs", response_class=HTMLResponse)
def runs_dashboard(request: Request):
    return templates.TemplateResponse(request, "runs.html")


@router.get("/coverage", response_class=HTMLResponse)
def coverage_matrix(request: Request):
    return templates.TemplateResponse(request, "coverage.html")
