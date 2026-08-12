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

from tui.markup import esc

# Where you can be. A command is only suggested in the places it actually works, so
# the menu never offers /run with no test loaded or /activate outside the knowledge
# base — the list you see is the list you can use.
PLACES = ("home", "test", "kb", "runs", "coverage", "settings")
EVERYWHERE = frozenset(PLACES)
WITH_A_TEST = frozenset({"test"})

# Each entry: (canonical name, aliases, category, help, places). Categories:
#   "nav"    — go to a place (handled by App.goto)
#   "action" — do something to the current test/screen (handled per-screen)
#   "global" — app-wide (help / back / quit)
# Multi-word aliases ("new test") are matched longest-first, so "/new test"
# never gets mistaken for "/new".
_REGISTRY = [
    # name        aliases                                  category   help                                          places
    ("tests",    ("tests", "library"),                     "nav",    "all your tests",                              EVERYWHERE),
    ("new",      ("new", "new test"),                       "nav",    "start a new test",                            EVERYWHERE),
    ("prompt",   ("prompt",),                               "nav",    "the current test's prompt",                   WITH_A_TEST),
    ("code",     ("code",),                                 "nav",    "the current test's code",                     WITH_A_TEST),
    ("config",   ("config", "test settings"),               "nav",    "the current test's run config",               WITH_A_TEST),
    ("output",   ("output", "results", "test results"),     "nav",    "the current test's run output",               WITH_A_TEST),
    ("runs",     ("runs",),                                 "nav",    "run history for every test",                  EVERYWHERE),
    ("coverage", ("coverage", "cov"),                       "nav",    "spec-coverage tree",                          EVERYWHERE),
    ("kb",       ("kb", "knowledge"),                        "nav",    "knowledge bases — --new, --storage",          EVERYWHERE),
    ("settings", ("settings",),                             "nav",    "app settings",                                EVERYWHERE),

    ("generate", ("generate", "gen"),                       "action", "regenerate from the current prompt",          WITH_A_TEST),
    ("run",      ("run",),                                  "action", "run the current test",                        WITH_A_TEST),
    ("repair",   ("repair",),                               "action", "auto-repair after a failed run",              WITH_A_TEST),
    ("edit",     ("edit",),                                 "action", "--code | --prompt — edit in place",           WITH_A_TEST),
    ("export",   ("export", "copy"),                        "action", "copy the code to the clipboard",              WITH_A_TEST),
    ("version",  ("version", "ver"),                        "action", "next | prev — step through versions",         WITH_A_TEST),
    ("open",     ("open",),                                 "action", "open a test by name or number",               EVERYWHERE),
    ("save",     ("save",),                                 "action", "save what you're editing",                    frozenset({"settings"})),
    ("activate", ("activate",),                             "action", "activate a knowledge-base version",           frozenset({"kb"})),
    ("delete",   ("delete",),                               "action", "delete an errored knowledge-base version",    frozenset({"kb"})),

    ("help",     ("help", "?"),                             "global", "everything you can do here",                  EVERYWHERE),
    ("back",     ("back",),                                 "global", "back to the workspace",                       EVERYWHERE),
    ("quit",     ("quit", "exit", "q"),                     "global", "exit casewright",                             EVERYWHERE),
]

