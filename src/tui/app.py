"""The casewright Textual application.

Holds the single local user and wires up the screens. The workspace (the core
generate → review → run loop) is the home screen; Settings, Knowledge base,
Coverage, and Runs are pushed on top of it.
"""

from textual.app import App

from web import config


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


def main() -> None:
    """Console entry point (``casewright`` / ``python -m tui``)."""
    from tui.bootstrap import startup

    user = startup()
    CasewrightApp(user).run()


if __name__ == "__main__":
    main()
