"""End-to-end eval CLI.

Offline (default): push each case's prompt through the real generation pipeline, score
endpoints, LLM-judge the code, and check it compiles. Prints a markdown table and writes
``report.json`` + ``report.md``.

    python -m eval.e2e.run_e2e --cases src/eval/e2e/cases.yaml

Live tier (opt-in): additionally generate through the app's persist path and actually run
each ``needs_ap`` case in Docker against a real Meraki network, recording first-run
pass/fail. Guarded so it never runs by accident:

    AUTOTEST_E2E_LIVE=1 python -m eval.e2e.run_e2e --cases … --live

Live needs a configured Meraki org + a claimable AP + Docker (see README).
"""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from eval.e2e import report as report_mod
from eval.e2e.runner import run_cases


def load_cases(path: str) -> list:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a YAML list of cases, got {type(data).__name__}")
    return data


# --- live tier ------------------------------------------------------------------------
def run_live(cases: list, source: str) -> list:
    """Generate through the app's persist path and actually run each needs_ap case.

    Reuses the exact app flow: ``generation.start_generate`` (persists code + the
    pipeline-decided hardware) then ``run_flow.start_run`` (ephemeral network + claimed AP
    + Docker). Blocks on each background job by draining ``job.subscribe()`` directly — no
    Textual app needed.
    """
    if os.environ.get("AUTOTEST_E2E_LIVE", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise SystemExit("Refusing to run live: set AUTOTEST_E2E_LIVE=1 to confirm (real Meraki side effects).")

    from tui.bootstrap import startup
    from tui import generation, run_flow
    from web.services import tests_store

    uid = startup()["id"]

    def drain(job):
        last = {}
        for ev in job.subscribe():
            last = ev
        return last

    results = []
    live_cases = [c for c in cases if c.get("needs_ap")]
    for c in live_cases:
        row = {"id": c.get("id", "?"), "prompt": c["prompt"]}
        try:
            job, test_id, _ = generation.start_generate(uid, c["prompt"], c.get("language", "py"))
            done = drain(job)
            if done.get("status") != "done":
                row.update(ran=False, status="generation_failed")
                results.append(row)
                continue
            run, error, run_job = run_flow.start_run(uid, test_id, source, "")
            if error or run_job is None:
                row.update(ran=False, status=f"start_failed: {error or (run and run.get('error_message'))}")
                results.append(row)
                continue
            fin = drain(run_job)
            final = tests_store.get_test(uid, test_id)  # refresh for status if needed
            status = fin.get("status") or (final or {}).get("status") or "unknown"
            row.update(ran=True, status=status, first_run_pass=status in ("success", "passed"))
        except Exception as exc:  # noqa: BLE001 — a live case blowing up shouldn't kill the sweep
            row.update(ran=False, status=f"error: {exc}")
        results.append(row)
    return results


# --- main ------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="End-to-end eval for casewright.")
    ap.add_argument("--cases", default=str(Path(__file__).with_name("cases.yaml")),
                    help="Path to the cases YAML (default: the bundled cases.yaml).")
    ap.add_argument("--out", default=".", help="Directory to write report.json / report.md.")
    ap.add_argument("--live", action="store_true", help="Also run the live Docker/Meraki tier.")
    ap.add_argument("--live-source", default="example", choices=["example", "scratch"],
                    help="How the live tier builds the run network (default: example).")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    print(f"Running {len(cases)} case(s) offline…", file=sys.stderr)
    results = run_cases(cases)
    summary = report_mod.summarize(results)
    md = report_mod.to_markdown(results, summary)
    print(md)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(md + "\n")
    (out / "report.json").write_text(report_mod.to_json(results, summary))
    print(f"\nWrote {out/'report.md'} and {out/'report.json'}", file=sys.stderr)

    if args.live:
        print("\nLive tier (Docker + Meraki)…", file=sys.stderr)
        live = run_live(cases, args.live_source)
        for r in live:
            mark = "✓" if r.get("first_run_pass") else "✗"
            print(f"  {mark} {r['id']}: {r.get('status')}")
        (out / "report_live.json").write_text(json.dumps(live, indent=2))

    # Non-zero exit if any case missed the goal, so CI can gate on it.
    return 0 if summary.get("met_goal", 0) == summary.get("cases", 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
