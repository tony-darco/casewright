"""Runs history — every run of every test, newest first.

The web `runs.html` was only an empty shell; this builds a real history from
runs_store (list_runs_for_test per test).
"""

from textual.app import ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from web.services import runs_store, tests_store
from tui.command_screen import CommandScreen


class RunsScreen(CommandScreen):
    PLACE = "runs"
    PLACE_LABEL = "runs"
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("RUN HISTORY", classes="eyebrow")
        yield DataTable(id="runs-table")
        yield self.command_bar()
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.add_columns("Test", "Status", "Source", "Started", "Error")
        rows = []
        for t in tests_store.list_tests():
            for r in runs_store.list_runs_for_test(t["id"]):
                rows.append((r["created_at"], t["name"], r["status"], r["source"],
                             r.get("finished_at") or "", r.get("error_message") or ""))
        rows.sort(reverse=True)  # newest first by created_at
        for created, name, status, source, _finished, err in rows:
            table.add_row(name, status, source, created, (err[:60] + "…") if len(err) > 60 else err)
        if not rows:
            table.add_row("No runs yet.", "", "", "", "")
