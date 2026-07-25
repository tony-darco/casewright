"""The overrides dict handed to the pipeline merges the user's active knowledge-base
version's collection_name — the only wiring needed to make version-switching take
effect, since deps.get_pipeline's cache key differentiates on it automatically. This
lives in tui.generation.build_overrides (was app_view.app_generate_start).
"""

from web import db
from web.services import kb_store
from tui import generation


def test_prov_includes_collection_name_when_active_version_done():
    db.init()
    u = db.create_user("kbovr1", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "spec.json")
    kb_store.mark_done(u["id"], v["id"], 3)
    kb_store.set_active(u["id"], v["id"])

    prov = generation.build_overrides(u["id"])
    assert prov.get("collection_name") == v["collection_name"]


def test_prov_omits_collection_name_without_active_version():
    db.init()
    u = db.create_user("kbovr2", "h")

    prov = generation.build_overrides(u["id"])
    assert "collection_name" not in prov
