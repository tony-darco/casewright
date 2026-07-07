from concurrent.futures import ThreadPoolExecutor

from rag.provider import ProviderConfig, build_vector_store

# Same ProviderConfig the pipeline uses -> ingest writes exactly the store
# (dir, collection, embedding model) that retrieval reads back.
vector_store = build_vector_store(ProviderConfig())

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

