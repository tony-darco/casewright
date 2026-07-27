"""Per-case orchestration: generate → score endpoints → judge code → check it runs.

``run_case`` takes the ``generate`` and ``judge`` callables as parameters so tests can
inject stubs (no Ollama, no Chroma). The real ``generate`` drives the full pipeline
in-process; the real ``judge`` is :func:`eval.e2e.judge.judge_code`.

A case "meets the goal" when all three objective checks pass: endpoint recall == 1.0,
the code compiles/parses (offline first-run), and the LLM judge passes.
"""

from eval.e2e.judge import judge_code
from eval.e2e.scoring import score_endpoints


def generate_real(prompt: str, language: str = "py") -> dict:
    """Run the full generation pipeline in-process and return what the harness scores.

    ``endpoints_used`` is the union of the grader's targets and the dependency-graph
    prerequisites — exactly the set the pipeline grounded the generation on.
    """
    from rag.pipeline import AutoTestLLM

    state = AutoTestLLM(eval_mode=False).run(prompt, language)
    return {
        "code": state.get("tests", "") or "",
        "endpoints_used": list(state.get("endpoints") or []) + list(state.get("dependency_endpoints") or []),
        "validation": state.get("validation") or {},
    }


def run_case(case: dict, generate=generate_real, judge=judge_code) -> dict:
    """Run one case end-to-end (offline tier) and return a scored result dict."""
    prompt = case["prompt"]
    language = case.get("language", "py")
    expected = case.get("expected_endpoints", []) or []

    gen = generate(prompt, language)
    code = gen.get("code", "") or ""
    endpoints_used = gen.get("endpoints_used", []) or []
    validation = gen.get("validation") or {}

    endpoints = score_endpoints(endpoints_used, expected)
    # Offline "runs first try": the validator confirms the code parses/compiles for its
    # language. ``ok`` is True (valid), False (invalid/wrong language), or None (unknown).
    compiles = validation.get("ok")
    code_judge = judge(prompt, case.get("reference_script", ""), code, endpoints_used, expected)

    met_goal = bool(endpoints["passed"] and compiles is True and code_judge["pass"])
    return {
        "id": case.get("id", "?"),
        "prompt": prompt,
        "language": language,
        "needs_ap": bool(case.get("needs_ap")),
        "code": code,
        "endpoints_used": sorted(endpoints["retrieved"]),
        "endpoints": endpoints,
        "compiles": compiles,
        "compile_detail": validation.get("detail", ""),
        "code_judge": code_judge,
        "met_goal": met_goal,
    }


def run_cases(cases, generate=generate_real, judge=judge_code) -> list:
    return [run_case(c, generate=generate, judge=judge) for c in cases]
