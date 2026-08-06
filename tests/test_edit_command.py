"""/edit --code and /edit --prompt: the in-app editor's round trip.

The risk here is asymmetric. Failing to save loses one edit; saving when the user
cancelled, or writing the prompt over the code, loses work that was already there —
so cancel and the code/prompt separation are what these cover.
"""

import asyncio

from tui.app import CasewrightApp
from tui.bootstrap import startup
from tui.commands import parse
from web.services import tests_store


def _seed() -> int:
    t = tests_store.create_test("T", "the original prompt", "f.test.py",
                                "line one\nline two\n", "py", ["GET /x"], [])
    tests_store.add_version(t["id"], "the original prompt", "f.test.py",
                            "line one\nline two\n", "py", ["GET /x"], None)
    return t["id"]


def _run(scenario):
    async def main():
        startup()
        app = CasewrightApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            await scenario(app, pilot)
    asyncio.run(main())


async def _open_editor(app, pilot, ws, args):
    ws.on_command(parse(f"/edit {args}"))
    await pilot.pause()
    await pilot.pause()
    return app.screen


def test_saving_code_stores_a_new_version():
    async def scenario(app, pilot):
        tid = _seed()
        ws = app.screen
        ws.load_test(tid, show_code=False)
        await pilot.pause()

        editor = await _open_editor(app, pilot, ws, "--code")
        editor.query_one("#editor-area").text = "line one\nedited\n"
        editor.action_save()
        await pilot.pause()
        await pilot.pause()

        assert tests_store.get_test(tid)["code"] == "line one\nedited\n"
        assert len(tests_store.list_versions(tid)) == 2   # the original is still reachable
        assert tests_store.get_version(tid, 0)["code"] == "line one\nline two\n"
    _run(scenario)


def test_cancelling_changes_nothing():
    """Escaping out must not be read as 'save an empty file'."""
    async def scenario(app, pilot):
        tid = _seed()
        ws = app.screen
        ws.load_test(tid, show_code=False)
        await pilot.pause()

        editor = await _open_editor(app, pilot, ws, "--code")
        editor.query_one("#editor-area").text = "throw this away"
        editor.action_cancel()
        await pilot.pause()
        await pilot.pause()

        assert tests_store.get_test(tid)["code"] == "line one\nline two\n"
        assert len(tests_store.list_versions(tid)) == 1
    _run(scenario)


def test_editing_the_prompt_leaves_the_code_alone():
    """The two are deliberately allowed to disagree — rewriting the prompt is how you
    set up the next generation, not a reason to discard working code."""
    async def scenario(app, pilot):
        tid = _seed()
        ws = app.screen
        ws.load_test(tid, show_code=False)
        await pilot.pause()

        editor = await _open_editor(app, pilot, ws, "--prompt")
        assert editor.query_one("#editor-area").text == "the original prompt"
        editor.query_one("#editor-area").text = "a rewritten prompt"
        editor.action_save()
        await pilot.pause()
        await pilot.pause()

        test = tests_store.get_test(tid)
        assert test["prompt"] == "a rewritten prompt"
        assert test["code"] == "line one\nline two\n"
        assert len(tests_store.list_versions(tid)) == 1   # a prompt edit isn't a version
    _run(scenario)


def test_edit_needs_a_loaded_test():
    async def scenario(app, pilot):
        ws = app.screen
        assert ws.current_test_id is None
        ws.on_command(parse("/edit --code"))
        await pilot.pause()
        assert type(app.screen).__name__ == "WorkspaceScreen"   # no editor opened
    _run(scenario)


def test_edit_refuses_a_read_only_version():
    """Older versions are snapshots; editing one would silently fork history."""
    async def scenario(app, pilot):
        tid = _seed()
        tests_store.add_version(tid, "p", "f.test.py", "v1 code\n", "py", [], None)
        ws = app.screen
        ws.load_test(tid, version_no=0, show_code=False)
        await pilot.pause()
        assert ws._readonly

        ws.on_command(parse("/edit --code"))
        await pilot.pause()
        assert type(app.screen).__name__ == "WorkspaceScreen"   # no editor opened
    _run(scenario)
