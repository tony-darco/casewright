"""Per-case orchestration: generate → score endpoints → judge code → check it runs.

``run_case`` takes the ``generate`` and ``judge`` callables as parameters so tests can
inject stubs (no Ollama, no Chroma). The real ``generate`` drives the full pipeline
in-process; the real ``judge`` is :func:`eval.e2e.judge.judge_code`.

A case "meets the goal" when all three objective checks pass: endpoint recall == 1.0,
the code compiles/parses (offline first-run), and the LLM judge passes.
"""

from eval.e2e.judge import judge_code
from eval.e2e.scoring import device_markers, score_devices, score_endpoints


def generate_real(prompt: str, language: str = "py", devices=None) -> dict:
    """Run generation in-process and return what the harness scores.

    Without ``devices`` this drives the raw pipeline. With ``devices`` (the device-adding
    path) it goes through ``web.services.generate.stream_events`` instead — that's the
    seam that injects ``{{DEVICE_SERIAL_N}}`` tokens, pins the mentioned hardware, and
    bakes known serials back into the code. ``endpoints_used`` is targets ∪ dependency
    prerequisites either way.
    """
    if devices:
        return _generate_with_devices(prompt, language, devices)

    from rag.pipeline import AutoTestLLM

    state = AutoTestLLM(eval_mode=False).run(prompt, language)
    return {
        "code": state.get("tests", "") or "",
        "endpoints_used": list(state.get("endpoints") or []) + list(state.get("dependency_endpoints") or []),
        "validation": state.get("validation") or {},
        "hardware": [],
    }


def _generate_with_devices(prompt, language, devices) -> dict:
    """Device-adding path: generate with a device roster so serial tokens are injected
    and hardware is pinned, then read the final workspace view model."""
    from web.services import generate as gen_svc

    vm = {}
    for ev in gen_svc.stream_events(prompt, devices, language=language):
        if ev.get("type") == "final":
            vm = ev.get("vm", {}) or {}
    return {
        "code": vm.get("code", "") or "",
        "endpoints_used": list(vm.get("endpoints") or []) + list(vm.get("dep_endpoints") or []),
        "validation": vm.get("validation") or {},
        "hardware": vm.get("hardware") or [],
    }


def run_case(case: dict, generate=generate_real, judge=judge_code) -> dict:
    """Run one case end-to-end (offline tier) and return a scored result dict."""
    prompt = case["prompt"]
    language = case.get("language", "py")
    expected = case.get("expected_endpoints", []) or []
    devices = case.get("devices") or []

    gen = generate(prompt, language, devices)
    code = gen.get("code", "") or ""
    endpoints_used = gen.get("endpoints_used", []) or []
    validation = gen.get("validation") or {}

    endpoints = score_endpoints(endpoints_used, expected)
    # Offline "runs first try": the validator confirms the code parses/compiles for its
    # language. ``ok`` is True (valid), False (invalid/wrong language), or None (unknown).
    compiles = validation.get("ok")
    code_judge = judge(prompt, case.get("reference_script", ""), code, endpoints_used, expected)
    # Device-adding check: is the declared device pinned + present in the code?
    devices_score = score_devices(code, device_markers(devices), gen.get("hardware", []))

    met_goal = bool(endpoints["passed"] and compiles is True
                    and code_judge["pass"] and devices_score["passed"])
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
        "devices": devices_score,
        "met_goal": met_goal,
    }


def run_cases(cases, generate=generate_real, judge=judge_code) -> list:
    return [run_case(c, generate=generate, judge=judge) for c in cases]
