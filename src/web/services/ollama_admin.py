"""Thin Ollama admin client used by the Settings model-provider section.

Talks to a user-supplied Ollama server to confirm it's up, list its installed
models, and pull missing ones. This is a pure client: the caller passes the
base URL in (looked up from the per-user provider settings — see
web.services.provider_store). Any failure raises ``OllamaError`` with a
human-readable message for the inline error slot.
"""

import ipaddress
import json
import os
import socket
from urllib.parse import urlparse

import requests

_TIMEOUT = 10
# Pulls stream NDJSON progress; allow generous gaps between chunks (large layers).
_PULL_TIMEOUT = (10, 300)


class OllamaError(Exception):
    """A check/list/pull failed; the message is safe to show inline."""


def _allowed_hosts() -> list:
    """Operator-configured hosts a user may point Ollama at (comma-separated
    ``host`` or ``host:port``; ``*`` disables the restriction). Empty by default."""
    raw = os.getenv("AUTOTEST_OLLAMA_ALLOWED_HOSTS", "").strip()
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _guard_ssrf(url: str) -> None:
    """Refuse a user-supplied Ollama URL that would make the server dial somewhere it
    shouldn't (SSRF, #16). Default policy: only loopback is allowed — Ollama's default
    is localhost, so the server can only talk to itself and can't be used to probe LAN,
    the internet, or cloud metadata (169.254.169.254). Operators widen this per host via
    AUTOTEST_OLLAMA_ALLOWED_HOSTS.
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise OllamaError("The Ollama URL must start with http:// or https://.")
    if parts.username or parts.password:
        raise OllamaError("Credentials aren't allowed in the Ollama URL.")
    host = parts.hostname
    if not host:
        raise OllamaError("Enter a valid Ollama server URL.")

    allow = _allowed_hosts()
    if "*" in allow:
        return                                  # operator opted out of the restriction
    if allow:                                   # explicit allowlist: match host or host:port
        candidates = {host.lower()}
        if parts.port:
            candidates.add(f"{host.lower()}:{parts.port}")
        if candidates & set(allow):
            return
        raise OllamaError(
            f"'{host}' isn't an allowed Ollama host. Ask your admin to add it "
            "(AUTOTEST_OLLAMA_ALLOWED_HOSTS)."
        )

    # default: loopback only. Resolve the host and block if ANY address isn't loopback.
    try:
        infos = socket.getaddrinfo(host, parts.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise OllamaError(f"Couldn't resolve the Ollama host '{host}'.")
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_loopback:
            raise OllamaError(
                f"For safety, only a local (loopback) Ollama URL is allowed by default. "
                f"To use '{host}', set AUTOTEST_OLLAMA_ALLOWED_HOSTS on the server."
            )


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        raise OllamaError("Enter the Ollama server URL.")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    _guard_ssrf(url)
    return url


def check_server(url: str) -> str:
    """Confirm the server is up and is Ollama. Returns its version string."""
    url = normalize_url(url)
    try:
        resp = requests.get(f"{url}/api/version", timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach the Ollama server at {url} ({exc.__class__.__name__}).")
    if resp.status_code >= 400:
        raise OllamaError(f"The server at {url} responded with HTTP {resp.status_code} — is it Ollama?")
    try:
        return str(resp.json().get("version", "unknown"))
    except ValueError:
        raise OllamaError(f"Unexpected (non-JSON) response from {url} — is it Ollama?")


def list_models(url: str) -> list:
    """Names of the models installed on the server (``/api/tags``)."""
    url = normalize_url(url)
    try:
        resp = requests.get(f"{url}/api/tags", timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach the Ollama server at {url} ({exc.__class__.__name__}).")
    if resp.status_code >= 400:
        raise OllamaError(f"Ollama returned HTTP {resp.status_code} listing models.")
    try:
        models = resp.json().get("models", [])
    except ValueError:
        raise OllamaError("Unexpected (non-JSON) response listing models.")
    return sorted(m.get("name", "") for m in models if isinstance(m, dict) and m.get("name"))


def model_exists(url: str, name: str) -> bool:
    """True if ``name`` is installed on the server. A bare name matches its
    ``:latest`` tag, but NOT other tags — entering ``foo`` when only ``foo:22b``
    exists is a miss, since pulling ``foo`` would fetch ``foo:latest``, a
    different model."""
    name = (name or "").strip()
    installed = set(list_models(url))
    return name in installed or (":" not in name and f"{name}:latest" in installed)


def pull_model(url: str, name: str) -> None:
    """Pull ``name`` onto the server, blocking until it finishes.

    Raises ``OllamaError`` if the name doesn't exist upstream (Ollama reports it
    either as an HTTP error or as an ``error`` event mid-stream) or the server
    becomes unreachable during the download.
    """
    url = normalize_url(url)
    name = (name or "").strip()
    try:
        resp = requests.post(f"{url}/api/pull", json={"model": name}, stream=True, timeout=_PULL_TIMEOUT)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("error", "")
            except ValueError:
                detail = ""
            raise OllamaError(
                f"Couldn't pull '{name}': {detail or f'HTTP {resp.status_code}'}. "
                "Check the model name (ollama.com/library)."
            )
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("error"):
                raise OllamaError(f"Couldn't pull '{name}': {event['error']}")
    except requests.RequestException as exc:
        raise OllamaError(f"Pull of '{name}' failed — lost the Ollama server ({exc.__class__.__name__}).")
