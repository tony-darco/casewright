"""Tests for the Logs page storage (issue #8): per-test logs (persisted, per-user)
and the app-wide ring buffer."""

import logging

from web import db
from web.services import logs_store, tests_store


def _user(username):
    return db.create_user(username, "hash")


def test_per_test_logs_roundtrip():
    db.init()
    u = _user("loguser")
    t = tests_store.create_test(u["id"], "My test", "prompt", "f.test.py", "code", "py", ["GET /x"], [])
    logs_store.record(u["id"], t["id"], [
        {"stage": "start", "level": "info", "message": "began"},
        {"stage": "generate", "level": "error", "message": "boom"},
    ])
    groups = logs_store.tests_with_logs(u["id"])
    assert len(groups) == 1
    g = groups[0]
    assert g["name"] == "My test"
    assert [e["stage"] for e in g["entries"]] == ["start", "generate"]
    assert g["entries"][1]["level"] == "error"


def test_per_test_logs_are_user_scoped():
    db.init()
    u1, u2 = _user("scope1"), _user("scope2")
    t1 = tests_store.create_test(u1["id"], "t1", "", "", "c", "py", [], [])
    logs_store.record(u1["id"], t1["id"], [{"stage": "s", "level": "info", "message": "m"}])
    assert logs_store.tests_with_logs(u2["id"]) == []
    assert len(logs_store.tests_with_logs(u1["id"])) == 1


def test_record_ignores_empty():
    db.init()
    u = _user("emptyuser")
    t = tests_store.create_test(u["id"], "t", "", "", "c", "py", [], [])
    logs_store.record(u["id"], t["id"], None)
    logs_store.record(u["id"], t["id"], [])
    assert logs_store.tests_with_logs(u["id"]) == []


def test_app_log_ring_buffer_captures_records():
    logs_store.install_app_log()
    logging.getLogger("web.test").info("hello-ring-buffer")
    assert any("hello-ring-buffer" in line["message"] for line in logs_store.app_log_lines())
