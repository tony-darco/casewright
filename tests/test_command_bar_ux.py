"""The command bar's sense of place: scoped suggestions, history, and /help.

The bar is the whole interface, so what it offers has to match where you are — a
menu listing /run with no test loaded is a menu you have to read past.
"""

import asyncio

from tui.app import CasewrightApp
from tui.bootstrap import startup
from tui.commands import CommandBar, available, parse, parse_edit, suggest

_parse = parse


# --- scoping ----------------------------------------------------------------------

def test_home_does_not_offer_test_commands():
    names = [n for n, _ in available("home")]
    assert "run" not in names and "edit" not in names and "code" not in names
    assert "tests" in names and "kb" in names and "help" in names


def test_a_loaded_test_unlocks_its_commands():
    names = [n for n, _ in available("test")]
    assert {"run", "edit", "code", "config", "output", "version", "repair"} <= set(names)


def test_knowledge_base_commands_stay_in_the_knowledge_base():
    assert {"activate", "delete"} <= {n for n, _ in available("kb")}
    for place in ("home", "test", "runs", "coverage", "settings"):
        assert "activate" not in {n for n, _ in available(place)}


def test_save_is_only_where_there_is_something_to_save():
    assert "save" in {n for n, _ in available("settings")}
    assert "save" not in {n for n, _ in available("home")}


def test_suggest_filters_by_place():
    assert [n for n, _ in suggest("/r", "home")] == ["runs"]
    # "results" is an alias of /output, so it matches /r too
    assert [n for n, _ in suggest("/r", "test")] == ["output", "runs", "run", "repair"]
    assert suggest("/edit", "home") == []


def test_unscoped_suggest_still_returns_everything():
    """No place given (tests, or a bar with no screen yet) means no filtering."""
    assert [n for n, _ in suggest("/r")] == ["output", "runs", "run", "repair"]


# --- /edit --------------------------------------------------------------------------

def test_edit_targets():
    assert parse_edit("--code") == ("code", "")
    assert parse_edit("--prompt") == ("prompt", "")
    assert parse_edit("code") == ("code", "")          # tolerate the missing dashes


def test_edit_needs_a_target():
    target, error = parse_edit("")
    assert target == "" and "--code" in error


def test_edit_rejects_unknown_and_multiple_targets():
    assert parse_edit("--everything")[1] != ""
    assert "One at a time" in parse_edit("--code --prompt")[1]


# --- live bar behaviour -------------------------------------------------------------

def _bar(app) -> CommandBar:
    return app.screen.query_one(CommandBar)


def test_place_starts_at_home_and_follows_the_screen():
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.screen.place == "home"
            assert "home" in str(_bar(app).query_one("#command-place").renderable)

            app.dispatch_global(_parse("/kb"))
            await pilot.pause()
            assert app.screen.place == "kb"
            assert "knowledge base" in str(_bar(app).query_one("#command-place").renderable)

    asyncio.run(scenario())


def test_up_arrow_recalls_previous_input():
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            # On the knowledge-base screen plain text is inert; submitting it in the
            # workspace would start a real generation.
            app.dispatch_global(_parse("/kb"))
            await pilot.pause()
            bar, inp = _bar(app), app.screen.query_one("#command")

            for text in ("first prompt", "second prompt"):
                inp.value = text
                await pilot.press("enter")
                await pilot.pause()
            assert inp.value == ""

            await pilot.press("up")                 # newest first
            await pilot.pause()
            assert inp.value == "second prompt"
            await pilot.press("up")
            await pilot.pause()
            assert inp.value == "first prompt"
            await pilot.press("down")               # back toward the present
            await pilot.pause()
            assert inp.value == "second prompt"
            await pilot.press("down")               # past the newest → empty draft again
            await pilot.pause()
            assert inp.value == ""

    asyncio.run(scenario())


def test_recalling_a_slash_command_does_not_reopen_the_menu():
    """A recalled '/config' matches itself, so the menu would open and take the next ↑
    for its highlight — leaving you stuck one entry into your own history."""
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.dispatch_global(_parse("/kb"))
            await pilot.pause()
            bar, inp = _bar(app), app.screen.query_one("#command")

            # /activate and /delete act on this screen rather than navigating away,
            # so the bar under test stays the same one throughout
            for text in ("/activate", "/delete"):
                inp.value = text
                await pilot.press("enter")
                await pilot.pause()

            await pilot.press("up")
            await pilot.pause()
            assert inp.value == "/delete"
            assert not bar.suggestions_open          # the menu stayed shut…
            await pilot.press("up")
            await pilot.pause()
            assert inp.value == "/activate"          # …so ↑ kept walking history

    asyncio.run(scenario())


def test_history_does_not_fight_the_suggestion_menu():
    """↑/↓ move the highlight while the menu is open, and only then."""
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.dispatch_global(_parse("/kb"))
            await pilot.pause()
            bar, inp = _bar(app), app.screen.query_one("#command")
            inp.value = "an earlier line"
            await pilot.press("enter")
            await pilot.pause()

            inp.value = "/"                          # menu opens
            await pilot.pause()
            assert bar.suggestions_open
            await pilot.press("down")
            await pilot.pause()
            assert bar._sel == 1                     # moved the highlight…
            assert inp.value == "/"                  # …and left the text alone

    asyncio.run(scenario())


def test_help_fills_the_suggestion_menu():
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            bar, inp = _bar(app), app.screen.query_one("#command")
            inp.value = "/help"
            await pilot.press("enter")
            await pilot.pause()

            assert bar.suggestions_open
            listed = str(bar.query_one("#command-suggest").renderable)
            assert "/tests" in listed and "/kb" in listed
            # scoped: no test is loaded ("/run" would match inside "/runs")
            assert "/repair" not in listed and "/edit" not in listed

    asyncio.run(scenario())


def test_place_label_survives_a_hostile_test_name():
    """A test's name is its prompt, so it can contain anything you'd type — including
    'assert body[0] == 1', which is markup that doesn't parse. Opening such a test
    used to take the whole app down from the location line."""
    async def scenario():
        startup()
        app = CasewrightApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            from web.services import tests_store
            t = tests_store.create_test("assert body[0] == 1 for Q2KD-DEMR-82P7",
                                        "p", "f.py", "code", "py", [], [])
            app.screen.load_test(t["id"], show_code=False)
            await pilot.pause()

            # the stored markup carries the escape; what matters is that it renders,
            # and renders back to the name the user actually gave
            from textual.markup import to_content
            markup = str(_bar(app).query_one("#command-place").renderable)
            assert "assert body[0] == 1" in str(to_content(markup))
            assert app.screen.place == "test"

    asyncio.run(scenario())
