"""Settings (handoff G9) + the Meraki integration proxy (G10).

The Meraki endpoints verify against the Dashboard API and return HTMX partials
(org card / network card / device table). On failure we return the inline error
partial and use ``HX-Retarget``/``HX-Reswap`` so it lands in the right error slot
rather than being appended to the list.
"""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from web.deps import templates
from web.services import meraki

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_home(request: Request):
    return templates.TemplateResponse(
        request, "settings.html", {"meraki_configured": meraki.configured()}
    )


def _error(request: Request, message: str, retarget: str | None = None):
    resp = templates.TemplateResponse(request, "partials/error.html", {"message": message})
    if retarget:
        # redirect this swap to the inline error slot (HTMX response headers)
        resp.headers["HX-Retarget"] = retarget
        resp.headers["HX-Reswap"] = "innerHTML"
    return resp


@router.post("/settings/meraki/orgs", response_class=HTMLResponse)
def add_org(request: Request, orgId: str = Form("")):
    org_id = orgId.strip()
    if not org_id:
        return _error(request, "Enter an organization ID.", retarget="#orgError")
    try:
        org = meraki.verify_org(org_id)
    except meraki.MerakiError as exc:
        return _error(request, str(exc), retarget="#orgError")
    return templates.TemplateResponse(request, "partials/org_card.html", {"org": org})


@router.post("/settings/meraki/orgs/{org_id}/networks", response_class=HTMLResponse)
def add_network(
    request: Request, org_id: str, networkId: str = Form(""), orgName: str = Form("")
):
    net_id = networkId.strip()
    slot = f"#neterr-{org_id}"
    if not net_id:
        return _error(request, "Enter a network ID.", retarget=slot)
    try:
        net = meraki.verify_network(net_id)
        devices = meraki.list_devices(net_id)
    except meraki.MerakiError as exc:
        return _error(request, str(exc), retarget=slot)
    return templates.TemplateResponse(
        request, "partials/net_card.html", {"net": net, "devices": devices, "org_name": orgName}
    )


@router.get("/settings/meraki/networks/{network_id}/devices", response_class=HTMLResponse)
def network_devices(request: Request, network_id: str):
    try:
        devices = meraki.list_devices(network_id)
    except meraki.MerakiError as exc:
        return _error(request, str(exc))
    return templates.TemplateResponse(request, "partials/dev_table.html", {"devices": devices})
