"""The overrides dict handed to the pipeline merges the active knowledge-base
version's collection_name — the only wiring needed to make version-switching take
effect, since deps.get_pipeline's cache key differentiates on it automatically.
Lives in tui.generation.build_overrides.
"""

from web.services import kb_store
from tui import generation


def test_prov_includes_collection_name_when_active_version_done():
    v = kb_store.create_embedding("S", "file", "custom", "spec.json")
    kb_store.mark_done(v["id"], 3)
    kb_store.set_active(v["id"])

    assert generation.build_overrides().get("collection_name") == v["collection_name"]


def test_prov_omits_collection_name_without_active_version():
    assert "collection_name" not in generation.build_overrides()
