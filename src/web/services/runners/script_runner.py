"""Generic script runner: execute the file directly in a configurable base image.

A catch-all for languages/formats that run as a single script via a shebang. Not
wired to any generated language today, but available behind the same interface.
"""

from web.services.runners.base import LanguageRunner


class ScriptRunner(LanguageRunner):
    name = "script"

    def image(self, settings) -> str:
        return settings.get("script_image", "ubuntu:24.04")

    def filename(self) -> str:
        return "script.sh"

    def command(self, filename) -> list:
        return ["sh", "-c", f"cd /work && chmod +x {filename} && ./{filename}"]
