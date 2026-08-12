"""Escaping text that isn't markup.

Textual renders ``[...]`` as a style tag, and unlike Rich it *raises* on a tag it
can't resolve rather than passing it through. So any outside string interpolated into
a markup template can take the screen down::

    self._say(f"[red]{exc}[/red]")        # exc = "[Errno 2] No such file: …"
    self._echo(f"[dim]>[/dim] {prompt}")  # prompt = "assert body[0] == 1"

Both raise ``MarkupError: Expected markup style value``. That second one matters most:
this app writes tests, so a prompt or a container's output containing ``body[0]`` is
ordinary, not exotic — and it would crash the workspace mid-run.

Anything that came from a user, a file path, an exception, a database row, or a
container's stdout goes through ``esc`` on its way into a markup string.
"""


def esc(value) -> str:
    """Neutralise markup in ``value`` so it renders as the literal text it is."""
    return str(value).replace("[", r"\[")
