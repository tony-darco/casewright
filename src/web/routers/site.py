"""Marketing pages (handoff G1): Home and How-it-works.

Real server routes (not the mockup's hash routing) so direct links work.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web.deps import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"active": "home"})


@router.get("/how", response_class=HTMLResponse)
def how_it_works(request: Request):
    return templates.TemplateResponse(request, "how.html", {"active": "how"})
