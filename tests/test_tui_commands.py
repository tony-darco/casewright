"""The command bar: parsing the vocabulary and navigating between places. The
workspace is a single transcript (no tabs, no sidebar); slash commands move between
screens and write their output into that transcript. Runs headless via Pilot, offline.
"""

import asyncio

from tui.bootstrap import startup
from tui.app import CasewrightApp
from tui.commands import parse


def test_parse_longest_alias_and_args():
    assert parse("/new test").name == "new"          # multi-word beats "/new"
    assert parse("/test results").name == "output"
    assert parse("/open Login flow").args == "Login flow"
    assert parse("plain words") is None               # not a command
    assert parse("/frobnicate").name is None          # unknown slash word


def test_command_bar_navigates_and_writes_transcript():
    async def scenario():
        app = CasewrightApp(startup())
        async with app.run_test() as pilot:
            await pilot.pause()
            # The command bar is the default focus.
            assert app.focused is not None and app.focused.id == "command"

            # A section command pushes its own screen…
            app.dispatch_global(parse("/settings"))
            await pilot.pause()
            assert type(app.screen).__name__ == "SettingsScreen"

            # …and /back returns to the workspace transcript.
            app.dispatch_global(parse("/back"))
            await pilot.pause()
            ws = app.screen
            assert type(ws).__name__ == "WorkspaceScreen"

            # A workspace view command writes into the transcript.
            before = len(ws.query_one("#transcript").lines)
            app.dispatch_global(parse("/tests"))
            await pilot.pause()
            assert len(ws.query_one("#transcript").lines) > before

            # Another section is reachable from the workspace.
            app.dispatch_global(parse("/coverage"))
            await pilot.pause()
            assert type(app.screen).__name__ == "CoverageScreen"

    asyncio.run(scenario())
