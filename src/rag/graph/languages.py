"""Target languages for test generation + deterministic sanitization.

One registry drives two things: what we tell the model to write (the framework
guidance injected into the generate prompt) and how we strip its answer back down
to pure source (the fence tags to keep and a heuristic for the first line of real
code when the model didn't fence its output).

``resolve`` accepts either a canonical name (``"python"``) or one of the UI/short
codes the web layer already uses (``"py"``, ``"ts"``, ``"csharp"``), so callers can
pass whatever they have. Unknown values fall back to Python — the historical and
still-default target.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    name: str                 # canonical id, also the state value
    label: str                # human name for the generate prompt
    aliases: tuple            # fence tags + UI codes that resolve here
    framework: str            # what the prompt tells the model to write
    code_start: re.Pattern    # first plausible line of real code (no-fence prose strip)


PYTHON = Language(
    name="python", label="Python", aliases=("python", "py"),
    framework="a pytest module using the requests library",
    code_start=re.compile(r"^\s*(import |from |def |class |@|async |BASE_URL)"),
)

TYPESCRIPT = Language(
    name="typescript", label="TypeScript", aliases=("typescript", "ts"),
    framework="a Jest test suite using the global fetch API",
    code_start=re.compile(r"^\s*(import |export |const |let |var |function |describe\(|test\(|it\(|//|/\*)"),
)

JAVA = Language(
    name="java", label="Java", aliases=("java",),
    framework="a JUnit 5 test class using java.net.http.HttpClient",
    code_start=re.compile(r"^\s*(package |import |public |private |class |@|//|/\*)"),
)

GO = Language(
    name="go", label="Go", aliases=("go", "golang"),
    framework="a Go test file using the standard testing and net/http packages",
    code_start=re.compile(r"^\s*(package |import |func |var |const |type |//)"),
)

CSHARP = Language(
    name="csharp", label="C#", aliases=("csharp", "cs", "c#"),
    framework="an xUnit test class using System.Net.Http.HttpClient",
    code_start=re.compile(r"^\s*(using |namespace |public |private |class |\[|//|/\*)"),
)

ALL = (PYTHON, TYPESCRIPT, JAVA, GO, CSHARP)
DEFAULT = PYTHON

_BY_KEY = {L.name: L for L in ALL}
_BY_KEY.update({alias: L for L in ALL for alias in L.aliases})


def resolve(language) -> Language:
    """Language for a canonical name or short code; Python for anything unknown."""
    if isinstance(language, Language):
        return language
    return _BY_KEY.get((language or "").strip().lower(), DEFAULT)
