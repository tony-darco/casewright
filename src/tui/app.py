"""The casewright Textual application.

Wires up the screens. The workspace (the core generate → review → run loop) is the
home screen; Settings, Knowledge base, Coverage, and Runs are pushed on top of it.
"""

from textual.app import App

from web import config
from tui.commands import Command

# Sections that live on their own screen; everything else is a view of the
# workspace. Values are lazy factories so the import graph stays shallow.
_SECTIONS = {
    "settings":  lambda: __import__("tui.screens.settings", fromlist=["SettingsScreen"]).SettingsScreen,
    "kb":        lambda: __import__("tui.screens.knowledgebase", fromlist=["KnowledgeBaseScreen"]).KnowledgeBaseScreen,
    "coverage":  lambda: __import__("tui.screens.coverage", fromlist=["CoverageScreen"]).CoverageScreen,
    "runs":      lambda: __import__("tui.screens.runs", fromlist=["RunsScreen"]).RunsScreen,
}


class CasewrightApp(App):
    """Root app."""

    CSS_PATH = "app.tcss"
    TITLE = config.WORDMARK

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        # Imported lazily so the module import graph stays shallow (and testable).
        from tui.screens.workspace import WorkspaceScreen

        self.push_screen(WorkspaceScreen())

    # --- command routing ---------------------------------------------------------
    def dispatch_global(self, cmd: Command) -> None:
        """Handle nav + app-wide commands from any screen's command bar."""
        if cmd.name == "quit":
            self.exit()
        elif cmd.name == "back":
            self.goto("workspace")
        else:
            self.goto(cmd.name, cmd.args)

    def goto(self, target: str, arg: str = "") -> None:
        """Navigate to a place. Sections push a screen; views live on the workspace.

        ``arg`` is carried to the section screen, so ``/kb --new …`` works from
        anywhere. A section that's already on top is handed the arguments in place
        rather than being torn down and rebuilt — rebuilding would drop a live
        subscription (an embedding in progress) for no reason."""
        from tui.screens.workspace import WorkspaceScreen

        if target in _SECTIONS:
            screen_cls = _SECTIONS[target]()
            if isinstance(self.screen, screen_cls):
                self.screen.run_args(arg)
                return

        # Unwind back to the workspace, the always-present home screen.
        while not isinstance(self.screen, WorkspaceScreen):
            self.pop_screen()
        workspace = self.screen
        if target in _SECTIONS:
            self.push_screen(_SECTIONS[target]()(arg))
        else:
            workspace.goto_view(target, arg)


def main() -> None:
    """Console entry point (``casewright`` / ``python -m tui``)."""
    from tui.bootstrap import startup

    startup()
    CasewrightApp().run()


if __name__ == "__main__":
    main()
