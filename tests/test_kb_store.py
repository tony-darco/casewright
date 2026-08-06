"""kb_store: CRUD for knowledge-base versions + the active-version pointer."""

from web.services import kb_store


def test_create_embedding_assigns_collection_name():
    v = kb_store.create_embedding("My Spec", "file", "custom", "spec.json")
    assert v["collection_name"] == f"kb_v{v['id']}"
    row = kb_store.get_version(v["id"])
    assert row["status"] == "embedding" and row["collection_name"] == v["collection_name"]


def test_mark_done_and_error():
    v = kb_store.create_embedding("S", "link", "langchain", "http://x")
    assert kb_store.mark_done(v["id"], 42)
    row = kb_store.get_version(v["id"])
    assert row["status"] == "done" and row["doc_count"] == 42

    v2 = kb_store.create_embedding("S2", "link", "langchain", "http://y")
    assert kb_store.mark_error(v2["id"], "boom")
    row2 = kb_store.get_version(v2["id"])
    assert row2["status"] == "error" and row2["error_message"] == "boom"


def test_list_versions_newest_first_with_active_flag():
    v1 = kb_store.create_embedding("V1", "upload", "custom", "a.json")
    kb_store.mark_done(v1["id"], 1)
    v2 = kb_store.create_embedding("V2", "upload", "custom", "b.json")
    kb_store.mark_done(v2["id"], 2)
    kb_store.set_active(v2["id"])

    versions = kb_store.list_versions()
    assert [v["id"] for v in versions] == [v2["id"], v1["id"]]
    assert versions[0]["is_active"] == 1
    assert versions[1]["is_active"] == 0


def test_set_active_refuses_non_done_version():
    v = kb_store.create_embedding("V", "upload", "custom", "a.json")
    assert kb_store.set_active(v["id"]) is False
    assert kb_store.get_active() is None


def test_get_active_only_returns_done_version():
    v = kb_store.create_embedding("V", "upload", "custom", "a.json")
    kb_store.mark_done(v["id"], 5)
    assert kb_store.set_active(v["id"]) is True
    active = kb_store.get_active()
    assert active["id"] == v["id"]


def test_delete_version_removes_errored_versions_only():
    """A finished version owns real vectors in Chroma; deleting only its row would
    orphan them, so it is kept."""
    done = kb_store.create_embedding("D", "file", "custom", "a.json")
    kb_store.mark_done(done["id"], 1)
    bad = kb_store.create_embedding("B", "file", "custom", "b.json")
    kb_store.mark_error(bad["id"], "boom")

    assert kb_store.delete_version(done["id"]) is False
    assert kb_store.delete_version(bad["id"]) is True
    assert kb_store.get_version(bad["id"]) is None


