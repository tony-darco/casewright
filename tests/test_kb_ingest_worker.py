"""kb_ingest.run_ingest: parse -> split -> embed -> mark_done/mark_error, driven
directly with a stub job (same style as test_versions.py's worker tests)."""

import json
from unittest import mock

from web import db
from web.services import kb_ingest, kb_store

_FAKE_SPEC = {
    "paths": {
        "/organizations": {"get": {"summary": "List orgs"}},
        "/organizations/{organizationId}/networks": {"get": {"summary": "List networks"}},
    }
}


class _StubJob:
    def __init__(self):
        self.events = []

    def emit(self, ev):
        self.events.append(ev)


def _fake_vector_store():
    store = mock.Mock()
    return store


def test_custom_split_embeds_and_marks_done():
    db.init()
    u = db.create_user("kbing1", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "spec.json")
    job = _StubJob()

    with mock.patch("rag.ingest.embed.embed_documents", return_value=2) as embed_mock:
        kb_ingest.run_ingest(job, u["id"], v["id"], json.dumps(_FAKE_SPEC).encode(),
                            "custom", "spec.json", {})

    embed_mock.assert_called_once()
    docs_arg = embed_mock.call_args.args[0]
    assert len(docs_arg) == 2
    assert docs_arg[0].metadata["endpoint_id"].startswith("GET ")

    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "done" and row["doc_count"] == 2
    assert job.events[-1] == {"type": "done", "status": "done", "doc_count": 2}


def test_langchain_split_embeds_and_marks_done():
    db.init()
    u = db.create_user("kbing2", "h")
    v = kb_store.create_embedding(u["id"], "S", "link", "langchain", "http://x/doc.txt")
    job = _StubJob()

    with mock.patch("rag.ingest.embed.embed_documents", return_value=3):
        kb_ingest.run_ingest(job, u["id"], v["id"], (b"hello world " * 500),
                            "langchain", "http://x/doc.txt", {})

    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "done" and row["doc_count"] == 3


def test_empty_split_result_marks_error():
    db.init()
    u = db.create_user("kbing3", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "notaspec.json")
    job = _StubJob()

    kb_ingest.run_ingest(job, u["id"], v["id"], b'{"not": "openapi"}', "custom",
                        "notaspec.json", {})

    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "error" and row["error_message"]
    assert job.events[-1]["type"] == "done" and job.events[-1]["status"] == "error"


def test_embed_failure_marks_error_not_raised():
    db.init()
    u = db.create_user("kbing4", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "spec.json")
    job = _StubJob()

    with mock.patch("rag.ingest.embed.embed_documents", side_effect=RuntimeError("chroma down")):
        kb_ingest.run_ingest(job, u["id"], v["id"], json.dumps(_FAKE_SPEC).encode(),
                            "custom", "spec.json", {})

    row = kb_store.get_version(u["id"], v["id"])
    assert row["status"] == "error" and "chroma down" in row["error_message"]
    assert job.events[-1] == {"type": "done", "status": "error", "message": "chroma down"}


def test_stage_events_emitted_in_order():
    db.init()
    u = db.create_user("kbing5", "h")
    v = kb_store.create_embedding(u["id"], "S", "upload", "custom", "spec.json")
    job = _StubJob()

    with mock.patch("rag.ingest.embed.embed_documents", return_value=2):
        kb_ingest.run_ingest(job, u["id"], v["id"], json.dumps(_FAKE_SPEC).encode(),
                            "custom", "spec.json", {})

    stages = [e["stage"] for e in job.events if e["type"] == "stage"]
    assert stages == ["parsing", "splitting", "embedding"]
