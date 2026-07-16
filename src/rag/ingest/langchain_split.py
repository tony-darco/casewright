"""LangChain split (Knowledge Base feature): generic recursive-character chunking
for any document shape, as the alternative to the OpenAPI-only custom split
(rag.ingest.split.split_openapi_custom). No assumption about document structure —
suitable for non-OpenAPI documents, or an OpenAPI document the user just wants
chunked plainly instead of split per endpoint.
"""

import hashlib

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_langchain(text: str, source_label: str, chunk_size: int = 1500,
                    chunk_overlap: int = 150) -> list:
    """Split ``text`` into chunks. Each chunk's id is a content hash + index, since
    there's no endpoint_id here — stable across re-ingests of identical content, so
    re-running ingest on the same document upserts rather than duplicates."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = splitter.split_text(text or "")
    docs = []
    for i, chunk in enumerate(chunks):
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
        docs.append(Document(
            page_content=chunk,
            metadata={"source": source_label, "chunk_index": i, "chunk_id": f"{digest}-{i}"},
        ))
    return docs
