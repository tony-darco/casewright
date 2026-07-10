"""Settings (handoff G9) + the Meraki integration proxy (G10).

Every route is user-scoped: it reads the current user (require_user) and only ever
touches that user's key/orgs/networks in the store. The Meraki API key is looked up
per-user and passed into the Meraki client. Failures return the inline error partial
with ``HX-Retarget`` so it lands in the right error slot.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from web.auth import require_user
from web.deps import templates
from web.services import meraki, store

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
    return templates.TemplateResponse(request, "settings.html", ctx)


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


@router.get("/settings/meraki/networks/{network_id}/devices", response_class=HTMLResponse)
def network_devices(request: Request, network_id: str, user: dict = Depends(require_user)):
    try:
        devices = meraki.list_devices(network_id, store.get_meraki_key(user["id"]))
    except meraki.MerakiError as exc:
        return _error(request, str(exc))
    return templates.TemplateResponse(request, "partials/dev_table.html", {"devices": devices})
