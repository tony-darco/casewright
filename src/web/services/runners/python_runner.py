"""Python runner: pytest + requests in a slim Python image."""

from web.services.runners.base import LanguageRunner


class PythonRunner(LanguageRunner):
    name = "python"

    def image(self, settings) -> str:
        return settings.get("python_image", "python:3.12-slim")

    def filename(self) -> str:
        return "test_generated.py"

    def command(self, filename) -> list:
        # Install the runtime deps the generated tests use, then run pytest. urllib3 is
        # pinned to the 1.26 line on purpose: generated code commonly configures retries
        # with Retry(method_whitelist=...), which urllib3 2.0 removed (renamed
        # allowed_methods). 1.26 accepts BOTH spellings, so tests run whichever the model
        # emits — and pinning keeps runs reproducible instead of drifting with PyPI.
        return ["sh", "-c",
                f"pip install -q pytest requests 'urllib3<2' && pytest -q /work/{filename}"]
