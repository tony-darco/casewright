"""Reciprocal Rank Fusion (the RRF in RAG-Fusion)."""

from collections import defaultdict

from langchain_core.documents import Document


def _doc_key(doc: Document) -> str:
    """Stable identity for de-duplicating a doc across ranked lists: prefer the
    endpoint id (set at ingest), fall back to raw content."""
    return doc.metadata.get("endpoint_id") or doc.page_content


def reciprocal_rank_fusion(ranked_lists, k: int = 60):
    """Fuse several ranked Document lists into one.

    Each document's fused score is the sum over lists of ``1 / (k + rank)``,
    with rank starting at 1. Returns ``[(Document, score), ...]`` sorted by
    score descending. ``k`` damps the influence of the very top ranks so that
    broad agreement across lists outweighs a single list's #1.
    """
    scores: dict[str, float] = defaultdict(float)
    doc_by_key: dict[str, Document] = {}
    for docs in ranked_lists:
        for rank, doc in enumerate(docs, start=1):
            key = _doc_key(doc)
            scores[key] += 1.0 / (k + rank)
            doc_by_key.setdefault(key, doc)
    ordered_keys = sorted(scores, key=lambda key: scores[key], reverse=True)
    return [(doc_by_key[key], scores[key]) for key in ordered_keys]
