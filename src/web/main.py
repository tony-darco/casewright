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
from web.routers import account_routes, app_view, auth_routes, runs, settings, site
from web.services import logs_store

# Fail closed on insecure secrets before doing anything else (issue #10): outside
# DEV_MODE this aborts startup rather than signing forgeable sessions.
config.validate_startup_secrets()

db.init()  # ensure the SQLite schema exists before serving
logs_store.install_app_log()  # capture app-wide logs for Settings → Logs (issue #8)

if config.DEV_MODE and config.JWT_SECRET == config.JWT_DEV_SENTINEL:
    logging.getLogger("web").warning(
        "CASEWRIGHT_DEV mode: signing sessions with the insecure dev JWT secret. "
        "Never run this in production — set JWT_SECRET and CASEWRIGHT_ENC_KEY instead."
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
app.include_router(runs.router, dependencies=[Depends(require_user)])
app.include_router(settings.router, dependencies=[Depends(require_user)])
app.include_router(account_routes.router, dependencies=[Depends(require_user)])
