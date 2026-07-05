import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from embed import vector_store

if __name__ == "__main__":
    ans = vector_store.similarity_search_with_relevance_scores(
        query="Return wireless profile assigned to the given camera"
    )
    for doc, score in ans:
        print(score, doc.page_content)
