"""Adopt an already-embedded Chroma collection as a knowledge-base version.

Two stores back the knowledge base, and only one of them is the database: SQLite
records *what* knowledge bases exist (name, version, doc count, which is active),
while Chroma holds the actual vectors. They can get out of step in two ways —
the CLI ingest path (``python -m rag.ingest.embed``) writes vectors and never a
row, and a fresh database starts with no rows at all while ``data/chroma``
survives untouched.

Either way the result is the same: minutes of embedding work sitting on disk that
the app refuses to show. Adopting writes the missing row instead of re-embedding.
It is deliberately conservative — it runs only when there are no versions at all,
so it can never shadow or duplicate a knowledge base someone actually built.

Reads Chroma through ``chromadb`` directly rather than ``rag.provider``: counting
documents needs no embedding function, and startup must not depend on a reachable
model backend.
"""

import logging

from web.services import kb_store

logger = logging.getLogger("web.kb_bootstrap")

# The collection the CLI ingest path writes to (rag.provider.DEFAULT_COLLECTION).
_LEGACY_COLLECTION = "meraki_openapi"
_LEGACY_NAME = "Meraki OpenAPI spec"
_LEGACY_SOURCE = "data/specs/meraki_open_api_spec.json"


def _persist_dir() -> str:
    from rag.provider import ProviderConfig

    return ProviderConfig().persist_dir


def _collection_count(name: str) -> int:
    """Documents in a local Chroma collection, or 0 if it isn't there. Never raises:
    a missing/unreadable store just means there's nothing to adopt."""
    try:
        import chromadb

        client = chromadb.PersistentClient(path=_persist_dir())
        existing = {getattr(c, "name", c) for c in client.list_collections()}
        if name not in existing:
            return 0
        return client.get_collection(name).count()
    except Exception:
        logger.debug("could not inspect the local Chroma store", exc_info=True)
        return 0


def adopt_existing_collection() -> dict | None:
    """Register the pre-existing collection as version 1 and activate it.

    Returns the new version, or None when there's nothing to adopt (versions already
    exist, or the store has no such collection). Safe to call on every startup.
    """
    if kb_store.list_versions():
        return None
    count = _collection_count(_LEGACY_COLLECTION)
    if not count:
        return None
    version = kb_store.adopt_collection(_LEGACY_NAME, _LEGACY_COLLECTION, count,
                                        _LEGACY_SOURCE)
    kb_store.set_active(version["id"])
    logger.info("adopted the existing '%s' collection (%d docs) as knowledge base v%s",
                _LEGACY_COLLECTION, count, version["id"])
    return version
