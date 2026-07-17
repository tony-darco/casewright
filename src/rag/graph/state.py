"""LangGraph state for the RAG pipeline.

`messages` is present now but unused: it carries the reducer needed for future
multi-turn conversation, so adding chat history later is a state read, not a
restructure. `total=False` lets nodes return partial updates.
"""

from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langgraph.graph.message import add_messages


class PipelineState(TypedDict, total=False):
    query: str                                  # current (possibly rewritten) retrieval query
    original_query: str                         # the user's question, preserved across rewrites
    messages: Annotated[list, add_messages]     # reserved for conversational multi-turn
    sub_queries: list[str]                       # RAG-Fusion variants (incl. the original)
    candidates: list[Document]                   # RRF-fused candidate pool
    ranked: list[Document]                       # after LLM rerank
    graded: list[Document]                       # CRAG-kept (relevant) docs
    confidence: str                              # "high" | "low" (drives the correction loop)
    attempts: int                                # correction-loop counter
    endpoints: list[str]                         # "METHOD path" ids retrieved (the test targets)
    dependency_endpoints: list[str]              # upstream producers from the graph (prerequisites)
    dependencies: str                            # rendered call-order deps for the endpoints
    language: str                                # target language for generation/sanitization
    tests: str                                   # first-pass generated test code (sanitized)
    validation: dict                             # {ok, method, detail, language} for the code
    hardware: list                               # [{type, count, reason}] physical Meraki hardware the run needs
    repair: dict                                 # {code, status, stage, output} of a failed run; set = fix that
                                                 # code instead of writing fresh (skips retrieval, reusing the
                                                 # endpoints the first pass already grounded on)
