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
    "You decide which candidate API endpoints a test for the user's request should "
    "exercise. First judge the request's SCOPE. If it names ONE specific operation, be "
    "strict: mark true only for the endpoint(s) whose HTTP method and resource directly "
    "perform that operation, and false for anything merely topically related — a "
    "different resource, a different action, or the same method on an unrelated path. "
    "If instead it asks for a BROAD, comprehensive, multi-endpoint, or integration test "
    "over a resource area — or to 'test everything about X' — mark true for every "
    "candidate that belongs to that area so the test can span them, and false only for "
    "candidates outside it. Do not include prerequisite/parameter-supplying endpoints "
    "here; those are added separately from the dependency graph. Return one boolean per "
    "candidate, in the same order as given."
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
        "already provided below: call each such listed producer endpoint first to obtain "
        "the path parameter it supplies (treat it as a setup step/fixture), and only then "
        "call the target endpoint. The organization ID, network ID, API key, and device "
        "serial are ALWAYS provided to your code through environment variables — read "
        "MERAKI_ORG_ID, MERAKI_NETWORK_ID, MERAKI_API_KEY, and MERAKI_DEVICE_SERIAL from "
        "the environment (e.g. os.getenv) and use those values directly. NEVER hardcode "
        "an organization id, network id, or device serial, and "
        "NEVER list, search, or filter organizations or networks to discover or verify "
        "them (a dependency marked '<-caller' means the value is supplied this way) — do "
        "not derive a network from a device name or model. The network id refers to a "
        "fresh network provisioned for this run, and MERAKI_DEVICE_SERIAL is a device "
        "freshly claimed for this run, so treat both as already existing: use them "
        "directly, do not create them or assert them into existence. A device serial is "
        "NEVER a model name — 'MR42' is a model, so use MERAKI_DEVICE_SERIAL and never a "
        "made-up string like 'MR42-1234567890'. More generally, never "
        "call a listing endpoint just to search or filter for an organization, network, "
        "or device you already have. Any base URL and device serials given below as "
        "concrete values should be used literally — do NOT leave placeholders like "
        "YOUR_ORG_ID for anything that was supplied. "
        "Build every request path from an endpoint's path EXACTLY as given, substituting "
        "each path parameter in place and adding no segments. Meraki paths are flat by "
        "resource and are NEVER nested inside one another: organization endpoints look "
        "like '/organizations/{organizationId}/...', network endpoints like "
        "'/networks/{networkId}/...', and device endpoints like '/devices/{serial}/...'. "
        "Put MERAKI_ORG_ID only in an {organizationId} slot, MERAKI_NETWORK_ID only in a "
        "{networkId} slot, and a serial only in a {serial} slot — never combine them into "
        "one path like '/organizations/{orgId}/networks/{networkId}/devices/{serial}/...' "
        "(no such route exists; it 404s). Do not fabricate a device serial from a model "
        "name or an @-mention (e.g. 'MR42' is a model, not a serial); use a serial only "
        "if one was supplied as a concrete value. "
        "Each test function is independent and shares no state with another: set up or "
        "fetch everything a test needs inside that test (or a fixture it depends on), and "
        "never reference a variable defined in a different test function. "
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
        "Change only what the failure requires: keep the same endpoints and structure. "
        "The organization ID, network ID, and API key are provided through environment "
        "variables (MERAKI_ORG_ID, MERAKI_NETWORK_ID, MERAKI_API_KEY) — read them from "
        "the environment. Code that hardcodes an org/network id, or lists/filters "
        "organizations or networks to discover or verify one (e.g. filtering by a device "
        "name/model like 'MR42', or asserting the network is present in a list), is the "
        "bug — replace it with a direct read of the environment variable. The network id "
        "is a fresh network provisioned for this run; use it directly, don't verify it "
        "exists. Any base URL and device serials supplied as concrete values stay as "
        "literals; do not invent placeholders for values that were supplied.\n"
        "A 404 on every call usually means the paths are wrong: Meraki paths are flat by "
        "resource and never nested — '/networks/{networkId}/...' and '/devices/{serial}/"
        "...' are correct, but '/organizations/{orgId}/networks/{networkId}/...' or "
        "'/networks/{networkId}/devices/{serial}/...' do not exist. Put each id only in "
        "its own path slot (org id in {organizationId}, network id in {networkId}, serial "
        "in {serial}). A NameError or a test using a value from another test means the "
        "functions wrongly share state — make each test set up what it needs itself. Do "
        "not use a model name (e.g. 'MR42') as a device serial.\n"
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
