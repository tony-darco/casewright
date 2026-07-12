"""A down model backend must be reported, not silently degraded.

The retrieval nodes swallow bad/unparseable model *responses* (graceful fallback),
but a backend that's simply unreachable has to surface — otherwise an outage looks
like "nothing retrieved". See is_connection_error + the retrieval nodes in pipeline.
"""

from unittest import mock

import pytest

import rag.pipeline as p
from rag.provider import is_connection_error

CONN = ConnectionError("Connection refused: max retries exceeded")
BAD_OUTPUT = ValueError("could not parse structured output")


def _pipeline(chat, store):
    with mock.patch.object(p, "build_chat_model", lambda c: chat), \
         mock.patch.object(p, "build_embeddings", lambda c: mock.Mock()), \
         mock.patch.object(p, "build_vector_store", lambda c, e: store):
        return p.AutoTestLLM(eval_mode=False)


def _chat_raising(exc):
    """A chat whose structured and plain invocations both raise ``exc``."""
    chat = mock.Mock()
    chat.with_structured_output.return_value.invoke.side_effect = exc
    chat.invoke.side_effect = exc
    return chat


# --- is_connection_error -----------------------------------------------------

def test_detects_connection_errors():
    assert is_connection_error(ConnectionError("boom"))
    assert is_connection_error(TimeoutError())
    assert is_connection_error(Exception("Max retries exceeded with url"))
    # wrapped in a cause chain
    try:
        try:
            raise ConnectionError("refused")
        except ConnectionError as e:
            raise RuntimeError("model call failed") from e
    except RuntimeError as wrapped:
        assert is_connection_error(wrapped)


def test_ignores_bad_model_output():
    assert not is_connection_error(ValueError("could not parse structured output"))
    assert not is_connection_error(KeyError("grades"))


# --- retrieval nodes: swallow bad output, surface an outage ------------------

def test_generate_queries_swallows_bad_output():
    llm = _pipeline(_chat_raising(BAD_OUTPUT), mock.Mock())
    # falls back to just the original query — no raise
    assert llm.generate_queries("list orgs") == ["list orgs"]


def test_generate_queries_reraises_connection_error():
    llm = _pipeline(_chat_raising(CONN), mock.Mock())
    with pytest.raises(ConnectionError):
        llm.generate_queries("list orgs")


def test_grade_swallows_bad_output():
    llm = _pipeline(_chat_raising(BAD_OUTPUT), mock.Mock())
    kept, confidence = llm.grade("q", [mock.Mock(), mock.Mock()])
    assert confidence == "high"          # graceful fallback


def test_grade_reraises_connection_error():
    llm = _pipeline(_chat_raising(CONN), mock.Mock())
    with pytest.raises(ConnectionError):
        llm.grade("q", [mock.Mock(), mock.Mock()])


# --- the reported bug: backend down + empty retrieval must not look "empty" --

def test_run_reraises_when_backend_down_and_retrieval_empty():
    store = mock.Mock()
    store.similarity_search.return_value = []      # reachable but nothing to return
    llm = _pipeline(_chat_raising(CONN), store)
    with pytest.raises(ConnectionError):
        llm.run("list orgs", language="py")