# Flattened (alias, name, category), longest alias first for prefix matching.
_ENTRIES = sorted(
    ((alias, name, cat) for name, aliases, cat, _, _ in _REGISTRY for alias in aliases),
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


def available(place: str = None) -> list[tuple[str, str]]:
    """``[(name, help), …]`` for every command usable in ``place``, registry order.

    ``place`` None means don't filter — used by tests and by any caller that hasn't
    got a screen to ask."""
    return [(aliases[0], doc) for _n, aliases, _c, doc, places in _REGISTRY
            if place is None or place in places]


def suggest(value: str, place: str = None) -> list[tuple[str, str]]:
    """Commands whose name/aliases match a half-typed slash word, in ``place``.

    Returns ``[(canonical_name, help), …]`` for ``/`` (everything available here) or
    ``/h`` (everything starting with ``h``). Returns ``[]`` once a space is typed —
    the command word is complete — or for plain (non-slash) text.
    """
    if not value.startswith("/"):
        return []
    body = value[1:]
    if " " in body:      # a space means the command word is finished (incl. trailing)
        return []
    low = body.lower()
    out, seen = [], set()
    for name, aliases, _cat, doc, places in _REGISTRY:
        if place is not None and place not in places:
            continue
        if name not in seen and any(a.startswith(low) for a in aliases):
            seen.add(name)
            out.append((aliases[0], doc))
    return out


# --- /edit arguments ---------------------------------------------------------------

_MENU_ROWS = 10   # visible suggestion rows; the rest scroll with the highlight

EDIT_TARGETS = ("code", "prompt")
EDIT_USAGE = "/edit --code    the generated code\n/edit --prompt  the description it was generated from"


def parse_edit(args: str) -> tuple[str, str]:
    """``(target, error)`` for the text after ``/edit``; exactly one is non-empty.

    There is no default: ``--code`` and ``--prompt`` do very different things (one
    stores a new version of the test, the other rewrites what it was asked for), and
    picking one silently is how you edit the wrong thing."""
    tokens = (args or "").split()
    if not tokens:
        return "", "Edit what?\n\n" + EDIT_USAGE
    if len(tokens) > 1:
        return "", "One at a time.\n\n" + EDIT_USAGE
    target = tokens[0].lstrip("-").lower()
    if target not in EDIT_TARGETS:
        return "", f"Don't know {tokens[0]!r}.\n\n" + EDIT_USAGE
    return target, ""


class CommandBar(Vertical):
    """The always-present command line: a ``>`` inside a rounded box, a suggestion
    menu above it, and a line naming where you are between the two. The inner Input
    keeps id ``command`` so routing and auto-focus are unchanged.

    Type ``/h`` and the menu lists matching commands; ↑/↓ move the highlight, Tab
    completes it, Enter runs the highlighted match (or the literal text). With the
    menu closed, ↑/↓ walk back through what you've already typed instead — the two
    never compete, because the menu is only open while a slash word is unfinished.

    Suggestions are scoped to the screen's ``place``, and ``/help`` fills the same
    menu with everything available there. One list, one look, one place to read.
    """

    def compose(self) -> ComposeResult:
        yield Static(" ", id="command-suggest")
        yield Static("", id="command-place")
        with Horizontal(id="command-row"):
            yield Static(">", id="command-prompt")
            yield Input(placeholder="Ask for a test, or / for commands", id="command")
        yield Static("/ for commands  ·  ↑↓ history  ·  Tab to complete  ·  Enter to run",
                     id="command-hint")

    def on_mount(self) -> None:
        self._matches: list[tuple[str, str]] = []
        self._sel = 0
        self.suggestions_open = False   # #command-suggest starts hidden via CSS
        self._history_pos = None        # None = not browsing; else an index into history
        self._draft = ""                # what was typed before ↑ started replacing it
        self._suppress_menu = False     # set while filling the input from history
        self.refresh_place()

    # --- where you are -----------------------------------------------------------
    @property
    def place(self) -> str:
        """The screen's scoping key, or 'home' before one is attached."""
        return getattr(self.screen, "place", "home")

    def refresh_place(self) -> None:
        """Redraw the location line. Screens call this when the answer changes — the
        workspace does on every load/clear, since it's 'home' until a test is open."""
        label = getattr(self.screen, "place_label", lambda: self.place)()
        # esc: a test's name comes from its prompt, so "assert body[0] == 1" ends up
        # here — unescaped it is markup, and markup that doesn't parse kills the app.
        self.query_one("#command-place", Static).update(f"[dim]▌[/dim] {esc(label)}")

    # --- history ------------------------------------------------------------------
    @property
    def _history(self) -> list:
        """Kept on the app, not the bar: it should survive moving between screens."""
        if not hasattr(self.app, "command_history"):
            self.app.command_history = []
        return self.app.command_history

    def remember(self, raw: str) -> None:
        """Record a submitted line. Consecutive duplicates collapse, so holding ↑
        after re-running something doesn't walk through copies of it."""
        raw = raw.strip()
        if raw and (not self._history or self._history[-1] != raw):
            self._history.append(raw)
        self._history_pos = None
        self._draft = ""

    def _recall(self, delta: int) -> None:
        history = self._history
        if not history:
            return
        inp = self.query_one("#command", Input)
        if self._history_pos is None:
            if delta > 0:            # ↓ with nothing recalled yet: nothing to go back to
                return
            self._draft = inp.value  # keep the half-typed line to restore on the way down
            self._history_pos = len(history)
        pos = self._history_pos + delta
        if pos >= len(history):      # walked past the newest: back to what you were typing
            self._history_pos = None
            self._set_value(inp, self._draft)
            return
        self._history_pos = max(pos, 0)
        self._set_value(inp, history[self._history_pos])

    def _set_value(self, inp: Input, value: str) -> None:
        """Fill the input from history without the suggestion menu reopening.

        Recalling a slash command would otherwise match itself, open the menu, and hand
        the next ↑ to the highlight instead of to history — so you could step back one
        entry and no further."""
        self._suppress_menu = True
        pos = self._history_pos
        inp.value = value
        inp.cursor_position = len(value)
        self._history_pos = pos

    # --- live suggestions --------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "command":
            return
        if self._suppress_menu:          # the change came from ↑/↓, not from typing
            self._suppress_menu = False
            self.hide()
            return
        self._matches = suggest(event.value, self.place)
        self._sel = 0
        self._refresh_menu()

    def show_help(self) -> None:
        """Fill the menu with every command available here (the /help command)."""
        self._matches = available(self.place)
        self._sel = 0
        self._refresh_menu()

    def _refresh_menu(self) -> None:
        box = self.query_one("#command-suggest", Static)
        if not self._matches:
            box.display = False
            self.suggestions_open = False
            return
        # With a test loaded there are ~20 commands — more than fits above the input on
        # a short terminal. Show a window that follows the highlight, and say how many
        # are outside it, so the list is never silently truncated.
        total = len(self._matches)
        start = 0
        if total > _MENU_ROWS:
            start = min(max(self._sel - _MENU_ROWS // 2, 0), total - _MENU_ROWS)
        window = self._matches[start:start + _MENU_ROWS]

        lines = []
        if start:
            lines.append(f"[dim]  ↑ {start} more[/dim]")
        for i, (name, doc) in enumerate(window, start=start):
            if i == self._sel:
                lines.append(f"[b]› /{name}[/b]  [dim]{doc}[/dim]")
            else:
                lines.append(f"[dim]  /{name}  {doc}[/dim]")
        if start + _MENU_ROWS < total:
            lines.append(f"[dim]  ↓ {total - start - _MENU_ROWS} more[/dim]")
        box.update("\n".join(lines))
        box.display = True
        self.suggestions_open = True

    def on_key(self, event) -> None:
        # Fires as the key bubbles up from the (focused) Input.
        if not self.suggestions_open:
            if event.key in ("up", "down"):
                self._recall(-1 if event.key == "up" else 1)
            else:
                return
        elif event.key == "down":
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
        menu resolves to the highlighted command; everything else is literal.

        ``/help`` opens the menu without any text behind it, so a bare Enter there
        would otherwise "resolve" to whatever happens to be highlighted."""
        stripped = raw.strip()
        if self._matches and stripped.startswith("/") and " " not in stripped[1:]:
            return "/" + self._matches[self._sel][0]
        return raw
