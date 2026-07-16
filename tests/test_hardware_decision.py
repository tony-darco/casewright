"""The hardware-decision node: returns structured requirements, degrades gracefully
on bad output, and surfaces a backend outage like the other nodes.

Follows the mocking idiom in test_pipeline_failures (patch the model builders, feed a
fake chat)."""

from unittest import mock

import pytest

import rag.pipeline as p
from rag.pipeline import HardwareRequirement, HardwareRequirements

CONN = ConnectionError("Connection refused: max retries exceeded")
BAD_OUTPUT = ValueError("could not parse structured output")


def _pipeline(chat):
    with mock.patch.object(p, "build_chat_model", lambda c: chat), \
         mock.patch.object(p, "build_embeddings", lambda c: mock.Mock()), \
         mock.patch.object(p, "build_vector_store", lambda c, e: mock.Mock()):
        return p.AutoTestLLM(eval_mode=False)


def _chat_returning(reqs):
    chat = mock.Mock()
    chat.with_structured_output.return_value.invoke.return_value = HardwareRequirements(devices=reqs)
    return chat


def test_returns_requirements_as_dicts():
    chat = _chat_returning([HardwareRequirement(type="wireless", count=2, reason="SSID test")])
    llm = _pipeline(chat)
    out = llm.decide_hardware("test the wireless ssid", [])
    assert out == [{"type": "wireless", "count": 2, "reason": "SSID test"}]


def test_empty_when_no_hardware_needed():
    llm = _pipeline(_chat_returning([]))
    assert llm.decide_hardware("list organizations", []) == []


def test_bad_output_degrades_to_no_hardware():
    chat = mock.Mock()
    chat.with_structured_output.return_value.invoke.side_effect = BAD_OUTPUT
    llm = _pipeline(chat)
    assert llm.decide_hardware("anything", []) == []


def test_backend_outage_surfaces():
    chat = mock.Mock()
    chat.with_structured_output.return_value.invoke.side_effect = CONN
    llm = _pipeline(chat)
    with pytest.raises(ConnectionError):
        llm.decide_hardware("anything", [])
