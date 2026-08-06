"""Edit a test's code or its prompt in place.

A modal over the workspace: a text area, ctrl+s to save, esc to discard. It returns
the edited text through ``dismiss`` — or ``None`` when cancelled, which the caller
must treat as "change nothing" rather than "empty string", or escaping out of an
edit would wipe the file.

The area is a plain ``TextArea``: syntax highlighting needs tree-sitter grammars
(``pip install 'textual[syntax]'``) that aren't a dependency here, and line numbers
plus code-friendly indentation are what actually matter for reviewing generated code.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, TextArea


class EditorScreen(ModalScreen):
    """Returns the new text on save, or ``None`` on cancel."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, title: str, text: str, subtitle: str = ""):
        super().__init__()
        self._title = title
        self._text = text or ""
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        with Vertical(id="editor-box"):
            area = TextArea.code_editor(self._text, id="editor-area", show_line_numbers=True)
            area.border_title = self._title
            if self._subtitle:
                area.border_subtitle = self._subtitle
            yield area
            yield Static("[b]ctrl+s[/b] save   ·   [b]esc[/b] discard", id="editor-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#editor-area", TextArea).focus()

    def action_save(self) -> None:
        self.dismiss(self.query_one("#editor-area", TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)
