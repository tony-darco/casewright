"""Pure split-function tests (no DB, no network): split_openapi_custom matches
write_one's existing contract; split_langchain chunks generically with stable ids.
"""

from unittest import mock

from rag.ingest.langchain_split import split_langchain
from rag.ingest.split import split_openapi_custom, write_one

_FAKE_SPEC = {
    "paths": {
        "/organizations": {
            "get": {"summary": "List orgs"},
            "post": {"summary": "Create org"},
            "parameters": [{"name": "x"}],   # path-level, not an operation -> must be skipped
        },
        "/organizations/{organizationId}/networks": {
            "get": {"summary": "List networks"},
        },
    }
}


def test_matches_write_ones_shape_on_the_same_fixture():
    """split_openapi_custom must produce the same Document shape write_one does —
    proves the pure version didn't quietly diverge from the original chunking.
    write_one also writes debug files under OUT_DIR as a side effect; mock that
    away so the test suite never touches data/specs/path_docs."""
    custom_docs = split_openapi_custom(_FAKE_SPEC, "my-source")

    write_one_docs = []
    with mock.patch("rag.ingest.split.Path.write_text"), \
         mock.patch("rag.ingest.split.Path.exists", return_value=False):
        for item in _FAKE_SPEC["paths"].items():
            write_one_docs.extend(write_one(item))

    assert len(custom_docs) == len(write_one_docs) == 3
    custom_ids = sorted(d.metadata["endpoint_id"] for d in custom_docs)
    write_one_ids = sorted(d.metadata["endpoint_id"] for d in write_one_docs)
    assert custom_ids == write_one_ids == [
        "GET /organizations", "GET /organizations/{organizationId}/networks", "POST /organizations",
    ]


def test_skips_path_level_parameters_key():
    docs = split_openapi_custom(_FAKE_SPEC, "src")
    methods = {d.metadata["method"] for d in docs if d.metadata["path"] == "/organizations"}
    assert methods == {"GET", "POST"}   # "parameters" never treated as a method


def test_metadata_shape_and_source_label():
    docs = split_openapi_custom(_FAKE_SPEC, "uploaded-spec.json")
    d = next(x for x in docs if x.metadata["endpoint_id"] == "GET /organizations")
    assert d.metadata["source"] == "uploaded-spec.json"
    assert d.metadata["method"] == "GET" and d.metadata["path"] == "/organizations"


def test_empty_or_non_openapi_document_yields_nothing():
    assert split_openapi_custom({}, "src") == []
    assert split_openapi_custom({"not": "openapi"}, "src") == []


def test_langchain_split_produces_multiple_chunks_for_long_text():
    text = "word " * 2000
    docs = split_langchain(text, "doc.txt", chunk_size=500, chunk_overlap=50)
    assert len(docs) > 1
    assert all(d.metadata["source"] == "doc.txt" for d in docs)
    assert [d.metadata["chunk_index"] for d in docs] == list(range(len(docs)))


def test_langchain_split_chunk_ids_are_stable_across_identical_input():
    text = "the quick brown fox " * 200
    docs1 = split_langchain(text, "doc.txt")
    docs2 = split_langchain(text, "doc.txt")
    assert [d.metadata["chunk_id"] for d in docs1] == [d.metadata["chunk_id"] for d in docs2]


def test_langchain_split_empty_text_yields_no_chunks():
    assert split_langchain("", "doc.txt") == []
