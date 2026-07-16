from concurrent.futures import ThreadPoolExecutor

from rag.provider import ProviderConfig, build_vector_store

# Same ProviderConfig the pipeline uses -> ingest writes exactly the store
# (dir, collection, embedding model) that retrieval reads back.
vector_store = build_vector_store(ProviderConfig())


def embed_documents(documents: list, provider_config) -> int:
    """Pure embed step (Knowledge Base feature): builds a Chroma store from
    ``provider_config`` (the caller sets a per-version ``collection_name``) and adds
    ``documents`` with stable ids, so re-running against the same collection upserts.
    Falls back from endpoint_id (custom split) to chunk_id (langchain split) to a
    sequential index if neither is present. Returns the number of documents added."""
    if not documents:
        return 0
    store = build_vector_store(provider_config)
    ids = [doc.metadata.get("endpoint_id") or doc.metadata.get("chunk_id") or str(i)
           for i, doc in enumerate(documents)]
    store.add_documents(documents, ids=ids)
    return len(documents)


if __name__ == "__main__":
    from rag.ingest.split import paths_data, write_one

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(write_one, paths_data.items())

    documents = [doc for docs in results for doc in docs]
    if documents:
        # Stable ids keyed on endpoint_id so re-ingest upserts (replaces) each
        # endpoint instead of appending a duplicate copy of the whole corpus.
        ids = [doc.metadata["endpoint_id"] for doc in documents]
        vector_store.add_documents(documents, ids=ids)

