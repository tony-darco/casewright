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

HARDWARE_SYSTEM = (
    "Given the user's request and the grounded API endpoints, decide what physical "
    "Meraki hardware — if any — a live run of this test would need to exercise "
    "realistically. A wireless test needs an access point (type 'wireless'); a "
    "firewall/VLAN/security test needs a security appliance (type 'security_appliance'); "
    "a camera test needs a camera (type 'camera'). Return an empty list if the test "
    "only touches organization/network endpoints with no device-specific hardware. "
    "Give a count of 1 unless the test clearly needs more, and a short reason each."
)


def user_hardware(query: str, endpoints: str) -> str:
    return f"Request: {query}\n\nGrounded endpoints:\n{endpoints}"


def generate_system(language_label: str, framework: str) -> str:
    """System prompt for the generate node, targeted at the selected language.

    ``language_label``/``framework`` come from the language registry so the model is
    told exactly what to write (e.g. Python + "a pytest module using requests").
    """
    return (
        f"You write API tests in {language_label}. Given a user request and the "
        "relevant OpenAPI endpoints (with methods, paths, summaries, and parameters), "
        f"write {framework} that exercises those endpoints. If call-order "
        "dependencies are provided, honor them exactly for any identifier that is NOT "
        "already given below as a concrete value: call each such listed producer "
        "endpoint first to obtain the path parameter it supplies (treat it as a setup "
        "step/fixture), and only then call the target endpoint. The organization ID and "
        "network ID are ALWAYS supplied to you as concrete values below (a dependency "
        "marked '<-caller' means exactly this): use them as literal constants and NEVER "
        "list, search, or filter organizations or networks to discover them — do not "
        "derive a network from a device name or model. More generally, never call an "
        "endpoint (e.g. list organizations, list networks, list devices) just to search "
        "or filter for an organization, network, or device you already have as a "
        "concrete value — a concrete value makes that discovery step unnecessary; use it "
        "directly. When concrete values are provided (base URL, organization ID, network "
        "ID, device serials), use those literal values in the code — do NOT leave "
        "placeholders like YOUR_ORG_ID for anything that was supplied. The API key is "
        "the only secret: read it from the environment. "
        f"Output raw {language_label} source ONLY: no Markdown code fences, no prose, "
        "no explanations before or after the code."
    )


def repair_system(language_label: str, framework: str) -> str:
    """System prompt for the repair pass: the same generation contract, but the model is
    fixing code that actually ran and failed rather than writing from scratch.

    The honesty clause matters. A run fails for two very different reasons: the test is
    wrong (fixable here), or the harness around it broke — Docker down, hardware not
    claimable, network unreachable — in which case the code may be perfectly correct and
    "fixing" it would replace working code with a guess. The model is told to say so and
    return the code unchanged rather than invent a change to look useful.
    """
    return (
        f"You repair {language_label} API tests. You are given the request the test was "
        "written for, the relevant OpenAPI endpoints, the current test code, and the "
        "output from running it — which failed. Diagnose the failure from that output "
        "and return a corrected version of the test.\n"
        "Change only what the failure requires: keep the same endpoints, structure, and "
        "the concrete identifiers already baked in (base URL, org/network ids, device "
        "serials). Do not invent placeholders for values that were supplied. If the "
        "failure came from code that searched or listed resources to find an "
        "organization, network, or device instead of using an identifier already "
        "supplied literally, that lookup is the bug — replace it with the literal value, "
        "don't just patch the search/filter logic. In particular, the organization ID "
        "and network ID are always supplied as concrete values; code that lists networks "
        "and filters by a device name/model (e.g. 'MR42' in a network name) to find the "
        "network is wrong — use the supplied network ID literally instead.\n"
        "If the output shows the failure was NOT caused by the test code — the runner or "
        "environment failed, the container could not start, hardware could not be "
        "claimed, the API was unreachable — then the code needs no change: return it "
        "exactly as given. Do not fabricate an edit to appear useful.\n"
        f"Output raw {language_label} source ONLY: no Markdown code fences, no prose, no "
        f"explanations before or after the code. ({framework})"
    )


def user_repair(repair: dict) -> str:
    """The failed run's evidence: what the code was, how it failed, and what it printed."""
    status = repair.get("status") or "failed"
    stage = repair.get("stage") or "run"
    ran = "the test executed and exited non-zero" if stage == "run" else (
        f"the run never reached the test — it failed during '{stage}'")
    parts = [
        f"The previous version of this test failed (status: {status}; {ran}).",
        "",
        "Current test code:",
        repair.get("code") or "(none)",
        "",
        "Output from the failed run:",
        repair.get("output") or "(no output captured)",
    ]
    return "\n".join(parts)


def user_query(query: str) -> str:
    return f"Request: {query}"


def user_candidates(query: str, rendered_candidates: str) -> str:
    return f"Query: {query}\n\nCandidates:\n{rendered_candidates}"


def user_generate(query: str, context: str, dependencies: str = "", repair: dict = None) -> str:
    parts = [f"Request: {query}", "", "Relevant endpoints:", context]
    if dependencies:
        parts += ["", "Call-order dependencies (call producers before consumers) — "
                       "these are a fallback for identifiers that were NOT already given "
                       "above as a concrete value; skip any step here that would "
                       "rediscover one you already have:", dependencies]
    if repair:
        parts += ["", user_repair(repair)]
    return "\n".join(parts)
