"""The command vocabulary for the TUI's single command bar.

The bar replaces tab clicks and function keys: everything is driven by typing.
Text that starts with ``/`` is a command (navigate somewhere, or act on the
current test); anything else is plain text handled by whatever place you're in
(the prompt on the Prompt view, a filter on the test list, and so on).

This module is pure data + parsing — no Textual imports beyond the bar widget —
so the vocabulary stays in one place and is easy to test.
"""

import shlex
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
    ("kb",       ("kb", "knowledge"),                        "nav",    "knowledge bases (--new, --storage)"),
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


# --- /kb arguments ---------------------------------------------------------------

KB_SPLITS = ("custom", "langchain")
KB_USAGE = ("/kb                                    list knowledge bases\n"
            "/kb --new <url|path> --split custom    embed a new one (or --split langchain)\n"
            "/kb --new <url|path> --split custom --name \"My spec\"\n"
            "/kb --storage <chroma-url>             keep vectors on a remote Chroma server\n"
            "/kb --storage local                    keep them inside the app")


@dataclass
class KbRequest:
    """One parsed ``/kb`` invocation. ``error`` non-empty means don't act — show it."""
    action: str = "list"     # list | new | storage
    source: str = ""         # --new: the URL or file path to embed
    split: str = ""          # --new: custom | langchain
    name: str = ""           # --new: optional label, else derived from the source
    storage: str = ""        # --storage: a Chroma URL, or "local"
    error: str = ""


def parse_kb(args: str) -> KbRequest:
    """Parse the text after ``/kb``.

    Bare ``/kb`` lists. ``--new`` embeds a source — a URL or a local path, told apart
    when it's actually fetched, not here — and requires ``--split`` because the two
    methods produce very different corpora from the same file: guessing would quietly
    give someone a knowledge base that retrieves badly.

    Quoting is shell-style, so ``--name "Meraki v1.53"`` survives as one value.
    """
    try:
        tokens = shlex.split(args or "")
    except ValueError as exc:                      # an unbalanced quote
        return KbRequest(error=f"Couldn't read that: {exc}")
    if not tokens:
        return KbRequest(action="list")

    req = KbRequest()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        value = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in ("--new", "--storage") and req.action != "list":
            return KbRequest(error="Use one of --new or --storage at a time.\n\n" + KB_USAGE)
        if tok == "--new":
            if not value or value.startswith("--"):
                return KbRequest(error="--new needs a URL or file path.\n\n" + KB_USAGE)
            req.action, req.source, i = "new", value, i + 2
        elif tok == "--storage":
            if not value or value.startswith("--"):
                return KbRequest(error="--storage needs a Chroma URL, or 'local'.\n\n" + KB_USAGE)
            req.action, req.storage, i = "storage", value, i + 2
        elif tok == "--split":
            if value not in KB_SPLITS:
                return KbRequest(error="--split takes 'custom' (one document per OpenAPI "
                                       "operation) or 'langchain' (generic recursive "
                                       "chunking).")
            req.split, i = value, i + 2
        elif tok == "--name":
            if not value or value.startswith("--"):
                return KbRequest(error="--name needs a value.")
            req.name, i = value, i + 2
        else:
            return KbRequest(error=f"Don't know {tok!r}.\n\n" + KB_USAGE)

    if req.action == "list":                       # only --split/--name were given
        return KbRequest(error="Nothing to do — --split and --name describe a --new "
                               "knowledge base.\n\n" + KB_USAGE)
    if req.action == "new" and not req.split:
        return KbRequest(error="--new needs --split custom or --split langchain.\n\n" + KB_USAGE)
    if req.action == "storage" and req.name:
        return KbRequest(error="--name describes a knowledge base, not storage.")
    return req


def suggest(value: str) -> list[tuple[str, str]]:
    """Commands whose name/aliases match a half-typed slash word.

    Returns ``[(canonical_name, help), …]`` for ``/`` (all commands) or ``/h``
    (everything starting with ``h``). Returns ``[]`` once a space is typed — the
    command word is complete — or for plain (non-slash) text.
    """
    if not value.startswith("/"):
        return []
    body = value[1:]
    if " " in body:      # a space means the command word is finished (incl. trailing)
        return []
    low = body.lower()
    out, seen = [], set()
    for name, aliases, _cat, doc in _REGISTRY:
        if name not in seen and any(a.startswith(low) for a in aliases):
            seen.add(name)
            out.append((aliases[0], doc))
    return out


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
    a ``>`` inside a rounded box, a live suggestion menu above it, and a dim hint
    line beneath. The inner Input keeps id ``command`` so routing and auto-focus
    are unchanged.

    Type ``/h`` and the menu lists matching commands; ↑/↓ move the highlight, Tab
    completes it, Enter runs the highlighted match (or the literal text).
    """

    def compose(self) -> ComposeResult:
        yield Static(" ", id="command-suggest")
        with Horizontal(id="command-row"):
            yield Static(">", id="command-prompt")
            yield Input(placeholder="Ask for a test, or / for commands", id="command")
        yield Static("/ for commands  ·  Tab to complete  ·  Enter to run", id="command-hint")

    def on_mount(self) -> None:
        self._matches: list[tuple[str, str]] = []
        self._sel = 0
        self.suggestions_open = False   # #command-suggest starts hidden via CSS

    # --- live suggestions --------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "command":
            self._matches = suggest(event.value)
            self._sel = 0
            self._refresh_menu()

    def _refresh_menu(self) -> None:
        box = self.query_one("#command-suggest", Static)
        if not self._matches:
            box.display = False
            self.suggestions_open = False
            return
        lines = []
        for i, (name, doc) in enumerate(self._matches):
            if i == self._sel:
                lines.append(f"[b]› /{name}[/b]  [dim]{doc}[/dim]")
            else:
                lines.append(f"[dim]  /{name}  {doc}[/dim]")
        box.update("\n".join(lines))
        box.display = True
        self.suggestions_open = True

    def on_key(self, event) -> None:
        # Fires as the key bubbles up from the (focused) Input; only act while the
        # menu is open so normal typing/focus behaviour is untouched otherwise.
        if not self.suggestions_open:
            return
        if event.key == "down":
            self._sel = (self._sel + 1) % len(self._matches)
            self._refresh_menu()
        elif event.key == "up":
            self._sel = (self._sel - 1) % len(self._matches)
            self._refresh_menu()
        elif event.key == "tab":
            self._complete()
        elif event.key == "escape":
            self.hide()
        else:
            return
        event.stop()
        event.prevent_default()

    def _complete(self) -> None:
        inp = self.query_one("#command", Input)
        inp.value = f"/{self._matches[self._sel][0]} "
        inp.cursor_position = len(inp.value)   # on_input_changed then clears the menu

    def hide(self) -> None:
        self._matches = []
        self.suggestions_open = False
        self.query_one("#command-suggest").display = False

    def effective(self, raw: str) -> str:
        """The text to actually run on Enter: a half-typed slash word with an open
        menu resolves to the highlighted command; everything else is literal."""
        stripped = raw.strip()
        if self._matches and stripped.startswith("/") and " " not in stripped[1:]:
            return "/" + self._matches[self._sel][0]
        return raw
