"""app_generate_start merges the user's active knowledge-base version's
collection_name into the overrides dict passed to gen_registry.start (and from
there to get_pipeline) — the only wiring needed to make version-switching take
effect, since deps.get_pipeline's cache key differentiates on it automatically.
"""

from web import db
from web.routers import app_view
from web.services import kb_store


class _StubJob:
    def emit(self, ev):
        pass


def _run_and_capture_prov(monkeypatch, uid):
    """Drive app_generate_start directly (no HTTP layer), stubbing _run_generation
    to capture the prov dict it's handed, and running the job target synchronously."""
    captured_prov = {}

    def fake_run_generation(job, uid_, test_id, name, prompt, dev, devices_raw, language, meta, prov,
                            **kwargs):
        captured_prov.update(prov)

    def fake_start(test_id, user_id, target):
        target(None)
        return _StubJob()

    monkeypatch.setattr(app_view, "_run_generation", fake_run_generation)
    monkeypatch.setattr(app_view.gen_registry, "start", fake_start)
    monkeypatch.setattr(app_view.tests_store, "create_generating",
                        lambda *a, **k: {"id": 1, "name": "T"})

    app_view.app_generate_start(prompt="hello", devices="[]", language="py",
                                regen_of="", user={"id": uid})
    return captured_prov


def test_prov_includes_collection_name_when_active_version_done(monkeypatch):
    db.init()
    u = db.create_user("kbovr1", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "spec.json")
    kb_store.mark_done(u["id"], v["id"], 3)
    kb_store.set_active(u["id"], v["id"])

    prov = _run_and_capture_prov(monkeypatch, u["id"])
    assert prov.get("collection_name") == v["collection_name"]


def test_prov_omits_collection_name_without_active_version(monkeypatch):
    db.init()
    u = db.create_user("kbovr2", "h")

    prov = _run_and_capture_prov(monkeypatch, u["id"])
    assert "collection_name" not in prov
