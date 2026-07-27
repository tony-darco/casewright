"""Self-tests for the end-to-end eval harness.

These verify the harness machinery (endpoint scoring, judge-JSON parsing, per-case
orchestration, aggregation) with **stubbed** generation and judging — no Ollama, no
Chroma, no Docker — so the scorer is trusted before it grades real runs.
"""

from eval.e2e import report
from eval.e2e.judge import _parse
from eval.e2e.runner import run_case, run_cases
from eval.e2e.scoring import device_markers, score_devices, score_endpoints


def test_score_endpoints_recall_gate_and_normalization():
    s = score_endpoints(
        retrieved=["get /networks/{networkId}/wireless/ssids/{number}",
                   "put /networks/{networkId}/wireless/ssids/{number}",
                   "GET /organizations"],                      # extra -> precision noise
        expected=["GET /networks/{networkId}/wireless/ssids/{number}",
                  "PUT /networks/{networkId}/wireless/ssids/{number}"])
    assert s["recall"] == 1.0 and s["passed"] is True          # every expected was grounded
    assert s["missing"] == []
    assert s["extra"] == ["GET /organizations"]
    assert 0.6 < s["precision"] < 0.7                          # 2/3


def test_score_endpoints_missing_fails_gate():
    s = score_endpoints(retrieved=["GET /organizations"],
                        expected=["PUT /networks/{networkId}/wireless/ssids/{number}"])
    assert s["recall"] == 0.0 and s["passed"] is False
    assert s["missing"] == ["PUT /networks/{networkId}/wireless/ssids/{number}"]


def test_judge_parse_tolerates_fences_and_prose():
    assert _parse('```json\n{"score": 0.9, "pass": true, "reasons": "ok"}\n```')["pass"] is True
    assert _parse('here you go: {"score": 2, "pass": true, "reasons": "x"}')["score"] == 1.0  # clamped
    bad = _parse("not json at all")
    assert bad["pass"] is False and bad["score"] == 0.0


def _stub_generate(code, endpoints, ok, hardware=None):
    return lambda prompt, language="py", devices=None: {
        "code": code, "endpoints_used": endpoints, "validation": {"ok": ok, "detail": "stub"},
        "hardware": hardware or []}


def _stub_judge(passed, score=1.0):
    return lambda *a, **k: {"score": score, "pass": passed, "reasons": "stub"}


def test_run_case_meets_goal_when_all_three_pass():
    case = {"id": "c1", "prompt": "enable ssid", "language": "py",
            "expected_endpoints": ["PUT /networks/{networkId}/wireless/ssids/{number}"],
            "reference_script": "ref"}
    r = run_case(case,
                 generate=_stub_generate("def test(): assert True",
                                         ["PUT /networks/{networkId}/wireless/ssids/{number}"], True),
                 judge=_stub_judge(True))
    assert r["endpoints"]["passed"] and r["compiles"] is True and r["code_judge"]["pass"]
    assert r["met_goal"] is True


def test_run_case_fails_goal_on_any_miss():
    case = {"id": "c2", "prompt": "x", "expected_endpoints": ["GET /a"], "reference_script": ""}
    # right endpoints + compiles, but the judge fails -> goal not met
    r = run_case(case, generate=_stub_generate("code", ["GET /a"], True), judge=_stub_judge(False))
    assert r["met_goal"] is False
    # wrong endpoints -> recall gate fails even if it compiles and the judge passes
    r2 = run_case(case, generate=_stub_generate("code", ["GET /b"], True), judge=_stub_judge(True))
    assert r2["endpoints"]["passed"] is False and r2["met_goal"] is False


def test_device_markers_serial_vs_token():
    devs = [{"name": "a", "serial": "Q2PD-1111-AAAA"}, {"name": "b"}]  # b is type-only
    assert device_markers(devs) == ["Q2PD-1111-AAAA", "{{DEVICE_SERIAL_2}}"]
    assert device_markers([]) == []


def test_score_devices_needs_marker_in_code_and_pinned_hardware():
    code = 'SERIAL = "Q2PD-1111-AAAA"\n'
    hw = [{"serial": "Q2PD-1111-AAAA", "model": "MR46"}]
    assert score_devices(code, ["Q2PD-1111-AAAA"], hw)["passed"] is True
    # serial present but nothing pinned -> not added for the run
    assert score_devices(code, ["Q2PD-1111-AAAA"], [])["passed"] is False
    # pinned but the model never wrote the serial -> not in the test
    s = score_devices("no serial here", ["Q2PD-1111-AAAA"], hw)
    assert s["passed"] is False and s["missing"] == ["Q2PD-1111-AAAA"]
    # no declared devices -> not applicable, passes trivially
    assert score_devices("x", [], [])["applicable"] is False


def test_run_case_device_path_gates_on_device_added():
    case = {"id": "d1", "prompt": "test @lobby-ap", "language": "py",
            "expected_endpoints": ["GET /devices/{serial}/wireless/status"],
            "reference_script": "ref",
            "devices": [{"name": "lobby-ap", "model": "MR46", "serial": "Q2PD-1111-AAAA"}]}
    good = run_case(
        case,
        generate=_stub_generate('SERIAL="Q2PD-1111-AAAA"\ndef test(): assert True',
                                ["GET /devices/{serial}/wireless/status"], True,
                                hardware=[{"serial": "Q2PD-1111-AAAA"}]),
        judge=_stub_judge(True))
    assert good["devices"]["applicable"] and good["devices"]["passed"] and good["met_goal"]
    # same case, but the model fabricated/omitted the serial -> device fails -> goal fails
    bad = run_case(
        case,
        generate=_stub_generate("def test(): assert True",
                                ["GET /devices/{serial}/wireless/status"], True,
                                hardware=[{"serial": "Q2PD-1111-AAAA"}]),
        judge=_stub_judge(True))
    assert bad["devices"]["passed"] is False and bad["met_goal"] is False


def test_summary_aggregates():
    cases = [
        {"id": "a", "prompt": "p", "expected_endpoints": ["GET /a"], "reference_script": ""},
        {"id": "b", "prompt": "p", "expected_endpoints": ["GET /b"], "reference_script": ""},
    ]
    results = run_cases(
        cases,
        generate=_stub_generate("code", ["GET /a"], True),   # matches a, not b
        judge=_stub_judge(True))
    summary = report.summarize(results)
    assert summary["cases"] == 2
    assert summary["met_goal"] == 1                            # only case a
    assert "| Case |" in report.to_markdown(results, summary)
