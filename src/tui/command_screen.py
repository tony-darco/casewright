"""Base screen for the command-driven TUI.

Every screen mounts a :class:`~tui.commands.CommandBar` (yield ``self.command_bar()``
just before the ``Footer``) and inherits routing here: slash input becomes a
:class:`~tui.commands.Command`, plain text is passed to :meth:`on_text`.

Navigation and app-wide commands are handled centrally by the App (``goto`` /
``dispatch_global``); action commands are offered to the current screen via
:meth:`on_command`, which returns ``False`` when it doesn't apply here.

A screen also declares its ``place`` — the key that scopes which commands the bar
suggests, and the label shown above the input.
"""

from textual.screen import Screen
from textual.widgets import Input

from tui.commands import Command, CommandBar, parse


class CommandScreen(Screen):
    # Focus the command bar on mount so typing always lands there.
    AUTO_FOCUS = "#command"

    PLACE = "home"          # scoping key; see tui.commands.PLACES
    PLACE_LABEL = None      # display label, defaults to PLACE

    def __init__(self, args: str = ""):
        """``args`` is whatever followed the command that opened this screen, e.g. the
        ``--new …`` of a ``/kb --new …`` typed from the workspace. Screens that take
        none simply ignore it."""
        super().__init__()
        self.args = args

    def command_bar(self) -> CommandBar:
        return CommandBar()

    def run_args(self, args: str) -> None:
        """Act on arguments for a screen already on top (see App.goto). Screens that
        take arguments override this; the default is to ignore them."""

    # --- where you are -----------------------------------------------------------
    @property
    def place(self) -> str:
        return self.PLACE

    def place_label(self) -> str:
        return self.PLACE_LABEL or self.PLACE

    def refresh_place(self) -> None:
        """Redraw the location line after something changes what this place *is*."""
        bars = self.query(CommandBar)
        if bars:
            bars.first().refresh_place()

    # --- routing -----------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only the command bar routes; other Inputs (API key, org id, …) are
        # left to their own screens' button handlers.
        if event.input.id != "command":
            return
        event.stop()
        bar = self.query_one(CommandBar)
        raw = bar.effective(event.value)
        event.input.value = ""
        bar.remember(raw)
        bar.hide()
        cmd = parse(raw)
        if cmd is None:
            self.on_text(raw)
            return
        if cmd.name is None:
            self.app.notify(f"Unknown command: {raw.strip()} — try /help", severity="warning")
            return
        if cmd.name == "help":
            # Fills the suggestion menu rather than a toast — one list, one look. After
            # a refresh, because clearing the input above posts a Changed that would
            # otherwise close the menu right after it opened.
            self.call_after_refresh(bar.show_help)
            return
        if cmd.category in ("nav", "global"):
            self.app.dispatch_global(cmd)
        elif not self.on_command(cmd):
            self.app.notify(f"/{cmd.name} isn't available here", severity="warning")

    # --- overridable hooks -------------------------------------------------------
    def on_text(self, text: str) -> None:
        """Handle plain (non-slash) text for this place. Default: a gentle nudge."""
        if text.strip():
            self.app.notify("Nothing to type here — use a /command (or /help)")

    def on_command(self, cmd: Command) -> bool:
        """Handle an action command local to this screen. Return True if handled."""
        return False
