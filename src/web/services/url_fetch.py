"""SSRF-safe fetch for a user-supplied spec URL (Knowledge Base feature).

We allow the public internet (real spec-hosting URLs), but block private/loopback/
link-local/reserved/multicast addresses, since we're fetching a user-supplied URL
server-side (classic SSRF surface).
"""

import ipaddress
import socket
from urllib.parse import urlparse

import requests

_TIMEOUT = 15
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap on a spec download


class FetchError(Exception):
    """A spec URL fetch failed or was refused; the message is safe to show inline."""


def _guard_public(url: str) -> str:
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError("The URL must start with http:// or https://.")
    if parts.username or parts.password:
        raise FetchError("Credentials aren't allowed in the URL.")
    host = parts.hostname
    if not host:
        raise FetchError("Enter a valid URL.")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError(f"Couldn't resolve '{host}'.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise FetchError(f"'{host}' resolves to a non-public address and can't be fetched.")
    return url


def fetch_spec_url(url: str, timeout: int = _TIMEOUT) -> bytes:
    """Fetch a user-supplied spec URL, refusing anything that resolves to a private/
    internal address. Known limitation of the resolve-then-connect approach: a DNS
    answer could change between this check and the actual request (DNS rebinding) —
    an accepted residual risk, not a blocker for this feature."""
    url = _guard_public((url or "").strip())
    try:
        resp = requests.get(url, timeout=timeout, stream=True,
                            headers={"User-Agent": "casewright-kb-fetch/1"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FetchError(f"Could not fetch {url} ({exc.__class__.__name__}).")
    content = resp.raw.read(_MAX_BYTES + 1, decode_content=True)
    if len(content) > _MAX_BYTES:
        raise FetchError("The document is larger than 10 MB.")
    return content
