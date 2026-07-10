"""FastAPI application for the casewright frontend.

Run it:  uvicorn web.main:app --reload

Mounts the static assets, includes the page/proxy routers, and nothing else —
the generation engine is reused from ``rag`` behind a lazy singleton (web.deps).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from web import config
from web.routers import app_view, settings, site

app = FastAPI(title="casewright", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(site.router)
app.include_router(app_view.router)
app.include_router(settings.router)
