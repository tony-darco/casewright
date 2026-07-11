"""FastAPI application for the casewright frontend.

Run it:  uvicorn web.main:app --reload

Mounts static, includes the page/proxy routers, wires auth. Marketing + auth pages
are public; the app, settings, and dashboards require a logged-in user (JWT cookie).
The generation engine is reused from ``rag`` behind a lazy singleton (web.deps).
"""

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from web import config, db
from web.auth import AuthRedirect, require_user
from web.routers import account_routes, app_view, auth_routes, settings, site
from web.services import logs_store

db.init()  # ensure the SQLite schema exists before serving
logs_store.install_app_log()  # capture app-wide logs for Settings → Logs (issue #8)

if config.JWT_SECRET == "dev-insecure-change-me":
    logging.getLogger("web").warning(
        "JWT_SECRET is the insecure dev default — set JWT_SECRET (and a real "
        "CASEWRIGHT_ENC_KEY) before deploying, or sessions can be forged."
    )

app = FastAPI(title="casewright", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.exception_handler(AuthRedirect)
async def _auth_redirect(request: Request, exc: AuthRedirect):
    # HTMX requests can't follow a 303 body-swap, so signal a client-side redirect.
    if request.headers.get("HX-Request"):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


# public
app.include_router(site.router)
app.include_router(auth_routes.router)
# protected — a logged-in user is required for every route in these routers
app.include_router(app_view.router, dependencies=[Depends(require_user)])
app.include_router(settings.router, dependencies=[Depends(require_user)])
app.include_router(account_routes.router, dependencies=[Depends(require_user)])
