from rag.ingest.embed import vector_store


retriever = vector_store.as_retriever()

if __name__ == "__main__":
    ans = vector_store.similarity_search_with_relevance_scores(
        query="List the organizations"
    )
    for doc, score in ans:
        print(score, doc.page_content)
