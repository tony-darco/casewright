"""Per-user persistence for knowledge-base embedding runs (SQLite `kb_versions` +
`kb_settings` tables), plus the storage-location *setting*, which lives in
config.yaml (web.settings) instead.

Every version call is scoped to user_id — a user only ever sees or controls their own
versions. Mirrors tests_store.py's shape: a two-phase create (insert, then a
follow-up update once the row id is known) since collection_name embeds the
version's own id (``kb_u{user_id}_v{version_id}``).
"""

from web import db, settings


def create_embedding(user_id, name, source_kind, split_method, source_label) -> dict:
    """Create an 'embedding' row and assign its collection_name from its own id."""
    with db.cursor() as conn:
        cur = conn.execute(
            "INSERT INTO kb_versions (user_id, name, source_kind, source_label, split_method) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, name.strip(), source_kind, source_label, split_method),
        )
        version_id = cur.lastrowid
        collection_name = f"kb_u{user_id}_v{version_id}"
        conn.execute(
            "UPDATE kb_versions SET collection_name = ? WHERE id = ?",
            (collection_name, version_id),
        )
    return {"id": version_id, "name": name.strip(), "collection_name": collection_name,
            "status": "embedding"}


def mark_done(user_id, version_id, doc_count) -> bool:
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE kb_versions SET status = 'done', doc_count = ? WHERE id = ? AND user_id = ?",
            (doc_count, version_id, user_id),
        )
        return cur.rowcount > 0


def mark_error(user_id, version_id, message) -> bool:
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE kb_versions SET status = 'error', error_message = ? WHERE id = ? AND user_id = ?",
            (message, version_id, user_id),
        )
        return cur.rowcount > 0


def list_versions(user_id) -> list:
    """This user's versions, newest first, each with an is_active flag."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT v.*, (s.active_version_id = v.id) AS is_active "
            "FROM kb_versions v LEFT JOIN kb_settings s ON s.user_id = v.user_id "
            "WHERE v.user_id = ? ORDER BY v.created_at DESC, v.id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(user_id, version_id):
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM kb_versions WHERE id = ? AND user_id = ?", (version_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def get_active(user_id):
    """The user's active version, or None — only ever a 'done' version, so a
    still-embedding or errored version can never end up backing generation."""
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT v.* FROM kb_settings s JOIN kb_versions v ON v.id = s.active_version_id "
            "WHERE s.user_id = ? AND v.status = 'done'",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def set_active(user_id, version_id) -> bool:
    """Activate version_id for this user. Refuses a version that isn't the user's
    own or isn't done embedding yet."""
    version = get_version(user_id, version_id)
    if not version or version["status"] != "done":
        return False
    with db.cursor() as conn:
        conn.execute(
            "INSERT INTO kb_settings (user_id, active_version_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET active_version_id = excluded.active_version_id",
            (user_id, version_id),
        )
    return True


def delete_version(user_id, version_id) -> bool:
    """Remove a version row — errored versions only (nothing was embedded for them,
    so deleting the row leaves no orphaned Chroma collection behind)."""
    with db.cursor() as conn:
        cur = conn.execute(
            "DELETE FROM kb_versions WHERE id = ? AND user_id = ? AND status = 'error'",
            (version_id, user_id),
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
