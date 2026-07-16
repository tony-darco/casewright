"""kb_store: CRUD for knowledge-base versions + per-user active-version pointer."""

from web import db
from web.services import kb_store


def test_create_embedding_assigns_collection_name():
    db.init()
    u = db.create_user("kbcreate", "h")
    v = kb_store.create_embedding(u["id"], "My Spec", "upload", "custom", "spec.json")
    assert v["collection_name"] == f"kb_u{u['id']}_v{v['id']}"
    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "embedding" and row["collection_name"] == v["collection_name"]


def test_mark_done_and_error():
    db.init()
    u = db.create_user("kbdone", "h")
    v = kb_store.create_embedding(u["id"], "S", "link", "langchain", "http://x")
    assert kb_store.mark_done(u["id"], v["id"], 42)
    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "done" and row["doc_count"] == 42

    v2 = kb_store.create_embedding(u["id"], "S2", "link", "langchain", "http://y")
    assert kb_store.mark_error(u["id"], v2["id"], "boom")
    row2 = kb_store.get_version(u["id"], v2["id"])
    assert row2["status"] == "error" and row2["error_message"] == "boom"


def test_list_versions_newest_first_with_active_flag():
    db.init()
    u = db.create_user("kblist", "h")
    v1 = kb_store.create_embedding(u["id"], "V1", "upload", "custom", "a.json")
    kb_store.mark_done(u["id"], v1["id"], 1)
    v2 = kb_store.create_embedding(u["id"], "V2", "upload", "custom", "b.json")
    kb_store.mark_done(u["id"], v2["id"], 2)
    kb_store.set_active(u["id"], v2["id"])

    versions = kb_store.list_versions(u["id"])
    assert [v["id"] for v in versions] == [v2["id"], v1["id"]]
    assert versions[0]["is_active"] == 1
    assert versions[1]["is_active"] == 0


def test_set_active_refuses_non_done_version():
    db.init()
    u = db.create_user("kbrefuse", "h")
    v = kb_store.create_embedding(u["id"], "V", "upload", "custom", "a.json")
    assert kb_store.set_active(u["id"], v["id"]) is False
    assert kb_store.get_active(u["id"]) is None


def test_get_active_only_returns_done_version():
    db.init()
    u = db.create_user("kbactive", "h")
    v = kb_store.create_embedding(u["id"], "V", "upload", "custom", "a.json")
    kb_store.mark_done(u["id"], v["id"], 5)
    assert kb_store.set_active(u["id"], v["id"]) is True
    active = kb_store.get_active(u["id"])
    assert active["id"] == v["id"]


def test_delete_version_errored_only_and_user_scoped():
    db.init()
    u, other = db.create_user("kbdel1", "h"), db.create_user("kbdel2", "h")
    done = kb_store.create_embedding(u["id"], "D", "upload", "custom", "a.json")
    kb_store.mark_done(u["id"], done["id"], 1)
    bad = kb_store.create_embedding(u["id"], "B", "upload", "custom", "b.json")
    kb_store.mark_error(u["id"], bad["id"], "boom")

    assert kb_store.delete_version(u["id"], done["id"]) is False   # done: kept
    assert kb_store.delete_version(other["id"], bad["id"]) is False  # not theirs
    assert kb_store.delete_version(u["id"], bad["id"]) is True
    assert kb_store.get_version(u["id"], bad["id"]) is None


def test_versions_and_activation_are_user_scoped():
    db.init()
    owner, other = db.create_user("kbowner", "h"), db.create_user("kbother", "h")
    v = kb_store.create_embedding(owner["id"], "V", "upload", "custom", "a.json")
    kb_store.mark_done(owner["id"], v["id"], 1)

    assert kb_store.list_versions(other["id"]) == []
    assert kb_store.get_version(other["id"], v["id"]) is None
    assert kb_store.set_active(other["id"], v["id"]) is False
    assert kb_store.mark_done(other["id"], v["id"], 99) is False
