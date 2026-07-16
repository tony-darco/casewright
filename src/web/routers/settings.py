"""Settings (handoff G9) + the Meraki integration proxy (G10).

Every route is user-scoped: it reads the current user (require_user) and only ever
touches that user's key/orgs/networks in the store. The Meraki API key is looked up
per-user and passed into the Meraki client. Failures return the inline error partial
with ``HX-Retarget`` so it lands in the right error slot.
"""

import json
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from web.auth import require_user
from web.deps import forget_failed_pipelines, templates
from web.services import (
    kb_ingest, kb_registry, kb_store, logs_store, meraki, ollama_admin, provider_store,
    store, url_fetch,
)

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"

logger = logging.getLogger(__name__)

router = APIRouter()


def _apikey_ctx(user_id, error=None, identity=None):
    """Non-secret view of the API-key state — masked hint only, never plaintext."""
    return {
        "configured": store.key_configured(user_id),
        "masked": store.masked_meraki_key(user_id),
        "error": error,
        "identity": identity,
    }


def _apikey_response(request, user_id, error=None, identity=None):
    return templates.TemplateResponse(
        request, "partials/apikey_status.html", _apikey_ctx(user_id, error, identity)
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_home(request: Request, user: dict = Depends(require_user)):
    ctx = _apikey_ctx(user["id"])
    ctx["orgs"] = store.list_orgs(user["id"])  # this user's persisted orgs/networks/devices
    ctx["account"] = user  # id, username, email, first_name, last_name
    ctx["provider"] = provider_store.get_settings(user["id"])  # model-provider overrides
    return templates.TemplateResponse(request, "settings.html", ctx)


@router.get("/settings/logs", response_class=HTMLResponse)
def settings_logs(request: Request, user: dict = Depends(require_user)):
    """Logs page (issue #8): the app-wide log plus this user's per-test logs.
    Loaded on demand (HTMX) since the app log can be large."""
    return templates.TemplateResponse(request, "partials/logs.html", {
        "app_log": logs_store.app_log_lines(),
        "test_logs": logs_store.tests_with_logs(user["id"]),
    })


@router.post("/settings/meraki/apikey", response_class=HTMLResponse)
def set_apikey(request: Request, apiKey: str = Form(""), user: dict = Depends(require_user)):
    key = apiKey.strip()
    if not key:
        return _apikey_response(request, user["id"], error="Paste an API key.")
    # Validate against Meraki before storing, so we never persist a dud.
    try:
        identity = meraki.validate_key(key)
        store.set_meraki_key(user["id"], key)
    except meraki.MerakiError as exc:
        return _apikey_response(request, user["id"], error=str(exc))
    except Exception:
        logger.exception("Unexpected error saving Meraki API key for user %s", user["id"])
        return _apikey_response(request, user["id"], error="Something went wrong saving the key — check the server logs.")
    return _apikey_response(request, user["id"], identity=identity)


@router.post("/settings/meraki/apikey/remove", response_class=HTMLResponse)
def remove_apikey(request: Request, user: dict = Depends(require_user)):
    store.clear_meraki_key(user["id"])
    return _apikey_response(request, user["id"])


# --- model provider (Settings → Model provider) -----------------------------------

def _provider_result(request: Request, **ctx):
    return templates.TemplateResponse(request, "partials/provider_result.html", ctx)


@router.post("/settings/provider/check", response_class=HTMLResponse)
def check_provider(request: Request, ollamaUrl: str = Form(""), user: dict = Depends(require_user)):
    """Validate the Ollama server is reachable; on success, list its installed
    models (the partial carries the datalists that feed the model inputs)."""
    try:
        url = ollama_admin.normalize_url(ollamaUrl)
        version = ollama_admin.check_server(url)
        models = ollama_admin.list_models(url)
    except ollama_admin.OllamaError as exc:
        return templates.TemplateResponse(request, "partials/provider_check.html", {"error": str(exc)})
    return templates.TemplateResponse(
        request, "partials/provider_check.html", {"ok": True, "version": version, "models": models}
    )


@router.post("/settings/provider", response_class=HTMLResponse)
def save_provider(
    request: Request,
    provider: str = Form("ollama"),
    ollamaUrl: str = Form(""),
    chatModel: str = Form(""),
    embedModel: str = Form(""),
    temperature: str = Form(""),
    pull: str = Form(""),
    user: dict = Depends(require_user),
):
    """Validate and save the user's provider settings. If an entered model isn't
    installed on the server, come back with a confirm prompt instead of pulling
    unasked; the prompt's button re-posts this same form with ``pull=1``."""
    if provider != "ollama":
        return _provider_result(request, error=f"Unsupported provider: {provider!r}.")
    chat_model, embed_model = chatModel.strip(), embedModel.strip()

    temp = None
    if temperature.strip():
        try:
            temp = float(temperature)
        except ValueError:
            return _provider_result(request, error="Temperature must be a number (e.g. 0.2).")
        if not 0.0 <= temp <= 2.0:
            return _provider_result(request, error="Temperature must be between 0 and 2.")

    try:
        url = ollama_admin.normalize_url(ollamaUrl)
        ollama_admin.check_server(url)  # the server must be up before we persist anything
        missing = [m for m in dict.fromkeys((chat_model, embed_model))
                   if m and not ollama_admin.model_exists(url, m)]
    except ollama_admin.OllamaError as exc:
        return _provider_result(request, error=str(exc))

    if missing and not pull:
        return _provider_result(request, missing=missing)
    for name in missing:
        try:
            ollama_admin.pull_model(url, name)
        except ollama_admin.OllamaError as exc:
            return _provider_result(request, error=str(exc))

    provider_store.save_settings(user["id"], provider, url, chat_model, embed_model, temp)
    forget_failed_pipelines()  # the new settings may fix a previously failed pipeline build
    return _provider_result(request, saved=provider_store.get_settings(user["id"]), pulled=missing)


def _error(request: Request, message: str, retarget: str | None = None):
    resp = templates.TemplateResponse(request, "partials/error.html", {"message": message})
    if retarget:
        resp.headers["HX-Retarget"] = retarget
        resp.headers["HX-Reswap"] = "innerHTML"
    return resp


@router.post("/settings/meraki/orgs", response_class=HTMLResponse)
def add_org(request: Request, orgId: str = Form(""), user: dict = Depends(require_user)):
    org_id = orgId.strip()
    if not org_id:
        return _error(request, "Enter an organization ID.", retarget="#orgError")
    try:
        org = meraki.verify_org(org_id, store.get_meraki_key(user["id"]))
    except meraki.MerakiError as exc:
        return _error(request, str(exc), retarget="#orgError")
    org = store.save_org(user["id"], org)  # persist under this user
    return templates.TemplateResponse(request, "partials/org_card.html", {"org": org})


@router.post("/settings/meraki/orgs/{org_id}/networks", response_class=HTMLResponse)
def add_network(
    request: Request, org_id: str, networkId: str = Form(""), orgName: str = Form(""),
    user: dict = Depends(require_user),
):
    net_id = networkId.strip()
    slot = f"#neterr-{org_id}"
    if not net_id:
        return _error(request, "Enter a network ID.", retarget=slot)
    try:
        key = store.get_meraki_key(user["id"])
        net = meraki.verify_network(net_id, key)
        devices = meraki.list_devices(net_id, key)
    except meraki.MerakiError as exc:
        return _error(request, str(exc), retarget=slot)
    store.add_network(user["id"], org_id, net, devices)  # persist under this user
    return templates.TemplateResponse(
        request, "partials/net_card.html", {"net": net, "devices": devices, "org_name": orgName}
    )


@router.get("/api/networks", response_class=JSONResponse)
def api_networks(user: dict = Depends(require_user)):
    """This user's verified networks + devices for the app's @-mention / device picker."""
    return store.verified_networks(user["id"])


# --- knowledge base (Settings → Knowledge base) -----------------------------------

def _kb_page(request: Request, user_id: int):
    """The full Knowledge base page partial (storage + form + version list)."""
    return templates.TemplateResponse(request, "partials/knowledgebase.html", {
        "versions": kb_store.list_versions(user_id),
        "storage": kb_store.get_storage(user_id),
    })


@router.get("/settings/knowledgebase", response_class=HTMLResponse)
def settings_kb(request: Request, user: dict = Depends(require_user)):
    """Knowledge base page: loaded on demand (HTMX), same as Logs."""
    return _kb_page(request, user["id"])


@router.post("/settings/knowledgebase/storage", response_class=HTMLResponse)
def kb_set_storage(request: Request, storageKind: str = Form("local"),
                   storageUrl: str = Form(""), user: dict = Depends(require_user)):
    """Where this user's vector store lives: local — inside this application
    (default) — or a remote Chroma server URL, wherever it's hosted. The URL is an
    infrastructure setting like the Ollama URL, but unlike Ollama (localhost by
    design) a remote Chroma may live anywhere, so only the URL's shape is validated
    — no host restriction."""
    if storageKind == "remote":
        storageUrl = (storageUrl or "").strip().rstrip("/")
        if storageUrl and not storageUrl.startswith(("http://", "https://")):
            storageUrl = "http://" + storageUrl
        parts = urlparse(storageUrl)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return _error(request, "Enter a valid Chroma server URL.", retarget="#kbStorageError")
        if parts.username or parts.password:
            return _error(request, "Credentials aren't allowed in the URL.", retarget="#kbStorageError")
    else:
        storageKind, storageUrl = "local", ""
    kb_store.set_storage(user["id"], storageKind, storageUrl)
    return templates.TemplateResponse(request, "partials/kb_storage.html", {
        "storage": kb_store.get_storage(user["id"]), "saved": True,
    })


@router.post("/settings/knowledgebase/start", response_class=HTMLResponse)
def kb_start(
    request: Request,
    sourceKind: str = Form(...),
    splitMethod: str = Form(...),
    url: str = Form(""),
    file: UploadFile | None = File(None),
    user: dict = Depends(require_user),
):
    uid = user["id"]
    if splitMethod not in ("langchain", "custom"):
        return _error(request, "Choose a split method.", retarget="#kbError")

    try:
        if sourceKind == "link":
            url = url.strip()
            if not url:
                return _error(request, "Enter a URL.", retarget="#kbError")
            content = url_fetch.fetch_spec_url(url)
            source_label = url
            name = url
        elif sourceKind == "upload":
            if file is None or not file.filename:
                return _error(request, "Choose a file to upload.", retarget="#kbError")
            content = file.file.read()   # in-memory only, discarded after ingest parses it
            source_label = file.filename
            name = file.filename
        else:
            return _error(request, "Choose upload or link.", retarget="#kbError")
    except url_fetch.FetchError as exc:
        return _error(request, str(exc), retarget="#kbError")

    v = kb_store.create_embedding(uid, name, sourceKind, splitMethod, source_label)
    prov = provider_store.overrides(uid)   # ingestion embeds with the same model the user configured
    kb_registry.start(v["id"], uid, lambda job: kb_ingest.run_ingest(
        job, uid, v["id"], content, splitMethod, source_label, prov))

    version = kb_store.get_version(uid, v["id"])
    return templates.TemplateResponse(request, "partials/kb_version_item.html", {"v": version})


@router.get("/settings/knowledgebase/{version_id}/stream")
def kb_stream(version_id: int, user: dict = Depends(require_user)):
    """Attach to a running ingest (replay + live), or fall back to the persisted
    status if the job is gone (finished long ago or the server restarted)."""
    uid = user["id"]
    job = kb_registry.get(version_id, uid)
    if job is not None:
        return StreamingResponse((_sse(ev) for ev in job.subscribe()),
                                 media_type="text/event-stream", headers=_SSE_HEADERS)

    version = kb_store.get_version(uid, version_id)

    def fallback():
        if not version:
            yield _sse({"type": "done", "status": "error", "message": "Version not found."})
        elif version["status"] == "embedding":
            msg = "This ingest was interrupted (the server restarted). Try again."
            yield _sse({"type": "done", "status": "error", "message": msg})
        else:
            yield _sse({"type": "done", "status": version["status"], "message": version["error_message"]})

    return StreamingResponse(fallback(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/settings/knowledgebase/{version_id}/activate", response_class=HTMLResponse)
def kb_activate(request: Request, version_id: int, user: dict = Depends(require_user)):
    ok = kb_store.set_active(user["id"], version_id)
    if not ok:
        return _error(request, "Can't activate that version (not found, or still embedding).",
                      retarget="#kbError")
    return _kb_page(request, user["id"])


@router.post("/settings/knowledgebase/{version_id}/delete", response_class=HTMLResponse)
def kb_delete(request: Request, version_id: int, user: dict = Depends(require_user)):
    """Remove a failed version from the list (errored versions only — nothing was
    embedded for them, so there's no collection to clean up)."""
    kb_store.delete_version(user["id"], version_id)
    return _kb_page(request, user["id"])
