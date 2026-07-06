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
    "You judge whether each retrieved API endpoint is relevant to the user query "
    "— i.e. whether it would plausibly be needed to fulfil the request (including "
    "endpoints that supply required path parameters). For each candidate return "
    "true if relevant, false otherwise, in the same order as given."
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
    "the requests library that exercises those endpoints. Call endpoints that "
    "produce ids before the endpoints that consume them. Use placeholder base "
    "URLs/credentials via variables. Output only Python code."
)


def user_query(query: str) -> str:
    return f"Request: {query}"


def user_candidates(query: str, rendered_candidates: str) -> str:
    return f"Query: {query}\n\nCandidates:\n{rendered_candidates}"


def user_generate(query: str, context: str) -> str:
    return f"Request: {query}\n\nRelevant endpoints:\n{context}"
