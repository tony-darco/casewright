"""Logs page storage: per-test logs (persisted) and the app-wide ring buffer."""

import logging

from web.services import logs_store, tests_store


def test_per_test_logs_roundtrip():
    t = tests_store.create_test("My test", "prompt", "f.test.py", "code", "py", ["GET /x"], [])
    logs_store.record(t["id"], [
        {"stage": "start", "level": "info", "message": "began"},
        {"stage": "generate", "level": "error", "message": "boom"},
    ])
    groups = logs_store.tests_with_logs()
    assert len(groups) == 1
    g = groups[0]
    assert g["name"] == "My test"
    assert [e["stage"] for e in g["entries"]] == ["start", "generate"]
    assert g["entries"][1]["level"] == "error"


def test_record_ignores_empty():
    t = tests_store.create_test("t", "", "", "c", "py", [], [])
    logs_store.record(t["id"], None)
    logs_store.record(t["id"], [])
    assert logs_store.tests_with_logs() == []


def test_app_log_ring_buffer_captures_records():
    logs_store.install_app_log()
    logging.getLogger("web.test").info("hello-ring-buffer")
    assert any("hello-ring-buffer" in line["message"] for line in logs_store.app_log_lines())
