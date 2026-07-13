"""Resolve a test's language to a runner.

Python, Go, and a generic script runner are implemented now. TypeScript/Java/C#
resolve to a clear NotImplementedError (surfaced as the run's ``error``) rather than
failing silently — they can be added later against the same LanguageRunner interface.
"""

from rag.graph import languages
from web.services.runners.go_runner import GoRunner
from web.services.runners.python_runner import PythonRunner
from web.services.runners.script_runner import ScriptRunner

_RUNNERS = {
    "python": PythonRunner,
    "go": GoRunner,
    "script": ScriptRunner,
}

_NOT_YET = {"typescript", "java", "csharp"}


def get_runner(language):
    """Return a runner instance for ``language`` (canonical name or UI code). Raises
    NotImplementedError for languages without a runner yet."""
    name = languages.resolve(language).name if language != "script" else "script"
    if name in _RUNNERS:
        return _RUNNERS[name]()
    if name in _NOT_YET:
        raise NotImplementedError(f"A container runner for {name} isn't implemented yet.")
    raise NotImplementedError(f"No runner is available for language {name!r}.")
