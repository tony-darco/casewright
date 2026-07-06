"""AutoTestLLM: an AI-agnostic agentic RAG pipeline over an OpenAPI corpus.

One class holds the whole surface — model ops (embed/chat) and RAG ops
(RAG-Fusion, LLM rerank, CRAG grade/correct, first-pass test generation) — and
assembles them into a LangGraph:

    generate_queries -> retrieve(RRF) -> rerank -> grade
                            ^                         |
                            |                    [confidence low & attempts<MAX]
                         rewrite <------------------- decide
                                                      | else
                                                   finalize -> generate -> END

The model is swappable via ProviderConfig; nothing here names Ollama directly.
Graph state carries a `messages` channel so conversational multi-turn can be
added later without reshaping the pipeline.
"""

import json
import sys
from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from rag.graph import prompts
from rag.graph.fusion import reciprocal_rank_fusion
from rag.graph.state import PipelineState
from rag.provider import (
    ProviderConfig,
    build_chat_model,
    build_embeddings,
    build_vector_store,
)

N_QUERY_VARIANTS = 3     # extra RAG-Fusion queries beyond the original
RETRIEVE_K = 8           # docs pulled per sub-query before fusion
RERANK_TOP_N = 12        # fused candidates handed to the LLM rerank/grade nodes
MAX_ATTEMPTS = 2         # CRAG correction (rewrite+re-retrieve) budget


# --- structured-output schemas for the LLM nodes ---------------------------

class QueryVariants(BaseModel):
    queries: list[str] = Field(description="alternative search queries")


class RerankOrder(BaseModel):
    order: list[int] = Field(description="candidate indices, most relevant first")


class RelevanceGrades(BaseModel):
    grades: list[bool] = Field(description="relevant flag per candidate, in input order")


