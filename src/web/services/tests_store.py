"""Persistence for generated tests (SQLite `tests` table).

The flexible bits (grounded endpoints, device refs) are stored as JSON columns.
This module is the seam if tests ever move to a document store later.
"""

import json

from web import db


def create_test(name, prompt, file_name, code, language, endpoints, devices, validation=None):
    with db.cursor() as conn:
        test_id = conn.execute(
            "INSERT INTO tests (name, prompt, file_name, code, language, "
            "endpoints_json, devices_json, validation_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name.strip(), prompt, file_name, code, language,
             json.dumps(endpoints or []), json.dumps(devices or []),
             json.dumps(validation) if validation else ""),
        ).lastrowid
    return {"id": test_id, "name": name.strip()}


def create_generating(name, prompt, language, devices):
    """Create a placeholder row for a test whose generation is starting, so it appears
    in the library immediately and can be reattached to. Filled in by finish_test (or
    removed by delete_test on failure)."""
    with db.cursor() as conn:
        test_id = conn.execute(
            "INSERT INTO tests (name, prompt, language, devices_json, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (name.strip(), prompt, language, json.dumps(devices or []), "generating"),
        ).lastrowid
    return {"id": test_id, "name": name.strip(), "status": "generating"}


def finish_test(test_id, file_name, code, endpoints, validation, status="done",
                hardware=None, gen_meta=None, dep_endpoints=None):
    """Fill in a generating row with the generated result and flip its status. Also
    stores the LLM-decided hardware requirements and the generation-time identifiers
    (org/base URL/network ids) the runner later substitutes (Run feature).

    ``endpoints`` are the test's targets; ``dep_endpoints`` are the prerequisites it
    calls to set them up. Both count as usage in the coverage tree, kept apart so the
    tree can say which role an endpoint plays in a given test."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET file_name = ?, code = ?, endpoints_json = ?, "
            "dep_endpoints_json = ?, validation_json = ?, hardware_json = ?, "
            "gen_meta_json = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
            (file_name, code, json.dumps(endpoints or []), json.dumps(dep_endpoints or []),
             json.dumps(validation) if validation else "", json.dumps(hardware or []),
             json.dumps(gen_meta or {}), status, test_id),
        )
        return cur.rowcount > 0


def update_hardware(test_id, hardware):
    """Persist edits to a test's hardware requirements (the run config)."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET hardware_json = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(hardware or []), test_id),
        )
        return cur.rowcount > 0


def set_run_config(test_id, run_source, source_network_id):
    """Persist a test's per-run configuration (build source + chosen example network)."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET run_source = ?, source_network_id = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (run_source, source_network_id, test_id),
        )
        return cur.rowcount > 0


def delete_test(test_id):
    """Remove a test (used when a generation fails and leaves an empty placeholder)."""
    with db.cursor() as conn:
        cur = conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        return cur.rowcount > 0


def abandon_generation(test_id):
    """A regenerate/repair failed: flip the row out of 'generating' so it isn't stuck,
    leaving the stored code and version history untouched. A brand-new test is deleted
    instead (it never had content to keep) — see app_flow.persist_generation_result."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET status = 'done', updated_at = datetime('now') WHERE id = ?",
            (test_id,),
        )
        return cur.rowcount > 0


def restart_generation(test_id, prompt, language):
    """Reuse an existing test row for a regenerate: flip it back to 'generating' and
    update the prompt/language. Returns the row stub, or None if the test is gone."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET status = 'generating', prompt = ?, language = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (prompt, language, test_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT name FROM tests WHERE id = ?", (test_id,)).fetchone()
    return {"id": test_id, "name": row["name"], "status": "generating"}


# --- version history -------------------------------------------------------------

def add_version(test_id, prompt, file_name, code, language, endpoints, validation):
    """Append a snapshot as the next version of a test; returns its 0-based version_no."""
    with db.cursor() as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM test_versions WHERE test_id = ?", (test_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO test_versions (test_id, version_no, prompt, file_name, code, "
            "language, endpoints_json, validation_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (test_id, n, prompt, file_name, code, language,
             json.dumps(endpoints or []), json.dumps(validation) if validation else ""),
        )
    return n


def list_versions(test_id):
    """version_no + created_at for each version of a test, oldest first."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT version_no, created_at FROM test_versions WHERE test_id = ? "
            "ORDER BY version_no",
            (test_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(test_id, version_no):
    """A single version snapshot, or None."""
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM test_versions WHERE test_id = ? AND version_no = ?",
            (test_id, version_no),
        ).fetchone()
    return dict(row) if row else None


def list_tests():
    """Lightweight rows for the library list (no code payload), newest first."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, name, status, created_at FROM tests ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_endpoint_usage():
    """Each finished test's id/name plus the endpoint ids it uses — the source the
    coverage tree inverts into an endpoint -> tests index. Generating rows are skipped:
    their endpoint lists aren't filled in yet."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, name, endpoints_json, dep_endpoints_json FROM tests "
            "WHERE status = 'done' ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_test(test_id):
    """Full row, or None if not found."""
    with db.cursor() as conn:
        row = conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()
    return dict(row) if row else None


def update_code(test_id, code):
    """Persist edits to a test's code. Returns True if the test exists."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET code = ?, updated_at = datetime('now') WHERE id = ?",
            (code, test_id),
        )
        return cur.rowcount > 0


def rename_test(test_id, name):
    with db.cursor() as conn:
        conn.execute(
            "UPDATE tests SET name = ?, updated_at = datetime('now') WHERE id = ?",
            (name.strip(), test_id),
        )
    return get_test(test_id)
