"""Connecting/disconnecting a Meraki org (Settings → Meraki integration).

Orgs live as a JSON tree on the user's meraki_data row, so uniqueness and removal are
enforced in the store rather than by a DB constraint — these cover both, plus the
default-example-network coupling (removing an org must not leave the default pointing
at a network we've forgotten).
"""

import pytest

from web import db
from web.services import store

_seq = iter(range(1000))


@pytest.fixture
def uid():
    db.init()
    return db.create_user(f"orgtest{next(_seq)}", "h")["id"]


def _mk_org(org_id="123", name="Acme"):
    return {"id": org_id, "name": name, "url": "", "status": "operational",
            "region": "North America", "hostname": "n1.meraki.com"}


def _net(net_id, name):
    return {"id": net_id, "name": name, "orgId": "123", "url": ""}


def test_save_org_is_idempotent(uid):
    store.save_org(uid, _mk_org())
    store.save_org(uid, _mk_org())
    assert len(store.list_orgs(uid)) == 1


def test_org_exists_tracks_saved_orgs(uid):
    assert not store.org_exists(uid, "123")
    store.save_org(uid, _mk_org())
    assert store.org_exists(uid, "123")
    assert not store.org_exists(uid, "999")


def test_remove_org_drops_only_that_org(uid):
    store.save_org(uid, _mk_org("123", "Acme"))
    store.save_org(uid, _mk_org("456", "Other"))

    assert store.remove_org(uid, "123") is True

    assert [o["id"] for o in store.list_orgs(uid)] == ["456"]


def test_remove_org_is_reported_when_absent(uid):
    assert store.remove_org(uid, "nope") is False


def test_remove_org_keeps_the_api_key(uid):
    store.set_meraki_key("secret-key")
    store.save_org(uid, _mk_org())

    store.remove_org(uid, "123")

    assert store.get_meraki_key() == "secret-key"


def test_remove_org_clears_a_default_network_it_owned(uid):
    store.save_org(uid, _mk_org())
    store.set_networks_for_org(uid, "123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.remove_org(uid, "123")

    # otherwise a run would try to clone a network we no longer know anything about
    assert store.get_default_network_id() == ""


def test_remove_org_leaves_an_unrelated_default_network_alone(uid):
    store.save_org(uid, _mk_org("123"))
    store.save_org(uid, _mk_org("456"))
    store.set_networks_for_org(uid, "123", [_net("N_1", "home")])
    store.set_networks_for_org(uid, "456", [_net("N_2", "lab")])
    store.set_default_network_id("N_2")

    store.remove_org(uid, "123")

    assert store.get_default_network_id() == "N_2"


def test_default_network_survives_an_org_refresh(uid):
    """Refreshing the cached org tree (SQLite) must not disturb the default network
    (config.yaml)."""
    store.save_org(uid, _mk_org())
    store.set_networks_for_org(uid, "123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.set_networks_for_org(uid, "123", [_net("N_1", "home renamed")])

    assert store.get_default_network_id() == "N_1"


def test_default_network_survives_an_api_key_change(uid):
    store.save_org(uid, _mk_org())
    store.set_networks_for_org(uid, "123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.set_meraki_key("rotated")

    assert store.get_default_network_id() == "N_1"
