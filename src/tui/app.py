"""The casewright Textual application.

Holds the single local user and wires up the screens. The workspace (the core
generate → review → run loop) is the home screen; Settings, Knowledge base,
Coverage, and Runs are pushed on top of it.
"""

from textual.app import App

from web import config
from tui.commands import Command, help_text

# Sections that live on their own screen; everything else is a view of the
# workspace. Values are lazy factories so the import graph stays shallow.
_SECTIONS = {
    "settings":  lambda: __import__("tui.screens.settings", fromlist=["SettingsScreen"]).SettingsScreen,
    "kb":        lambda: __import__("tui.screens.knowledgebase", fromlist=["KnowledgeBaseScreen"]).KnowledgeBaseScreen,
    "coverage":  lambda: __import__("tui.screens.coverage", fromlist=["CoverageScreen"]).CoverageScreen,
    "runs":      lambda: __import__("tui.screens.runs", fromlist=["RunsScreen"]).RunsScreen,
}


class CasewrightApp(App):
    """Root app. Carries the local user so every screen can pass ``uid`` to services."""

    CSS_PATH = "app.tcss"
    TITLE = config.WORDMARK

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, user: dict):
        super().__init__()
        self.user = user

    @property
    def uid(self) -> int:
        return self.user["id"]

    def on_mount(self) -> None:
        # Imported lazily so the module import graph stays shallow (and testable).
        from tui.screens.workspace import WorkspaceScreen

        self.push_screen(WorkspaceScreen())

    # --- command routing ---------------------------------------------------------
    def dispatch_global(self, cmd: Command) -> None:
        """Handle nav + app-wide commands from any screen's command bar."""
        if cmd.name == "quit":
            self.exit()
        elif cmd.name == "help":
            self.notify(help_text(), title="commands", timeout=12)
        elif cmd.name == "back":
            self.goto("workspace")
        else:
            self.goto(cmd.name, cmd.args)

    def goto(self, target: str, arg: str = "") -> None:
        """Navigate to a place. Sections push a screen; views live on the workspace."""
        from tui.screens.workspace import WorkspaceScreen

        # Unwind back to the workspace, the always-present home screen.
        while not isinstance(self.screen, WorkspaceScreen):
            self.pop_screen()
        workspace = self.screen
        if target in _SECTIONS:
            self.push_screen(_SECTIONS[target]()())
        else:
            workspace.goto_view(target, arg)


def main() -> None:
    """Console entry point (``casewright`` / ``python -m tui``)."""
    from tui.bootstrap import startup

    user = startup()
    CasewrightApp(user).run()


if __name__ == "__main__":
    main()
