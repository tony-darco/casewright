"""The Meraki API key, and the connected organizations / networks / devices.

The key is a secret and lives in ``.env`` (web.settings); the default example
network is a setting and lives in config.yaml. What's left here is *data* — the
fetched org -> network -> device tree — which stays in SQLite (db.meraki_data),
scoped to a user_id like the rest of the cached records.
"""

import json

from web import db, settings


def _row(user_id: int) -> dict:
    with db.cursor() as conn:
        r = conn.execute("SELECT orgs_json FROM meraki_data WHERE user_id = ?", (user_id,)).fetchone()
    return dict(r) if r else {"orgs_json": "[]"}


def _save(user_id: int, orgs: list) -> None:
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO meraki_data (user_id, orgs_json) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET orgs_json = excluded.orgs_json",
            (user_id, json.dumps(orgs)),
        )


# --- API key (secret, in .env) ----------------------------------------------------

def get_meraki_key() -> str:
    return settings.secret(settings.MERAKI_API_KEY)


def set_meraki_key(key: str) -> None:
    settings.set_secret(settings.MERAKI_API_KEY, key)


def clear_meraki_key() -> None:
    settings.clear_secret(settings.MERAKI_API_KEY)


def key_configured() -> bool:
    return bool(get_meraki_key())


def masked_meraki_key() -> str:
    key = get_meraki_key()
    if not key:
        return ""
    return ("•" * 8 + key[-4:]) if len(key) >= 4 else "•" * len(key)


# --- organizations / networks / devices ------------------------------------------

def list_orgs(user_id: int) -> list:
    orgs = json.loads(_row(user_id)["orgs_json"] or "[]")
    for org in orgs:
        org.setdefault("networks", [])
        for net in org["networks"]:
            net.setdefault("devices", [])
    return orgs


def org_exists(user_id: int, org_id: str) -> bool:
    return any(o.get("id") == org_id for o in list_orgs(user_id))


def remove_org(user_id: int, org_id: str) -> bool:
    """Forget an organization for this user: drops it and its cached networks/devices
    from our store only — nothing is deleted on the Meraki side. Clears the default
    example network too if it pointed into this org, so it can't dangle onto a network
    we no longer know about. Returns False if the org wasn't connected."""
    row = _row(user_id)
    orgs = json.loads(row["orgs_json"] or "[]")
    remaining = [o for o in orgs if o.get("id") != org_id]
    if len(remaining) == len(orgs):
        return False
    dropped_nets = {n.get("id") for o in orgs if o.get("id") == org_id
                    for n in o.get("networks", [])}
    _save(user_id, remaining)
    if get_default_network_id() in dropped_nets:
        set_default_network_id("")
    return True


def save_org(user_id: int, org: dict) -> dict:
    row = _row(user_id)
    orgs = json.loads(row["orgs_json"] or "[]")
    for i, existing in enumerate(orgs):
        if existing.get("id") == org.get("id"):
            org = {**org, "networks": existing.get("networks", [])}
            orgs[i] = org
            break
    else:
        org = {**org, "networks": []}
        orgs.append(org)
    _save(user_id, orgs)
    return org


def add_network(user_id: int, org_id: str, net: dict, devices: list) -> dict:
    entry = {**net, "devices": devices}
    row = _row(user_id)
    orgs = json.loads(row["orgs_json"] or "[]")
    for org in orgs:
        if org.get("id") == org_id:
            nets = org.setdefault("networks", [])
            for i, existing in enumerate(nets):
                if existing.get("id") == net.get("id"):
                    nets[i] = entry
                    break
            else:
                nets.append(entry)
            break
    _save(user_id, orgs)
    return entry


def set_network_devices(user_id: int, network_id: str, devices: list) -> None:
    """Attach a freshly-fetched device list to an already-stored network (used by the
    lazy per-network device load, so the @-mention picker gets populated on demand)."""
    row = _row(user_id)
    orgs = json.loads(row["orgs_json"] or "[]")
    for org in orgs:
        for net in org.get("networks", []):
            if net.get("id") == network_id:
                net["devices"] = devices
                _save(user_id, orgs)
                return


def org_id_for_network(user_id: int, network_id: str) -> str:
    """The org that owns ``network_id`` for this user, or the first connected org as
    a fallback (used to inject a concrete organization ID into generated tests)."""
    orgs = list_orgs(user_id)
    if network_id:
        for org in orgs:
            for net in org.get("networks", []):
                if net.get("id") == network_id:
                    return org.get("id", "")
    return orgs[0].get("id", "") if orgs else ""


def verified_networks(user_id: int) -> list:
    out = []
    for org in list_orgs(user_id):
        for net in org.get("networks", []):
            out.append({
                "id": net.get("id"),
                "name": net.get("name"),
                "orgName": org.get("name", ""),
                "devices": net.get("devices", []),
            })
    return out


# --- default example network (Run feature) ---------------------------------------

def default_or_first_network_id(user_id: int, org_id: str = "") -> str:
    """A concrete network ID to bake into generated tests: the configured default
    example network, else the first connected network (preferring ``org_id``). The
    Run feature swaps this for the ephemeral network at run time, so any real network
    id works — the point is to hand the model a literal instead of nothing, which is
    what pushes it to invent network-discovery code."""
    default = get_default_network_id()
    if default:
        return default
    nets = verified_networks(user_id)
    if org_id:
        for n in nets:
            if org_id_for_network(user_id, n.get("id", "")) == org_id and n.get("id"):
                return n["id"]
    return next((n["id"] for n in nets if n.get("id")), "")


def get_default_network_id() -> str:
    return settings.section("meraki")["default_network_id"] or ""


def set_default_network_id(network_id: str) -> None:
    settings.save("meraki", {"default_network_id": network_id.strip()})


def set_networks_for_org(user_id: int, org_id: str, networks: list) -> None:
    """Replace the network list under ``org_id`` with ``networks`` (each a mapped
    network dict), preserving any devices already fetched for networks that persist.
    Used when auto-listing / refreshing an org's networks from the Meraki API."""
    row = _row(user_id)
    orgs = json.loads(row["orgs_json"] or "[]")
    for org in orgs:
        if org.get("id") == org_id:
            existing = {n.get("id"): n.get("devices", []) for n in org.get("networks", [])}
            org["networks"] = [{**net, "devices": existing.get(net.get("id"), [])} for net in networks]
            break
    _save(user_id, orgs)
