"""SSRF guard on the user-supplied Knowledge Base spec URL — inverse policy of
ollama_admin's guard (block private/internal, allow public). Numeric IPs are used
for the block cases so the check needs no DNS.
"""

from unittest import mock

import pytest

from web.services.url_fetch import FetchError, fetch_spec_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",   # cloud metadata (link-local)
    "http://10.0.0.5/spec.json",                  # private LAN
    "http://192.168.1.20/spec.json",              # private LAN
    "http://127.0.0.1:8000/spec.json",            # loopback
])
def test_private_addresses_blocked(url):
    with pytest.raises(FetchError):
        fetch_spec_url(url)


def test_credentials_rejected():
    with pytest.raises(FetchError):
        fetch_spec_url("http://user:pass@93.184.216.34/spec.json")


def test_bad_scheme_rejected():
    with pytest.raises(FetchError):
        fetch_spec_url("ftp://93.184.216.34/spec.json")


def test_empty_url_rejected():
    with pytest.raises(FetchError):
        fetch_spec_url("")


def test_public_address_allowed_and_fetched():
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.raw.read.return_value = b'{"paths": {}}'
    with mock.patch("web.services.url_fetch.socket.getaddrinfo",
                    return_value=[(None, None, None, None, ("93.184.216.34", 80))]), \
         mock.patch("web.services.url_fetch.requests.get", return_value=resp) as get:
        content = fetch_spec_url("http://example.com/spec.json")
    assert content == b'{"paths": {}}'
    get.assert_called_once()


def test_oversized_response_rejected():
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.raw.read.return_value = b"x" * (10 * 1024 * 1024 + 1)
    with mock.patch("web.services.url_fetch.socket.getaddrinfo",
                    return_value=[(None, None, None, None, ("93.184.216.34", 80))]), \
         mock.patch("web.services.url_fetch.requests.get", return_value=resp):
        with pytest.raises(FetchError):
            fetch_spec_url("http://example.com/spec.json")


def test_unresolvable_host_rejected():
    import socket as socket_mod
    with mock.patch("web.services.url_fetch.socket.getaddrinfo",
                    side_effect=socket_mod.gaierror):
        with pytest.raises(FetchError):
            fetch_spec_url("http://doesnotexist.invalid/spec.json")
