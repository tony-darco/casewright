"""Model-provider settings: the config.yaml-backed store and the Ollama admin client."""

import json
from unittest import mock

import pytest
import requests

from web.services import ollama_admin, provider_store


# --- provider_store ---------------------------------------------------------------

def test_defaults_when_unset():
    assert provider_store.get_settings() == provider_store.DEFAULTS
    assert provider_store.overrides() == {}   # nothing set -> pipeline stays on env defaults


def test_save_and_overrides_roundtrip():
    provider_store.save_settings("ollama", "http://box:11434", "mistral-small:22b",
                                 "nomic-embed-text:latest", 0.2)
    s = provider_store.get_settings()
    assert s["ollama_url"] == "http://box:11434"
    assert s["chat_model"] == "mistral-small:22b"
    assert provider_store.overrides() == {
        "provider": "ollama",
        "base_url": "http://box:11434",
        "chat_model": "mistral-small:22b",
        "embed_model": "nomic-embed-text:latest",
        "temperature": 0.2,
    }


def test_blank_fields_are_not_overrides():
    provider_store.save_settings("ollama", "http://box:11434", "", "", None)
    ov = provider_store.overrides()
    assert "chat_model" not in ov and "embed_model" not in ov and "temperature" not in ov
    assert ov["base_url"] == "http://box:11434"


def test_save_overwrites_the_previous_values():
    provider_store.save_settings("ollama", "http://a:11434", "m1", "", None)
    provider_store.save_settings("ollama", "http://b:11434", "m2", "", 1.0)
    s = provider_store.get_settings()
    assert (s["ollama_url"], s["chat_model"], s["temperature"]) == ("http://b:11434", "m2", 1.0)


# --- ollama_admin -----------------------------------------------------------------

def _resp(status=200, payload=None, lines=None):
    r = mock.Mock()
    r.status_code = status
    if payload is None:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = payload
    r.iter_lines.return_value = [json.dumps(l).encode() for l in (lines or [])]
    return r


def test_normalize_url():
    assert ollama_admin.normalize_url(" 192.168.1.17:11434/ ") == "http://192.168.1.17:11434"
    assert ollama_admin.normalize_url("https://box:11434") == "https://box:11434"
    with pytest.raises(ollama_admin.OllamaError):
        ollama_admin.normalize_url("   ")


def test_check_server_up_and_down():
    with mock.patch.object(ollama_admin.requests, "get", return_value=_resp(payload={"version": "0.6.2"})):
        assert ollama_admin.check_server("http://box:11434") == "0.6.2"
    with mock.patch.object(ollama_admin.requests, "get", side_effect=requests.ConnectionError()):
        with pytest.raises(ollama_admin.OllamaError, match="Could not reach"):
            ollama_admin.check_server("http://box:11434")
    with mock.patch.object(ollama_admin.requests, "get", return_value=_resp(status=404, payload={})):
        with pytest.raises(ollama_admin.OllamaError, match="is it Ollama"):
            ollama_admin.check_server("http://box:11434")


def test_list_models_and_exists():
    tags = {"models": [{"name": "b:latest"}, {"name": "a:22b"}]}
    with mock.patch.object(ollama_admin.requests, "get", return_value=_resp(payload=tags)):
        assert ollama_admin.list_models("http://box:11434") == ["a:22b", "b:latest"]
        assert ollama_admin.model_exists("http://box:11434", "b:latest")
        assert ollama_admin.model_exists("http://box:11434", "b")       # bare name -> :latest
        assert not ollama_admin.model_exists("http://box:11434", "a")   # a:22b is NOT a:latest
        assert not ollama_admin.model_exists("http://box:11434", "nope")


def test_pull_model_success_consumes_stream():
    r = _resp(payload={}, lines=[{"status": "pulling"}, {"status": "success"}])
    with mock.patch.object(ollama_admin.requests, "post", return_value=r):
        ollama_admin.pull_model("http://box:11434", "some-model")  # no raise


def test_pull_model_unknown_name_raises():
    # Ollama reports a bad model name as an error event in the NDJSON stream…
    r = _resp(payload={}, lines=[{"error": "pull model manifest: file does not exist"}])
    with mock.patch.object(ollama_admin.requests, "post", return_value=r):
        with pytest.raises(ollama_admin.OllamaError, match="does not exist"):
            ollama_admin.pull_model("http://box:11434", "nope")
    # …or as an HTTP error with an error body.
    with mock.patch.object(ollama_admin.requests, "post", return_value=_resp(status=500, payload={"error": "unknown model"})):
        with pytest.raises(ollama_admin.OllamaError, match="unknown model"):
            ollama_admin.pull_model("http://box:11434", "nope")


def test_pull_model_server_lost_mid_stream():
    with mock.patch.object(ollama_admin.requests, "post", side_effect=requests.ConnectionError()):
        with pytest.raises(ollama_admin.OllamaError, match="lost the Ollama server"):
            ollama_admin.pull_model("http://box:11434", "some-model")


def test_reasoning_tristate_unset_on_off():
    """reasoning follows temperature's contract: NULL/unset = keep the backend
    default (absent from overrides), while On/Off are explicit user choices."""
    provider_store.save_settings("ollama", "http://box:11434", "", "", None, reasoning=None)
    assert provider_store.get_settings()["reasoning"] is None
    assert "reasoning" not in provider_store.overrides()

    provider_store.save_settings("ollama", "http://box:11434", "", "", None, reasoning=True)
    assert provider_store.overrides()["reasoning"] is True

    provider_store.save_settings("ollama", "http://box:11434", "", "", None, reasoning=False)
    assert provider_store.overrides()["reasoning"] is False
