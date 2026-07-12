"""Per-user persistence for generated tests (SQLite `tests` table).

Every call is scoped to user_id — a user only ever sees or edits their own tests.
The flexible bits (grounded endpoints, @device refs) are stored as JSON columns.
This module is the seam if tests ever move to a document store later.
"""

import json

from web import db


def create_test(user_id, name, prompt, file_name, code, language, endpoints, devices):
    with db.cursor() as conn:
        cur = conn.execute(
            "INSERT INTO tests (user_id, name, prompt, file_name, code, language, "
            "endpoints_json, devices_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, name.strip(), prompt, file_name, code, language,
             json.dumps(endpoints or []), json.dumps(devices or [])),
        )
        test_id = cur.lastrowid
    return {"id": test_id, "name": name.strip()}


def list_tests(user_id):
    """Lightweight rows for the sidebar (no code payload), newest first."""
    with db.cursor() as conn:
        rows = conn.execute(
            "SELECT id, name, created_at FROM tests WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_test(user_id, test_id):
    """Full row (scoped to the user) or None if not found / not theirs."""
    with db.cursor() as conn:
        row = conn.execute(
            "SELECT * FROM tests WHERE id = ? AND user_id = ?", (test_id, user_id)
        ).fetchone()
    return dict(row) if row else None


def update_code(user_id, test_id, code):
    """Persist the user's edits to a test's code. Scoped to the owner; returns True
    if a row was updated (the test exists and belongs to the user)."""
    with db.cursor() as conn:
        cur = conn.execute(
            "UPDATE tests SET code = ?, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (code, test_id, user_id),
        )
        return cur.rowcount > 0


def rename_test(user_id, test_id, name):
    with db.cursor() as conn:
        conn.execute(
            "UPDATE tests SET name = ?, updated_at = datetime('now') "
            "WHERE id = ? AND user_id = ?",
            (name.strip(), test_id, user_id),
        )
    return get_test(user_id, test_id)
