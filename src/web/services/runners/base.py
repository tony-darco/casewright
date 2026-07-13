"""Common ephemeral-container runner machinery (Run feature).

A LanguageRunner knows three things about its language: which image to use, what to
name the file inside the container, and the command that runs it. The shared
``LanguageRunner.run`` handles the Docker lifecycle for all of them — write the code
to a temp dir, start a resource-capped container, stream its logs, wait with a
timeout, and clean up per the user's policy. The Docker SDK is imported lazily so a
missing/unreachable daemon surfaces as ``DockerUnavailable`` at run time (reported as
the run's ``error``), not at import time.
"""

import logging
import os
import tempfile
from dataclasses import dataclass

logger = logging.getLogger("web.runners")


class DockerUnavailable(Exception):
    """The Docker daemon couldn't be reached or the container couldn't be started."""


@dataclass
class RunResult:
    ok: bool          # True only if the test command exited 0
    exit_code: int


def _client():
    try:
        import docker
    except ImportError as exc:  # pragma: no cover - dependency declared in pyproject
        raise DockerUnavailable("The docker SDK is not installed.") from exc
    try:
        return docker.from_env()
    except Exception as exc:
        raise DockerUnavailable(f"Could not connect to the Docker daemon: {exc}") from exc


class LanguageRunner:
    name = ""

    def image(self, settings) -> str:
        raise NotImplementedError

    def filename(self) -> str:
        raise NotImplementedError

    def command(self, filename) -> list:
        raise NotImplementedError

    def run(self, code, env, settings, on_log=None) -> RunResult:
        """Execute ``code`` in a fresh container and return its RunResult. Streams
        container output to ``on_log`` as ``{stage:'run', message:<line>}``."""
        client = _client()
        image = self.image(settings)
        timeout = int(settings.get("timeout_seconds", 120))
        cleanup = settings.get("cleanup_policy", "always")

        with tempfile.TemporaryDirectory(prefix="cwrun-") as workdir:
            with open(os.path.join(workdir, self.filename()), "w") as f:
                f.write(code or "")
            self._emit(on_log, f"pulling image {image}…")
            try:
                container = client.containers.run(
                    image, self.command(self.filename()),
                    volumes={workdir: {"bind": "/work", "mode": "rw"}},
                    working_dir="/work",
                    environment=env or {},
                    mem_limit=f"{int(settings.get('memory_limit_mb', 512))}m",
                    nano_cpus=int(float(settings.get("cpu_limit", 1.0)) * 1_000_000_000),
                    detach=True, stdout=True, stderr=True,
                )
            except Exception as exc:
                raise DockerUnavailable(f"Could not start the run container: {exc}") from exc

            exit_code = 1
            try:
                for chunk in container.logs(stream=True, follow=True):
                    self._emit(on_log, chunk.decode("utf-8", "replace").rstrip("\n"))
                try:
                    status = container.wait(timeout=timeout)
                    exit_code = int(status.get("StatusCode", 1))
                except Exception as exc:  # timeout or daemon hiccup
                    self._emit(on_log, f"run timed out or was interrupted: {exc}", "error")
                    self._kill(container)
                    exit_code = 124
            finally:
                self._cleanup(container, cleanup, exit_code)

        return RunResult(ok=exit_code == 0, exit_code=exit_code)

    @staticmethod
    def _emit(on_log, message, level="info"):
        if on_log and message:
            on_log({"stage": "run", "level": level, "message": message})

    @staticmethod
    def _kill(container):
        try:
            container.kill()
        except Exception:
            pass

    def _cleanup(self, container, policy, exit_code):
        remove = policy == "always" or (policy == "on_success" and exit_code == 0)
        if not remove:
            return
        try:
            container.remove(force=True)
        except Exception as exc:
            logger.warning("could not remove run container: %s", exc)
