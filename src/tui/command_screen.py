"""Base screen for the command-driven TUI.

Every screen mounts a :class:`~tui.commands.CommandBar` (yield ``self.command_bar()``
just before the ``Footer``) and inherits routing here: slash input becomes a
:class:`~tui.commands.Command`, plain text is passed to :meth:`on_text`.

Navigation and app-wide commands are handled centrally by the App (``goto`` /
``dispatch_global``); action commands are offered to the current screen via
:meth:`on_command`, which returns ``False`` when it doesn't apply here.
"""

from textual.screen import Screen
from textual.widgets import Input

from tui.commands import Command, CommandBar, parse


class CommandScreen(Screen):
    # Focus the command bar on mount so typing always lands there.
    AUTO_FOCUS = "#command"

    def command_bar(self) -> CommandBar:
        return CommandBar()

    # --- routing -----------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Only the command bar routes; other Inputs (API key, org id, …) are
        # left to their own screens' button handlers.
        if event.input.id != "command":
            return
        event.stop()
        raw = event.value
        event.input.value = ""
        cmd = parse(raw)
        if cmd is None:
            self.on_text(raw)
            return
        if cmd.name is None:
            self.app.notify(f"Unknown command: {raw.strip()} — try /help", severity="warning")
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