class AutoTestLLM:
    def __init__(self, config: Optional[ProviderConfig] = None):
        self.config = config or ProviderConfig()
        self.chat = build_chat_model(self.config)
        self.embeddings = build_embeddings(self.config)
        self.vector_store = build_vector_store(self.embeddings)
        self.graph = self.build_graph()

    # -- agnostic model ops --------------------------------------------------

    def embed_query(self, text: str):
        return self.embeddings.embed_query(text)

    def embed_documents(self, texts):
        return self.embeddings.embed_documents(texts)

    # -- RAG-Fusion ----------------------------------------------------------

    def generate_queries(self, query: str, n: int = N_QUERY_VARIANTS):
        """Original query + up to n LLM-generated variants."""
        structured = self.chat.with_structured_output(QueryVariants)
        msgs = [
            SystemMessage(prompts.QUERY_GEN_SYSTEM.format(n=n)),
            HumanMessage(prompts.user_query(query)),
        ]
        try:
            variants = [q.strip() for q in structured.invoke(msgs).queries if q.strip()]
        except Exception:
            variants = []
        out = [query]
        for q in variants:
            if q not in out:
                out.append(q)
        return out

    def retrieve(self, query: str, k: int = RETRIEVE_K):
        return self.vector_store.similarity_search(query, k=k)

    def retrieve_fused(self, queries, k: int = RETRIEVE_K):
        ranked_lists = [self.retrieve(q, k=k) for q in queries]
        return [doc for doc, _ in reciprocal_rank_fusion(ranked_lists)]

    # -- LLM reranker --------------------------------------------------------

    def rerank(self, query: str, docs, top_n: int = RERANK_TOP_N):
        pool = docs[:top_n]
        if len(pool) <= 1:
            return list(docs)
        rendered = "\n".join(f"[{i}] {self._render(d)}" for i, d in enumerate(pool))
        structured = self.chat.with_structured_output(RerankOrder)
        msgs = [
            SystemMessage(prompts.RERANK_SYSTEM),
            HumanMessage(prompts.user_candidates(query, rendered)),
        ]
        try:
            order = structured.invoke(msgs).order
        except Exception:
            return list(docs)
        reranked, seen = [], set()
        for i in order:
            if isinstance(i, int) and 0 <= i < len(pool) and i not in seen:
                reranked.append(pool[i])
                seen.add(i)
        for i, d in enumerate(pool):        # append any indices the model dropped
            if i not in seen:
                reranked.append(d)
        return reranked + list(docs[top_n:])

    # -- CRAG grader + correction -------------------------------------------

    def grade(self, query: str, docs, top_n: int = RERANK_TOP_N):
        """Return (kept_relevant_docs, confidence)."""
        pool = docs[:top_n]
        if not pool:
            return [], "low"
        rendered = "\n".join(f"[{i}] {self._render(d)}" for i, d in enumerate(pool))
        structured = self.chat.with_structured_output(RelevanceGrades)
        msgs = [
            SystemMessage(prompts.GRADE_SYSTEM),
            HumanMessage(prompts.user_candidates(query, rendered)),
        ]
        try:
            grades = structured.invoke(msgs).grades
        except Exception:
            return pool[:5], "high"          # grader failed -> keep top few, don't loop forever
        kept = [d for d, ok in zip(pool, grades) if ok]
        return kept, ("high" if kept else "low")

    def rewrite_query(self, original_query: str):
        msgs = [
            SystemMessage(prompts.REWRITE_SYSTEM),
            HumanMessage(prompts.user_query(original_query)),
        ]
        try:
            text = (self.chat.invoke(msgs).content or "").strip()
            return text or original_query
        except Exception:
            return original_query

    # -- generation (first pass) --------------------------------------------

    def generate_tests(self, query: str, docs):
        if not docs:
            return ""
        context = "\n\n".join(self._render_full(d) for d in docs)
        msgs = [
            SystemMessage(prompts.GENERATE_SYSTEM),
            HumanMessage(prompts.user_generate(query, context)),
        ]
        try:
            return self.chat.invoke(msgs).content or ""
        except Exception:
            return ""

    # -- rendering / mapping helpers ----------------------------------------

    @staticmethod
    def _operation(doc):
        try:
            return json.loads(doc.page_content)
        except (ValueError, TypeError):
            return {}

    def _render(self, doc):
        """One-line candidate for rerank/grade prompts."""
        eid = doc.metadata.get("endpoint_id", "")
        op = self._operation(doc)
        summary = (op.get("summary") or op.get("description") or "").strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157] + "..."
        return f"{eid} — {summary}".strip(" —")

    def _render_full(self, doc):
        """Richer context block for generation."""
        eid = doc.metadata.get("endpoint_id", "")
        op = self._operation(doc)
        summary = (op.get("summary") or "").strip()
        params = [p.get("name") for p in op.get("parameters", []) if isinstance(p, dict)]
        return f"{eid}\n  summary: {summary}\n  parameters: {', '.join(p for p in params if p)}"

    @staticmethod
    def _endpoint_ids(docs):
        ids, seen = [], set()
        for doc in docs:
            eid = doc.metadata.get("endpoint_id")
            if eid and eid not in seen:
                ids.append(eid)
                seen.add(eid)
        return ids

    # -- graph ---------------------------------------------------------------

    def build_graph(self):
        builder = StateGraph(PipelineState)

        def generate_queries_node(state):
            return {"sub_queries": self.generate_queries(state["query"])}

        def retrieve_node(state):
            return {"candidates": self.retrieve_fused(state["sub_queries"])}

        def rerank_node(state):
            return {"ranked": self.rerank(state["query"], state["candidates"])}

        def grade_node(state):
            kept, confidence = self.grade(state["query"], state["ranked"])
            return {"graded": kept, "confidence": confidence}

        def rewrite_node(state):
            new_query = self.rewrite_query(state.get("original_query", state["query"]))
            return {"query": new_query, "attempts": state.get("attempts", 0) + 1}

        def finalize_node(state):
            docs = state.get("graded") or state.get("ranked") or []
            return {"endpoints": self._endpoint_ids(docs)}

        def generate_node(state):
            docs = state.get("graded") or state.get("ranked") or []
            question = state.get("original_query", state["query"])
            return {"tests": self.generate_tests(question, docs)}

        def decide(state) -> Literal["rewrite", "finalize"]:
            if state.get("confidence") == "low" and state.get("attempts", 0) < MAX_ATTEMPTS:
                return "rewrite"
            return "finalize"

        builder.add_node("generate_queries", generate_queries_node)
        builder.add_node("retrieve", retrieve_node)
        builder.add_node("rerank", rerank_node)
        builder.add_node("grade", grade_node)
        builder.add_node("rewrite", rewrite_node)
        builder.add_node("finalize", finalize_node)
        builder.add_node("generate", generate_node)

        builder.add_edge(START, "generate_queries")
        builder.add_edge("generate_queries", "retrieve")
        builder.add_edge("retrieve", "rerank")
        builder.add_edge("rerank", "grade")
        builder.add_conditional_edges("grade", decide,
                                      {"rewrite": "rewrite", "finalize": "finalize"})
        builder.add_edge("rewrite", "generate_queries")   # re-fuse on the rewritten query
        builder.add_edge("finalize", "generate")
        builder.add_edge("generate", END)

        return builder.compile()

    # -- entry points --------------------------------------------------------

    def run(self, query: str):
        return self.graph.invoke({"query": query, "original_query": query, "attempts": 0})

    async def ainvoke(self, query: str):
        return await self.graph.ainvoke({"query": query, "original_query": query, "attempts": 0})


if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else "List the organizations"
    state = AutoTestLLM().run(question)

    print(f"=== Query: {question}")
    print("\n=== Retrieved endpoints ===")
    for endpoint in state.get("endpoints", []):
        print(" ", endpoint)
    print("\n=== Generated tests (first pass) ===")
    print(state.get("tests") or "(none)")
