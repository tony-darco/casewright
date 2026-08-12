"""Persistence for knowledge-base embedding runs (SQLite `kb_versions` +
`kb_settings`), plus the storage-location *setting*, which lives in config.yaml
(web.settings) instead.

A knowledge base is identified by its **version**, not by an owner: re-embedding
a spec appends a new row and a new Chroma collection (``kb_v{id}``) rather than
overwriting the last one, so you can always switch back to the corpus a test was
originally grounded against.

Two-phase create (insert, then a follow-up update once the row id is known) since
the collection name embeds the version's own id.
"""

from web import db, settings


def create_embedding(name, source_kind, split_method, source_label) -> dict:
    """Create an 'embedding' row and assign its collection_name from its own id."""
    with db.cursor() as conn:
        cur = conn.execute(
            "INSERT INTO kb_versions (name, source_kind, source_label, split_method) "
            "VALUES (?, ?, ?, ?)",
            (name.strip(), source_kind, source_label, split_method),
        )
        version_id = cur.lastrowid
        collection_name = f"kb_v{version_id}"
        conn.execute(
            "UPDATE kb_versions SET collection_name = ? WHERE id = ?",
            (collection_name, version_id),
        )
    return {"id": version_id, "name": name.strip(), "collection_name": collection_name,
            "status": "embedding"}


def adopt_collection(name, collection_name, doc_count, source_label,
                     split_method="custom") -> dict:
    """Register an already-embedded Chroma collection as a finished version.

    For a corpus that exists in the vector store but has no row describing it — the
    CLI ingest path (``python -m rag.ingest.embed``) writes vectors and nothing else,
    and a fresh database starts with no rows at all. Adopting is instant and needs no
    model backend; re-embedding the same spec would cost minutes of Ollama time to
    reproduce vectors already sitting on disk."""
    with db.cursor() as conn:
        cur = conn.execute(
            "INSERT INTO kb_versions (name, source_kind, source_label, split_method, "
            "collection_name, doc_count, status) VALUES (?, 'file', ?, ?, ?, ?, 'done')",
            (name.strip(), source_label, split_method, collection_name, doc_count),
        )
    return {"id": cur.lastrowid, "name": name.strip(),
            "collection_name": collection_name, "status": "done"}


def mark_done(version_id, doc_count) -> bool:
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE kb_versions SET status = 'done', doc_count = ? WHERE id = ?",
            (doc_count, version_id),
        )
        return cur.rowcount > 0


def mark_error(version_id, message) -> bool:
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE kb_versions SET status = 'error', error_message = ? WHERE id = ?",
            (message, version_id),
        )
        return cur.rowcount > 0


def list_versions() -> list:
    """Every version, newest first, each with an is_active flag."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT v.*, (s.active_version_id = v.id) AS is_active "
            "FROM kb_versions v LEFT JOIN kb_settings s ON s.id = 1 "
            "ORDER BY v.created_at DESC, v.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(version_id):
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM kb_versions WHERE id = ?", (version_id,)
        ).fetchone()
    return dict(row) if row else None


def get_active():
    """The active version, or None — only ever a 'done' version, so a still-embedding
    or errored version can never end up backing generation."""
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT v.* FROM kb_settings s JOIN kb_versions v ON v.id = s.active_version_id "
            "WHERE s.id = 1 AND v.status = 'done'"
        ).fetchone()
    return dict(row) if row else None


def set_active(version_id) -> bool:
    """Activate version_id. Refuses a version that isn't done embedding yet."""
    version = get_version(version_id)
    if not version or version["status"] != "done":
        return False
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO kb_settings (id, active_version_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET active_version_id = excluded.active_version_id",
            (version_id,),
        )
    return True


def delete_version(version_id) -> bool:
    """Remove a version row — errored versions only (nothing was embedded for them,
    so deleting the row leaves no orphaned Chroma collection behind)."""
    with db.cursor() as conn:
        cur = conn.execute(
            "DELETE FROM kb_versions WHERE id = ? AND status = 'error'", (version_id,)
        )
        return cur.rowcount > 0


def get_storage() -> dict:
    """Where the vector store lives: {'storage_kind': 'local'|'remote',
    'storage_url': ...}. 'local' (the default) means the shared AUTOTEST_DATA_DIR
    persist directory; 'remote' means a Chroma server URL. A setting, so it lives in
    config.yaml — unlike the versions above, which are records."""
    return settings.section("knowledge_base")


def set_storage(storage_kind, storage_url) -> None:
    settings.save("knowledge_base", {"storage_kind": storage_kind,
                                     "storage_url": storage_url.strip()})
