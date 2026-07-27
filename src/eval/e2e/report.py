"""Turn scored results into an aggregate summary + human/machine reports."""

import json


def summarize(results: list) -> dict:
    """Aggregate metrics across all cases."""
    n = len(results)
    if not n:
        return {"cases": 0}
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    device_cases = [r for r in results if r.get("devices", {}).get("applicable")]
    return {
        "cases": n,
        "met_goal": sum(r["met_goal"] for r in results),
        "endpoint_recall_passed": sum(r["endpoints"]["passed"] for r in results),
        "compiled": sum(r["compiles"] is True for r in results),
        "code_judge_passed": sum(r["code_judge"]["pass"] for r in results),
        "device_cases": len(device_cases),
        "device_passed": sum(r["devices"]["passed"] for r in device_cases),
        "mean_recall": mean([r["endpoints"]["recall"] for r in results]),
        "mean_precision": mean([r["endpoints"]["precision"] for r in results]),
        "mean_f1": mean([r["endpoints"]["f1"] for r in results]),
        "mean_code_score": mean([r["code_judge"]["score"] for r in results]),
    }


def _tick(b) -> str:
    return "✓" if b is True else ("·" if b is None else "✗")


def to_markdown(results: list, summary: dict) -> str:
    """A readable table: one row per case + an aggregate line."""
    lines = [
        "| Case | Endpoints (R/P) | Compiles | Code judge | Device | Goal |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        ep = r["endpoints"]
        dev = r.get("devices", {})
        dev_cell = "·" if not dev.get("applicable") else _tick(dev.get("passed"))
        lines.append(
            f"| {r['id']} | {_tick(ep['passed'])} {ep['recall']:.2f}/{ep['precision']:.2f} "
            f"| {_tick(r['compiles'])} | {_tick(r['code_judge']['pass'])} {r['code_judge']['score']:.2f} "
            f"| {dev_cell} | {_tick(r['met_goal'])} |"
        )
    s = summary
    lines += [
        "",
        f"**{s.get('met_goal', 0)}/{s.get('cases', 0)} met the goal** "
        f"(endpoints+compile+judge+device all pass).",
        f"- endpoint recall passed: {s.get('endpoint_recall_passed', 0)}/{s.get('cases', 0)} "
        f"· mean recall {s.get('mean_recall', 0):.2f} · mean precision {s.get('mean_precision', 0):.2f}",
        f"- compiled first try: {s.get('compiled', 0)}/{s.get('cases', 0)}",
        f"- code judge passed: {s.get('code_judge_passed', 0)}/{s.get('cases', 0)} "
        f"· mean score {s.get('mean_code_score', 0):.2f}",
        f"- device added correctly: {s.get('device_passed', 0)}/{s.get('device_cases', 0)} "
        "(device-adding cases only)",
    ]
    return "\n".join(lines)


def to_json(results: list, summary: dict) -> str:
    # Drop the (potentially large) generated code from the machine report's top level;
    # keep everything else. Full code stays available in ``results`` if a caller wants it.
    trimmed = []
    for r in results:
        row = dict(r)
        row["code_lines"] = len((row.pop("code", "") or "").splitlines())
        trimmed.append(row)
    return json.dumps({"summary": summary, "results": trimmed}, indent=2)
