"""Python runner: pytest + requests in a slim Python image."""

from web.services.runners.base import LanguageRunner


class PythonRunner(LanguageRunner):
    name = "python"

    def image(self, settings) -> str:
        return settings.get("python_image", "python:3.12-slim")

    def filename(self) -> str:
        return "test_generated.py"

    def command(self, filename) -> list:
        # Install the two runtime deps the generated tests use, then run pytest.
        return ["sh", "-c", f"pip install -q pytest requests && pytest -q /work/{filename}"]
