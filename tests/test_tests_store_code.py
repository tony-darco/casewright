"""Editable Code tab: persisting code edits back to the test (user-scoped)."""

from web import db
from web.services import tests_store


def _user(username):
    return db.create_user(username, "hash")


def test_update_code_persists_and_is_scoped():
    db.init()
    owner, other = _user("codeowner"), _user("codeother")
    t = tests_store.create_test(owner["id"], "T", "p", "f.test.py", "old code", "ts", [], [])

    # owner can update; language is preserved
    assert tests_store.update_code(owner["id"], t["id"], "new code") is True
    row = tests_store.get_test(owner["id"], t["id"])
    assert row["code"] == "new code"
    assert row["language"] == "ts"

    # a different user cannot touch it, and the code is unchanged
    assert tests_store.update_code(other["id"], t["id"], "hacked") is False
    assert tests_store.get_test(owner["id"], t["id"])["code"] == "new code"


def test_update_code_missing_test():
    db.init()
    u = _user("codemissing")
    assert tests_store.update_code(u["id"], 99999, "x") is False
