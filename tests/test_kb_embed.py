"""embed_documents: id-fallback order and that collection_name flows through the
given provider_config, with rag.provider.build_vector_store mocked (no real
Chroma/Ollama calls)."""

from unittest import mock

from langchain_core.documents import Document

import rag.ingest.embed as embed_module


def _fake_config(collection_name="kb_u1_v1"):
    cfg = mock.Mock()
    cfg.collection_name = collection_name
    return cfg


def test_empty_documents_short_circuits():
    with mock.patch.object(embed_module, "build_vector_store") as build:
        n = embed_module.embed_documents([], _fake_config())
    build.assert_not_called()
    assert n == 0


def test_id_fallback_order_and_collection_name_passthrough():
    store = mock.Mock()
    docs = [
        Document(page_content="a", metadata={"endpoint_id": "GET /x"}),
        Document(page_content="b", metadata={"chunk_id": "abc-0"}),
        Document(page_content="c", metadata={}),
    ]
    cfg = _fake_config("kb_u7_v9")

    with mock.patch.object(embed_module, "build_vector_store", return_value=store) as build:
        n = embed_module.embed_documents(docs, cfg)

    build.assert_called_once_with(cfg)
    assert n == 3
    ids = store.add_documents.call_args.kwargs["ids"]
    assert ids == ["GET /x", "abc-0", "2"]


def test_endpoint_id_preferred_over_chunk_id():
    store = mock.Mock()
    docs = [Document(page_content="a", metadata={"endpoint_id": "GET /x", "chunk_id": "zzz"})]
    with mock.patch.object(embed_module, "build_vector_store", return_value=store):
        embed_module.embed_documents(docs, _fake_config())
    assert store.add_documents.call_args.kwargs["ids"] == ["GET /x"]
