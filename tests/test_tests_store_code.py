"""Editable code view: persisting code edits back to the test."""

from web.services import tests_store


def test_update_code_persists_and_keeps_the_language():
    t = tests_store.create_test("T", "p", "f.test.py", "old code", "ts", [], [])

    assert tests_store.update_code(t["id"], "new code") is True
    row = tests_store.get_test(t["id"])
    assert row["code"] == "new code"
    assert row["language"] == "ts"


def test_update_code_missing_test():
    assert tests_store.update_code(99999, "x") is False
