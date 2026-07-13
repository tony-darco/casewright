"""Background worker for a knowledge-base embedding run (Knowledge Base feature).

Parses the fetched/uploaded content, splits it (custom OpenAPI split or generic
LangChain split), embeds it into a per-version Chroma collection, and records the
outcome — emitting stage events onto a kb_registry Job as it goes so the Settings UI
can show live progress over SSE.

The ``rag`` package (LangChain/Chroma/Ollama) is imported lazily inside
``run_ingest``, not at module level — mirroring ``web.deps.get_pipeline`` — so that
importing this module (and therefore ``web.routers.settings``) never requires the
RAG stack to be installed. A deployment without it simply gets a clear per-run
error the first time someone tries to ingest, not a startup crash.
"""

import json
import logging

import yaml

from web.services import kb_store

logger = logging.getLogger("web.kb_ingest")


def _parse_spec_dict(text: str) -> dict:
    """Best-effort JSON, falling back to YAML (OpenAPI specs are commonly either)."""
    try:
        return json.loads(text)
    except ValueError:
        return yaml.safe_load(text) or {}


def run_ingest(job, user_id, version_id, content: bytes, split_method: str,
              source_label: str, provider_overrides: dict) -> None:
    """Parse -> split -> embed -> mark_done/mark_error. Emits {"type": "stage", ...}
    events onto ``job`` and a final {"type": "done", ...}."""
    def stage(name, **extra):
        job.emit({"type": "stage", "stage": name, **extra})

    try:
        from rag.ingest.embed import embed_documents
        from rag.ingest.langchain_split import split_langchain
        from rag.ingest.split import split_openapi_custom
        from rag.provider import ProviderConfig

        stage("parsing")
        text = content.decode("utf-8", errors="replace")

        stage("splitting")
        if split_method == "custom":
            spec = _parse_spec_dict(text)
            docs = split_openapi_custom(spec, source_label)
        else:
            docs = split_langchain(text, source_label)

        if not docs:
            raise ValueError(
                "No content could be chunked from this document — check it matches "
                "the split method you chose."
            )

        stage("embedding", doc_count=len(docs))
        version = kb_store.get_version(user_id, version_id)
        cfg = ProviderConfig(**provider_overrides)
        cfg.collection_name = version["collection_name"]
        n = embed_documents(docs, cfg)

        kb_store.mark_done(user_id, version_id, n)
        job.emit({"type": "done", "status": "done", "doc_count": n})
    except Exception as exc:  # noqa: BLE001 — reported as the version's error, never a 500
        message = str(exc) or exc.__class__.__name__
        logger.exception("kb ingest %s failed", version_id)
        kb_store.mark_error(user_id, version_id, message)
        job.emit({"type": "done", "status": "error", "message": message})
