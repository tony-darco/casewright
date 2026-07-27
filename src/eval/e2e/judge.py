"""LLM-as-judge for the generated code.

Given the prompt, the reference script, and the generated code, ask the chat model for a
strict-JSON verdict: a 0–1 quality score, a pass/fail, and a short reason. It reuses the
same Ollama chat model the pipeline uses (``rag.provider.build_chat_model``) at temperature
0 for repeatability. The judge is advisory — its score is reported, but the objective gates
are endpoints (recall) and does-it-run (compile/live).
"""

import json
import re

_SYSTEM = (
    "You are a strict senior test engineer grading a generated API test against a "
    "reference solution. You are given the user's request, a reference script that is "
    "known-good, and a candidate script produced by a model. Judge whether the candidate "
    "correctly fulfils the request: does it call the right Meraki endpoints, use the same "
    "HTTP methods, read org/network/key from the environment (never hardcoded), and make "
    "meaningful assertions comparable to the reference? Minor stylistic differences are "
    "fine; wrong endpoints, wrong methods, missing assertions, hardcoded ids, or code that "
    "does not address the request are failures.\n"
    "Respond with ONLY a JSON object, no prose, no code fences:\n"
    '{"score": <float 0..1>, "pass": <true|false>, "reasons": "<one or two sentences>"}'
)

_HUMAN = (
    "USER REQUEST:\n{prompt}\n\n"
    "EXPECTED ENDPOINTS (ground truth):\n{expected}\n\n"
    "ENDPOINTS THE MODEL GROUNDED ON:\n{used}\n\n"
    "REFERENCE SCRIPT:\n```\n{reference}\n```\n\n"
    "CANDIDATE SCRIPT:\n```\n{candidate}\n```\n"
)


def _parse(text: str) -> dict:
    """Pull the JSON verdict out of the model's reply, tolerating stray fences/prose."""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return {"score": 0.0, "pass": False, "reasons": f"unparseable judge reply: {text[:200]!r}"}
    try:
        obj = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {"score": 0.0, "pass": False, "reasons": f"non-JSON judge reply: {text[:200]!r}"}
    score = obj.get("score", 0.0)
    try:
        score = max(0.0, min(1.0, float(score)))
    except (ValueError, TypeError):
        score = 0.0
    return {"score": score, "pass": bool(obj.get("pass")), "reasons": str(obj.get("reasons", ""))}


def _default_model():
    from rag.provider import ProviderConfig, build_chat_model
    return build_chat_model(ProviderConfig())


def judge_code(prompt, reference_script, generated_code, endpoints_used,
               expected_endpoints, chat_model=None) -> dict:
    """Score ``generated_code`` against the reference + intent. Returns
    ``{"score": float, "pass": bool, "reasons": str}``."""
    if not (generated_code or "").strip():
        return {"score": 0.0, "pass": False, "reasons": "no code was generated"}
    model = chat_model or _default_model()
    human = _HUMAN.format(
        prompt=prompt,
        expected="\n".join(expected_endpoints) or "(none)",
        used="\n".join(endpoints_used) or "(none)",
        reference=(reference_script or "(none provided)").strip(),
        candidate=generated_code.strip(),
    )
    resp = model.invoke([("system", _SYSTEM), ("human", human)])
    content = resp.content if hasattr(resp, "content") else str(resp)
    return _parse(content)
