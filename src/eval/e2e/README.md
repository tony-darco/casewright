# End-to-end eval

Does casewright do its job on an access-point prompt? Each case in `cases.yaml` pairs a
**prompt** with its **ground-truth endpoints** and a **reference script**. The runner pushes
the prompt through the real generation pipeline and scores three things:

1. **Endpoints** — precision/recall of the endpoints the pipeline grounded on vs. the
   ground truth. **Recall is the gate** (every expected endpoint must be grounded); extra
   endpoints are tolerated noise.
2. **Code** — an LLM judge (`judge.py`) compares the generated code to the reference + intent
   and returns a 0–1 score + pass/fail. Advisory, not a hard gate on its own.
3. **Runs first try** — two tiers:
   - *offline*: does the code parse/compile for its language (the pipeline's `validate` node).
   - *live* (opt-in): actually run it in Docker against a real Meraki network + claimed AP.

A case **meets the goal** when endpoint recall is perfect, the code compiles, and the judge
passes.

## Run it

Offline (needs Ollama running + an **active knowledge base**, since the pipeline retrieves
against Chroma):

```bash
python -m eval.e2e.run_e2e --cases src/eval/e2e/cases.yaml --out .
```

Prints a table, writes `report.md` + `report.json`, and exits non-zero if any case missed
the goal (so CI can gate on it).

Live tier (real Meraki side effects — needs a configured org, a claimable AP, and Docker):

```bash
AUTOTEST_E2E_LIVE=1 python -m eval.e2e.run_e2e --cases src/eval/e2e/cases.yaml --live
```

It generates through the app's persist path (`generation.start_generate`) and runs each
`needs_ap` case (`run_flow.start_run`), recording first-run pass/fail into `report_live.json`.

## Self-test (no model needed)

`tests/test_e2e_harness.py` exercises the scoring, judge parsing, and per-case
orchestration with stubbed generation/judging:

```bash
pytest tests/test_e2e_harness.py -q
```

## Layout

| File | Role |
| --- | --- |
| `cases.yaml` | golden AP dataset — prompt + `expected_endpoints` + `reference_script` (+ `needs_ap`) |
| `scoring.py` | endpoint precision/recall/F1, recall-gated |
| `judge.py` | LLM-as-judge for the generated code |
| `runner.py` | per-case orchestration (injectable `generate`/`judge` for tests) |
| `report.py` | aggregate summary → markdown + JSON |
| `run_e2e.py` | CLI; offline tier + opt-in live tier |

## Note on ground truth

`expected_endpoints` are the **target** calls the test makes. The pipeline may also ground
on producer endpoints (e.g. `GET /organizations`); those appear as "extra" and don't fail
the recall gate. Org/network/key come from the environment in every reference script — the
generator is told never to hardcode or list them, so those don't appear as targets.
