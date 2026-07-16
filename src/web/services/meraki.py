"""Thin Meraki Dashboard API client used by the Settings proxy (handoff G10).

We expose a small, stable shape to the frontend (the *co-decided contract*), not
raw Meraki JSON:

    org     -> {id, name, url, region, hostname, status}
    network -> {id, orgId, name, url}
    device  -> {serial, name, model, mac, networkId, clientId}

This is a pure Meraki client: the caller passes the user's API key in (looked up
from the per-user store). It has no knowledge of users or storage. Any API failure
raises ``MerakiError`` with a human-readable message for the inline error slot.
"""

from urllib.parse import urlparse

import requests

from web import config

_TIMEOUT = 15


class MerakiError(Exception):
    """A verify/fetch failed; the message is safe to show inline."""


def _request(method, path, key, body=None):
    # The key is never logged and never returned to the client.
    if not key:
        raise MerakiError("Meraki integration is not configured (add an API key in Settings).")
    url = f"{config.MERAKI_BASE_URL}{path}"
    headers = {
        "X-Cisco-Meraki-API-Key": key,
        "Accept": "application/json",
    }
    try:
        resp = requests.request(method, url, headers=headers, json=body, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise MerakiError(f"Could not reach the Meraki Dashboard API ({exc.__class__.__name__}).")
    if resp.status_code == 404:
        raise MerakiError("Not found — check the ID and try again.")
    if resp.status_code in (401, 403):
        raise MerakiError("Meraki rejected the API key (401/403).")
    if resp.status_code >= 400:
        raise MerakiError(f"Meraki returned HTTP {resp.status_code}.")
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        raise MerakiError("Unexpected (non-JSON) response from Meraki.")


def _get(path, key):
    return _request("GET", path, key)


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


def verify_org(org_id, key):
    return _map_org(_get(f"/organizations/{org_id}", key))


def verify_network(network_id, key):
    return _map_network(_get(f"/networks/{network_id}", key))


def list_devices(network_id, key):
    data = _get(f"/networks/{network_id}/devices", key)
    if not isinstance(data, list):
        raise MerakiError("Unexpected device list from Meraki.")
    return [_map_device(d) for d in data if isinstance(d, dict)]


def validate_key(key):
    """Confirm a key works before we store it, via the canonical "who am I"
    endpoint. Returns a display name for the calling identity, or raises."""
    data = _get("/administered/identities/me", key)
    if not isinstance(data, dict):
        raise MerakiError("Unexpected response validating the API key.")
    return data.get("name") or data.get("email") or "your account"


# --- Run feature: network + device lifecycle -------------------------------------

def _map_inventory_device(data):
    serial = data.get("serial", "") or ""
    return {
        "serial": serial,
        "name": data.get("name") or serial or "(unnamed)",
        "model": data.get("model", "") or "",
        "mac": data.get("mac", "") or "",
        "productType": data.get("productType", "") or "",
        # networkId is null/blank for an unclaimed device still in org inventory.
        "networkId": str(data.get("networkId") or ""),
        "claimed": bool(data.get("networkId")),
    }


def list_networks(org_id, key):
    """All networks under an org (used to auto-populate Settings instead of manual
    per-ID entry)."""
    data = _get(f"/organizations/{org_id}/networks", key)
    if not isinstance(data, list):
        raise MerakiError("Unexpected network list from Meraki.")
    return [_map_network(d) for d in data if isinstance(d, dict)]


def list_org_inventory(org_id, key, unclaimed_only=True):
    """Devices in the org's inventory. With ``unclaimed_only`` (default) only devices
    not assigned to any network are returned — the pool a run can claim from."""
    q = "?usedState=unused" if unclaimed_only else ""
    data = _get(f"/organizations/{org_id}/inventoryDevices{q}", key)
    if not isinstance(data, list):
        raise MerakiError("Unexpected inventory list from Meraki.")
    return [_map_inventory_device(d) for d in data if isinstance(d, dict)]


def create_network(org_id, name, product_types, key, copy_from_network_id=None):
    """Create a network under an org. When ``copy_from_network_id`` is given, Meraki
    clones that network's configuration (build-from-example)."""
    body = {"name": name, "productTypes": list(product_types or [])}
    if copy_from_network_id:
        body["copyFromNetworkId"] = copy_from_network_id
    data = _request("POST", f"/organizations/{org_id}/networks", key, body)
    if not isinstance(data, dict):
        raise MerakiError("Unexpected response creating the network.")
    return _map_network(data)


def delete_network(network_id, key):
    """Delete an ephemeral network (teardown)."""
    _request("DELETE", f"/networks/{network_id}", key)


def claim_device(network_id, serials, key):
    """Claim one or more devices (by serial) from org inventory into a network."""
    return _request("POST", f"/networks/{network_id}/devices/claim", key,
                    {"serials": list(serials)})


def remove_device(network_id, serial, key):
    """Release a device from a network back to org inventory (teardown)."""
    _request("POST", f"/networks/{network_id}/devices/remove", key, {"serial": serial})


# --- Run feature: per-hardware-type configuration writes (scratch-build agent) ----

def update_ssid(network_id, number, config_body, key):
    """Configure a wireless SSID (e.g. name, auth, enabled)."""
    return _request("PUT", f"/networks/{network_id}/wireless/ssids/{number}", key, config_body)


def create_appliance_vlan(network_id, config_body, key):
    """Create a security-appliance VLAN."""
    return _request("POST", f"/networks/{network_id}/appliance/vlans", key, config_body)


def update_appliance_firewall_rules(network_id, rules, key):
    """Replace the security appliance's L3 firewall rules."""
    return _request("PUT", f"/networks/{network_id}/appliance/firewall/l3FirewallRules", key,
                    {"rules": rules})


def update_camera_quality_retention(network_id, config_body, key):
    """Create/update a camera quality-and-retention profile."""
    return _request("POST", f"/networks/{network_id}/camera/qualityRetentionProfiles", key,
                    config_body)
