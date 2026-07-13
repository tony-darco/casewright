"""SSRF guard on the user-supplied Ollama URL (#16).

Numeric IPs are used for the block cases so the check needs no DNS.
"""

import pytest

from web.services import ollama_admin
from web.services.ollama_admin import OllamaError, normalize_url


def test_loopback_allowed_by_default():
    assert normalize_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert normalize_url("localhost:11434") == "http://localhost:11434"   # coerced + loopback


@pytest.mark.parametrize("url", [
    "http://169.254.169.254",       # cloud metadata (link-local)
    "http://10.0.0.5:11434",        # private LAN
    "http://192.168.1.20:11434",    # private LAN
])
def test_non_loopback_blocked_by_default(url):
    with pytest.raises(OllamaError):
        normalize_url(url)


def test_credentials_rejected():
    with pytest.raises(OllamaError):
        normalize_url("http://user:pass@127.0.0.1:11434")


def test_allowlist_permits_named_host(monkeypatch):
    monkeypatch.setenv("AUTOTEST_OLLAMA_ALLOWED_HOSTS", "10.0.0.5:11434, gpu-box")
    assert normalize_url("http://10.0.0.5:11434") == "http://10.0.0.5:11434"   # host:port match
    assert normalize_url("http://gpu-box:11434") == "http://gpu-box:11434"     # host match
    with pytest.raises(OllamaError):
        normalize_url("http://10.0.0.6:11434")                                 # not on the list


def test_wildcard_disables_restriction(monkeypatch):
    monkeypatch.setenv("AUTOTEST_OLLAMA_ALLOWED_HOSTS", "*")
    assert normalize_url("http://169.254.169.254") == "http://169.254.169.254"


def test_empty_url_still_rejected():
    with pytest.raises(OllamaError):
        normalize_url("")
