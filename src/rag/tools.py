"""LangChain tools exposing runtime capabilities to a tool-calling agent.

These are the surface a future conversational/coding agent binds and invokes on
demand. The underlying traversal is deterministic Python (see rag.depgraph).
"""

from langchain_core.tools import tool

from rag.depgraph import get_default_graph


@tool
def endpoint_dependencies(endpoints: list[str], direction: str = "both") -> str:
    """Look up API call-order dependencies for one or more endpoints.

    Each endpoint is a "METHOD path" id, e.g.
    "PUT /networks/{networkId}/wireless/ssids/{number}". Returns, per endpoint:
    the ordered list of endpoints that must be called FIRST to obtain its path
    parameters (upstream dependencies, annotated with the param each supplies),
    and the endpoints that depend on it (downstream dependents).

    direction is "upstream", "downstream", or "both" (default).
    """
    return get_default_graph().render(endpoints, direction=direction)
