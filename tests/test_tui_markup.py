"""Outside text must not be parsed as markup.

Textual raises on a ``[...]`` it can't resolve as a style, so an un-escaped prompt,
error, or line of container output takes the screen down. This app writes tests, so
``assert body[0] == 1`` is an ordinary thing to type and an ordinary thing to see in
a failing run's output — exactly the strings that used to crash it.
"""

import pytest
from textual.markup import MarkupError, to_content

from tui.markup import esc

# Real shapes: a prompt with a subscript, a pytest failure line, an OSError, a Meraki
# error body, and a knowledge-base name taken from a filename.
HOSTILE = [
    "assert body[0] == 1",
    "E       assert response[0] == {'id': 1}",
    "[Errno 2] No such file or directory: '/specs/meraki.json'",
    "Meraki returned HTTP 400: {'errors': ['[invalid] serial']}",
    "spec [v2] (trimmed).json",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_escaped_text_renders_inside_markup(text):
    """The template around it keeps working, and the text survives verbatim."""
    content = to_content(f"[red]{esc(text)}[/red]")
    assert str(content) == text


@pytest.mark.parametrize("text", HOSTILE)
def test_the_transcript_line_shapes_survive(text):
    """The exact wrappers the workspace uses: an echoed prompt and a run log line."""
    assert str(to_content(f"[dim]>[/dim] {esc(text)}")) == f"> {text}"
    assert str(to_content(f"  [dim]\\[run][/dim] {esc(text)}")) == f"  [run] {text}"


def test_unescaped_text_is_what_used_to_crash():
    """Guards the reason esc() exists: without it these raise rather than render."""
    with pytest.raises(MarkupError):
        to_content("[dim]>[/dim] assert body[0] == 1")


def test_esc_accepts_non_strings():
    """Call sites pass exceptions and DB values straight in."""
    assert esc(FileNotFoundError("[Errno 2] nope")) == r"\[Errno 2] nope"
    assert esc(None) == "None"
    assert esc(957) == "957"
