"""Prompt text for the LLM-driven graph nodes.

Templates only — no model calls, no parsing. The pipeline fills these and pairs
them with the Pydantic schemas it enforces via structured output.
"""

QUERY_GEN_SYSTEM = (
    "You expand a user's request into diverse search queries for retrieving REST "
    "API endpoints from an OpenAPI specification. Produce {n} alternative queries "
    "that rephrase the intent and surface related resources, HTTP methods, and "
    "domain terms. Keep each query short and specific. Do not answer the request."
)

RERANK_SYSTEM = (
    "You are a reranker for API-endpoint retrieval. Given a user query and a "
    "numbered list of candidate endpoints, order them from most to least "
    "relevant to the query. Return the candidate indices best-first. Include "
    "every index exactly once."
)

GRADE_SYSTEM = (
    "You decide, strictly, whether each candidate API endpoint is one the user "
    "actually needs to perform the requested action — its HTTP method and resource "
    "must directly match the operation the request describes. Mark true ONLY for "
    "such a direct match. Mark false for endpoints that are merely topically "
    "related — a different resource, a different action, or the same HTTP method on "
    "an unrelated path — even if they look similar. When in doubt, mark false. Do "
    "not include prerequisite/parameter-supplying endpoints here; those are added "
    "separately from the dependency graph. Return one boolean per candidate, in the "
    "same order as given."
)

REWRITE_SYSTEM = (
    "The previous retrieval for this request returned little that was relevant. "
    "Rewrite the request into a single improved search query for an OpenAPI "
    "endpoint corpus: make the resource nouns, HTTP action, and domain terms "
    "explicit. Return only the rewritten query."
)

GENERATE_SYSTEM = (
    "You write API tests. Given a user request and the relevant OpenAPI endpoints "
    "(with methods, paths, summaries, and parameters), write a pytest module using "
    "the requests library that exercises those endpoints. If call-order "
    "dependencies are provided, honor them exactly: call each listed producer "
    "endpoint first to obtain the path parameters it supplies (treat them as setup "
    "steps/fixtures), and only then call the target endpoint. "
    "When concrete values are provided (base URL, organization ID, network ID, "
    "device serials), use those literal values in the code — do NOT leave "
    "placeholders like YOUR_ORG_ID for anything that was supplied. The API key is "
    "the only secret: read it from the environment. "
    "Output raw Python source ONLY: no Markdown code fences, no prose, no "
    "explanations before or after the code."
)


def user_query(query: str) -> str:
    return f"Request: {query}"


def user_candidates(query: str, rendered_candidates: str) -> str:
    return f"Query: {query}\n\nCandidates:\n{rendered_candidates}"


def user_generate(query: str, context: str, dependencies: str = "") -> str:
    parts = [f"Request: {query}", "", "Relevant endpoints:", context]
    if dependencies:
        parts += ["", "Call-order dependencies (call producers before consumers):", dependencies]
    return "\n".join(parts)
