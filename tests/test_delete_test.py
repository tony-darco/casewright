"""Deleting a test: the route removes the test and everything under it (versions,
runs, and their logs cascade via foreign keys), scoped to the owner."""

from web import db
from web.routers import app_view
from web.services import run_logs_store, runs_store, tests_store


def _counts(conn, test_id):
    def n(sql):
        return conn.execute(sql, (test_id,)).fetchone()[0]
    return {
        "tests": n("SELECT COUNT(*) FROM tests WHERE id = ?"),
        "versions": n("SELECT COUNT(*) FROM test_versions WHERE test_id = ?"),
        "runs": n("SELECT COUNT(*) FROM runs WHERE test_id = ?"),
        "test_logs": n("SELECT COUNT(*) FROM test_logs WHERE test_id = ?"),
    }


def _seed_test_with_history(uid):
    t = tests_store.create_test(uid, "T", "p", "f.py", "code", "py", ["GET /x"], [])
    tid = t["id"]
    tests_store.add_version(tid, "p", "f.py", "code", "py", ["GET /x"], None)
    run = runs_store.create_run(uid, tid, "example", version_no=0)
    run_logs_store.record(run["id"], [{"stage": "run", "level": "info", "message": "hi"}])
    with db.cursor() as conn:
        conn.execute("INSERT INTO test_logs (user_id, test_id, stage, message) VALUES (?, ?, 'gen', 'x')",
                     (uid, tid))
    return tid, run["id"]


def test_delete_removes_the_test_and_cascades():
    db.init()
    uid = db.create_user("delowner", "h")["id"]
    tid, run_id = _seed_test_with_history(uid)

    with db.cursor() as conn:
        assert _counts(conn, tid)["tests"] == 1

    resp = app_view.delete_test(tid, {"id": uid})
    assert resp.status_code == 204

    with db.cursor() as conn:
        after = _counts(conn, tid)
        assert after == {"tests": 0, "versions": 0, "runs": 0, "test_logs": 0}
        # run_logs hang off runs(id) — they must cascade too
        assert conn.execute("SELECT COUNT(*) FROM run_logs WHERE run_id = ?", (run_id,)).fetchone()[0] == 0


def test_delete_is_scoped_to_the_owner():
    db.init()
    owner = db.create_user("delowner2", "h")["id"]
    other = db.create_user("delother", "h")["id"]
    tid, _ = _seed_test_with_history(owner)

    resp = app_view.delete_test(tid, {"id": other})   # not their test
    assert resp.status_code == 404
    assert tests_store.get_test(owner, tid) is not None   # still there
