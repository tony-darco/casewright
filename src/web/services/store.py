"""Per-user Meraki store — the API key (encrypted) and the connected
organizations / networks / devices, all scoped to a user_id and persisted in
SQLite (db.meraki_data). Every function takes user_id and only ever touches that
user's row, so users can't see or modify each other's keys or data.

The API key additionally gets the secret treatment: encrypted at rest (crypto),
write-only from the UI, never returned to the browser, never logged.
"""

import json

from web import db
from web.services import crypto


def _row(user_id: int) -> dict:
    with db.cursor() as conn:
        r = conn.execute(
            "SELECT api_key_enc, orgs_json, default_network_id FROM meraki_data WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(r) if r else {"api_key_enc": None, "orgs_json": "[]", "default_network_id": ""}


def _save(user_id: int, api_key_enc, orgs: list) -> None:
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO meraki_data (user_id, api_key_enc, orgs_json) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET api_key_enc = excluded.api_key_enc, "
            "orgs_json = excluded.orgs_json",
            (user_id, api_key_enc, json.dumps(orgs)),
        )


# --- API key (secret) ------------------------------------------------------------

def get_meraki_key(user_id: int) -> str:
    enc = _row(user_id)["api_key_enc"]
    return crypto.decrypt(enc) if enc else ""


def set_meraki_key(user_id: int, key: str) -> None:
    row = _row(user_id)
    _save(user_id, crypto.encrypt(key.strip()), json.loads(row["orgs_json"] or "[]"))


def clear_meraki_key(user_id: int) -> None:
    row = _row(user_id)
    _save(user_id, None, json.loads(row["orgs_json"] or "[]"))


def key_configured(user_id: int) -> bool:
    return bool(_row(user_id)["api_key_enc"])


def masked_meraki_key(user_id: int) -> str:
    key = get_meraki_key(user_id)
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
    _save(user_id, row["api_key_enc"], orgs)
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
    _save(user_id, row["api_key_enc"], orgs)
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
                _save(user_id, row["api_key_enc"], orgs)
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

def get_default_network_id(user_id: int) -> str:
    return _row(user_id)["default_network_id"] or ""


def set_default_network_id(user_id: int, network_id: str) -> None:
    """Set the account's default example network (targeted update that leaves the
    api key / orgs tree untouched)."""
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO meraki_data (user_id, default_network_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET default_network_id = excluded.default_network_id",
            (user_id, network_id.strip()),
        )


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
    _save(user_id, row["api_key_enc"], orgs)
