"""kb_store storage location (local vs. remote Chroma) + ProviderConfig/
build_vector_store's remote branch (chromadb.HttpClient mocked, no real
connection)."""

from unittest import mock

from web.services import kb_store


def test_default_storage_is_local():
    assert kb_store.get_storage() == {"storage_kind": "local", "storage_url": ""}


def test_set_and_get_remote_storage():
    kb_store.set_storage("remote", "http://localhost:8000")
    assert kb_store.get_storage() == {"storage_kind": "remote", "storage_url": "http://localhost:8000"}


def test_set_storage_does_not_clobber_active_version():
    v = kb_store.create_embedding("S", "upload", "custom", "spec.json")
    kb_store.mark_done(v["id"], 1)
    kb_store.set_active(v["id"])

    kb_store.set_storage("remote", "http://localhost:8000")
    assert kb_store.get_active()["id"] == v["id"]


def test_build_vector_store_uses_remote_client_when_chroma_url_set():
    from rag.provider import ProviderConfig, build_vector_store

    cfg = ProviderConfig(persist_dir="/tmp/x", chroma_url="http://myhost:9000", collection_name="c1")
    fake_client = mock.Mock()
    with mock.patch("chromadb.HttpClient", return_value=fake_client) as http_client, \
         mock.patch("langchain_chroma.Chroma") as chroma_cls:
        build_vector_store(cfg, embeddings=mock.Mock())

    http_client.assert_called_once_with(host="myhost", port=9000, ssl=False)
    assert chroma_cls.call_args.kwargs["client"] is fake_client
    assert chroma_cls.call_args.kwargs["collection_name"] == "c1"


def test_build_vector_store_uses_local_persist_dir_by_default():
    from rag.provider import ProviderConfig, build_vector_store

    cfg = ProviderConfig(persist_dir="/tmp/x", collection_name="c1")
    with mock.patch("langchain_chroma.Chroma") as chroma_cls:
        build_vector_store(cfg, embeddings=mock.Mock())

    assert "client" not in chroma_cls.call_args.kwargs
    assert chroma_cls.call_args.kwargs["persist_directory"] == "/tmp/x"
