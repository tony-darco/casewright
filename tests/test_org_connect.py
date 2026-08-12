"""Connecting/disconnecting a Meraki org (Settings → Meraki integration).

Orgs live as a JSON tree on the meraki_data row, so uniqueness and removal are
enforced in the store rather than by a DB constraint — these cover both, plus the
default-example-network coupling (removing an org must not leave the default pointing
at a network we've forgotten).
"""

from web.services import store


def _mk_org(org_id="123", name="Acme"):
    return {"id": org_id, "name": name, "url": "", "status": "operational",
            "region": "North America", "hostname": "n1.meraki.com"}


def _net(net_id, name):
    return {"id": net_id, "name": name, "orgId": "123", "url": ""}


def test_save_org_is_idempotent():
    store.save_org(_mk_org())
    store.save_org(_mk_org())
    assert len(store.list_orgs()) == 1


def test_org_exists_tracks_saved_orgs():
    assert not store.org_exists("123")
    store.save_org(_mk_org())
    assert store.org_exists("123")
    assert not store.org_exists("999")


def test_remove_org_drops_only_that_org():
    store.save_org(_mk_org("123", "Acme"))
    store.save_org(_mk_org("456", "Other"))

    assert store.remove_org("123") is True

    assert [o["id"] for o in store.list_orgs()] == ["456"]


def test_remove_org_is_reported_when_absent():
    assert store.remove_org("nope") is False


def test_remove_org_keeps_the_api_key():
    store.set_meraki_key("secret-key")
    store.save_org(_mk_org())

    store.remove_org("123")

    assert store.get_meraki_key() == "secret-key"


def test_remove_org_clears_a_default_network_it_owned():
    store.save_org(_mk_org())
    store.set_networks_for_org("123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.remove_org("123")

    # otherwise a run would try to clone a network we no longer know anything about
    assert store.get_default_network_id() == ""


def test_remove_org_leaves_an_unrelated_default_network_alone():
    store.save_org(_mk_org("123"))
    store.save_org(_mk_org("456"))
    store.set_networks_for_org("123", [_net("N_1", "home")])
    store.set_networks_for_org("456", [_net("N_2", "lab")])
    store.set_default_network_id("N_2")

    store.remove_org("123")

    assert store.get_default_network_id() == "N_2"


def test_default_network_survives_an_org_refresh():
    """_save() rewrites the orgs tree on the same row default_network_id lives on."""
    store.save_org(_mk_org())
    store.set_networks_for_org("123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.set_networks_for_org("123", [_net("N_1", "home renamed")])

    assert store.get_default_network_id() == "N_1"


def test_default_network_survives_an_api_key_change():
    store.save_org(_mk_org())
    store.set_networks_for_org("123", [_net("N_1", "home")])
    store.set_default_network_id("N_1")

    store.set_meraki_key("rotated")

    assert store.get_default_network_id() == "N_1"
