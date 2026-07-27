"""The command vocabulary for the TUI's single command bar.

The bar replaces tab clicks and function keys: everything is driven by typing.
Text that starts with ``/`` is a command (navigate somewhere, or act on the
current test); anything else is plain text handled by whatever place you're in
(the prompt on the Prompt view, a filter on the test list, and so on).

This module is pure data + parsing — no Textual imports beyond the bar widget —
so the vocabulary stays in one place and is easy to test.
"""

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

# Each entry: (canonical name, aliases, category, help). Categories:
#   "nav"    — go to a place (handled by App.goto)
#   "action" — do something to the current test/screen (handled per-screen)
#   "global" — app-wide (help / back / quit)
# Multi-word aliases ("new test") are matched longest-first, so "/new test"
# never gets mistaken for "/new".
_REGISTRY = [
    # name        aliases                                  category   help
    ("tests",    ("tests", "library"),                     "nav",    "all your tests"),
    ("new",      ("new", "new test"),                       "nav",    "start a new test"),
    ("prompt",   ("prompt",),                               "nav",    "the current test's prompt"),
    ("code",     ("code",),                                 "nav",    "the current test's code"),
    ("config",   ("config", "test settings"),               "nav",    "the current test's run config"),
    ("output",   ("output", "results", "test results"),     "nav",    "the current test's run output"),
    ("runs",     ("runs",),                                 "nav",    "run history for every test"),
    ("coverage", ("coverage", "cov"),                       "nav",    "spec-coverage tree"),
    ("kb",       ("kb", "knowledge"),                        "nav",    "knowledge base"),
    ("settings", ("settings",),                             "nav",    "app settings"),

    ("generate", ("generate", "gen"),                       "action", "generate from the prompt"),
    ("run",      ("run",),                                  "action", "run the current test"),
    ("repair",   ("repair",),                               "action", "auto-repair after a failed run"),
    ("export",   ("export", "copy"),                        "action", "copy the code to the clipboard"),
    ("save",     ("save",),                                 "action", "save the code or config you're viewing"),
    ("open",     ("open",),                                 "action", "open a test by name or number"),
    ("version",  ("version", "ver"),                        "action", "next | prev — step through versions"),
    ("activate", ("activate",),                             "action", "activate a knowledge-base version"),
    ("delete",   ("delete",),                               "action", "delete an errored knowledge-base version"),
    ("embed",    ("embed",),                                "action", "embed the knowledge-base source"),

    ("help",     ("help", "?"),                             "global", "show this list"),
    ("back",     ("back",),                                 "global", "back to the workspace"),
    ("quit",     ("quit", "exit", "q"),                     "global", "exit casewright"),
]

# Flattened (alias, name, category), longest alias first for prefix matching.
_ENTRIES = sorted(
    ((alias, name, cat) for name, aliases, cat, _ in _REGISTRY for alias in aliases),
    key=lambda e: -len(e[0]),
)


@dataclass
class Command:
    name: str | None      # canonical name, or None when the slash word is unknown
    args: str             # everything after the command word, trimmed
    category: str         # nav | action | global | unknown


def parse(text: str) -> Command | None:
    """Parse one line from the command bar.

    Returns ``None`` for plain (non-slash) text — the caller handles it in
    context. Returns a ``Command`` for slash input; ``name`` is ``None`` when the
    slash word matches nothing.
    """
    text = text.strip()
    if not text.startswith("/"):
        return None
    body = text[1:].strip()
    low = body.lower()
    for alias, name, cat in _ENTRIES:
        if low == alias:
            return Command(name, "", cat)
        if low.startswith(alias + " "):
            return Command(name, body[len(alias):].strip(), cat)
    return Command(None, body, "unknown")


def help_text() -> str:
    """A grouped, Rich-markup list of every command for the /help toast."""
    groups = {"nav": "GO", "action": "DO", "global": "APP"}
    lines = []
    for cat, title in groups.items():
        lines.append(f"[b]{title}[/b]")
        for name, aliases, c, doc in _REGISTRY:
            if c == cat:
                lines.append(f"  [b]/{aliases[0]}[/b] — {doc}")
        lines.append("")
    lines.append("[dim]Plain text (no slash) goes to wherever you are.[/dim]")
    return "\n".join(lines)


class CommandBar(Vertical):
    """The always-present command line, styled after the Claude Code prompt:
    a ``>`` inside a rounded box with a dim hint line beneath. The inner Input
    keeps id ``command`` so routing and auto-focus are unchanged.
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="command-row"):
            yield Static(">", id="command-prompt")
            yield Input(placeholder="Ask for a test, or / for commands", id="command")
        yield Static("/ for commands  ·  plain text goes where you are", id="command-hint")
