"""Thin Meraki Dashboard API client used by the Settings proxy (handoff G10).

We expose a small, stable shape to the frontend (the *co-decided contract*), not
raw Meraki JSON:

    org     -> {id, name, url, region, hostname, status}
    network -> {id, orgId, name, url}
    device  -> {serial, name, model, mac, networkId, clientId}

The API key comes from ``MERAKI_API_KEY``. When it is unset, ``configured()`` is
False and callers render a "not configured" state — the UI stays demoable
without credentials. Any API failure raises ``MerakiError`` with a
human-readable message for the inline ``.set-error`` slot.
"""

from urllib.parse import urlparse

import requests

from web import config

_TIMEOUT = 15


class MerakiError(Exception):
    """A verify/fetch failed; the message is safe to show inline."""


def configured():
    return bool(config.MERAKI_API_KEY)


def _get(path):
    if not configured():
        raise MerakiError("Meraki integration is not configured (set MERAKI_API_KEY).")
    url = f"{config.MERAKI_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {config.MERAKI_API_KEY}",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise MerakiError(f"Could not reach the Meraki Dashboard API ({exc.__class__.__name__}).")
    if resp.status_code == 404:
        raise MerakiError("Not found — check the ID and try again.")
    if resp.status_code in (401, 403):
        raise MerakiError("Meraki rejected the API key (401/403).")
    if resp.status_code >= 400:
        raise MerakiError(f"Meraki returned HTTP {resp.status_code}.")
    try:
        return resp.json()
    except ValueError:
        raise MerakiError("Unexpected (non-JSON) response from Meraki.")


def _map_org(data):
    url = data.get("url", "") or ""
    region = ((data.get("cloud") or {}).get("region") or {}).get("name")
    api_enabled = (data.get("api") or {}).get("enabled", True)
    return {
        "id": str(data.get("id", "")),
        "name": data.get("name", "") or "(unnamed org)",
        "url": url,
        # hostname is the dashboard shard, derivable from the org url (n149.meraki.com)
        "region": region or "—",
        "hostname": urlparse(url).hostname or "—",
        "status": "operational" if api_enabled else "API disabled",
    }


def _map_network(data):
    return {
        "id": str(data.get("id", "")),
        "orgId": str(data.get("organizationId", "")),
        "name": data.get("name", "") or "(unnamed network)",
        "url": data.get("url", "") or "",
    }


def _map_device(data):
    serial = data.get("serial", "") or ""
    return {
        "serial": serial,
        # Meraki devices may have a blank name until configured; fall back to serial.
        "name": data.get("name") or serial or "(unnamed)",
        "model": data.get("model", "") or "",
        "mac": data.get("mac", "") or "",
        "networkId": str(data.get("networkId", "")),
        # Meraki devices carry no "clientId"; use the serial as the stable row id
        # the @-mention chip references (handoff G11 chip identifiers).
        "clientId": serial,
    }


def verify_org(org_id):
    return _map_org(_get(f"/organizations/{org_id}"))


def verify_network(network_id):
    return _map_network(_get(f"/networks/{network_id}"))


def list_devices(network_id):
    data = _get(f"/networks/{network_id}/devices")
    if not isinstance(data, list):
        raise MerakiError("Unexpected device list from Meraki.")
    return [_map_device(d) for d in data if isinstance(d, dict)]
