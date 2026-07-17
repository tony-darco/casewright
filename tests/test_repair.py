"""Repair pass: send a failed run back through the pipeline to fix the code.

Covers the two things that make repair safe rather than destructive:
- it reuses the endpoints the first pass grounded on (no retrieval, so no drift), and
- it never touches the run config — a code fix must not re-decide pinned hardware.

Plus the honesty of the evidence handed to the model: an infra failure (Docker down)
must be reported as "the code never ran", not dressed up as a test failure.
"""

from unittest import mock

from web import db
from web.services import generate, tests_store


# --- the evidence a repair sends -------------------------------------------------

def _logs(*triples):
    return [{"stage": s, "level": l, "message": m} for s, l, m in triples]


def test_repair_context_prefers_the_containers_own_output():
    """When the test actually ran, provision noise is dropped — the pytest output is
    the whole story, and burying it in scaffolding wastes the model's attention."""
    ctx = generate.repair_context(
        {"code": "def test_x(): assert False"},
        {"status": "failed", "error_message": ""},
        _logs(("provision", "info", "cloning example network"),
              ("run", "info", "E   assert False"),
              ("teardown", "info", "deleted network L_1")),
    )
    assert ctx["stage"] == "run"
    assert ctx["output"] == "E   assert False"       # no [provision]/[teardown] noise
    assert ctx["code"] == "def test_x(): assert False"
    assert ctx["status"] == "failed"


def test_repair_context_marks_an_infra_failure_as_never_run():
    """Docker down: nothing came from the container, so the code never executed. The
    stage must say so — prompts.repair_system uses it to tell the model to leave correct
    code alone instead of inventing a fix for a daemon outage."""
    ctx = generate.repair_context(
        {"code": "def test_x(): pass"},
        {"status": "error", "error_message": "Could not connect to the Docker daemon"},
        _logs(("provision", "info", "claimed MR16 Q2DD-S2Z2-RLKP"),
              ("run", "error", "")),          # blank message: no real container output
    )
    assert ctx["stage"] == "provision"        # i.e. "the test never ran"
    assert "[provision]" in ctx["output"]     # infra failures keep their stage tags
    assert "Docker daemon" in ctx["output"]   # the run's error is folded in


def test_repair_context_trims_a_huge_log_but_keeps_the_tail():
    """The error is at the end of a failing run, so the tail is what must survive."""
    noise = _logs(*[("run", "info", "x" * 200) for _ in range(100)])
    ctx = generate.repair_context({"code": "c"}, {"status": "failed"},
                                  noise + _logs(("run", "info", "THE ACTUAL ERROR")))
    assert "THE ACTUAL ERROR" in ctx["output"]
    assert "trimmed" in ctx["output"]
    assert len(ctx["output"]) < 8000


def test_repair_context_survives_a_run_with_no_logs():
    ctx = generate.repair_context({"code": "c"}, {"status": "error", "error_message": "boom"}, [])
    assert ctx["code"] == "c" and "boom" in ctx["output"]


# --- the prompt handed to the model ----------------------------------------------

def test_repair_prompt_carries_code_and_output():
    from rag.graph import prompts
    body = prompts.user_generate("do a thing", "GET /x", "", {
        "code": "CODE_HERE", "status": "failed", "stage": "run", "output": "TRACEBACK_HERE"})
    assert "CODE_HERE" in body and "TRACEBACK_HERE" in body
    assert "executed and exited non-zero" in body


def test_repair_prompt_says_when_the_code_never_ran():
    from rag.graph import prompts
    body = prompts.user_generate("do a thing", "GET /x", "", {
        "code": "CODE", "status": "error", "stage": "provision", "output": "docker down"})
    assert "never reached the test" in body


def test_repair_system_prompt_licenses_no_change():
    """The model must be allowed to say "the code is fine" rather than fabricate an edit."""
    from rag.graph import prompts
    sys_prompt = prompts.repair_system("Python", "a pytest module")
    assert "return it" in sys_prompt and "exactly as given" in sys_prompt


# --- runtime ids come from the environment; discovery is forbidden ---------------
# Observed failure: a test grounded on GET /devices/{serial}/... needs a network ID, but
# its dependency chain lists GET /organizations -> GET /organizations/{organizationId}/
# networks -> GET /networks/{networkId}/devices as "call first" steps for those ids. The
# model followed that chain and wrote a fixture that lists networks and filters for one
# whose *name* contains the device's *model* ("MR42") -- a network that doesn't exist,
# since networks are user-named ("Tony_home"), not named after the hardware inside them.
# The org id was also baked in as an int, so `org["id"] == ORGANIZATION_ID` never matched
# a string id. The fix: the org id, network id, and API key are supplied through
# environment variables (the network is a *fresh* per-run network, so it can't be a
# literal); the model reads them and never lists/filters to discover or verify them.

def test_generate_system_directs_supplied_ids_to_env_and_forbids_discovery():
    from rag.graph import prompts
    sys_prompt = prompts.generate_system("Python", "a pytest module")
    assert "MERAKI_ORG_ID" in sys_prompt and "MERAKI_NETWORK_ID" in sys_prompt
    assert "list, search, or filter organizations or networks" in sys_prompt


def test_repair_system_flags_a_discovery_lookup_as_the_bug_to_fix():
    from rag.graph import prompts
    sys_prompt = prompts.repair_system("Python", "a pytest module")
    assert "lists/filters organizations or networks to discover or verify" in sys_prompt
    assert "read of the environment variable" in sys_prompt


def test_dependency_block_is_qualified_as_a_fallback():
    """The dependency chain reads as an unconditional directive on its own ("call
    producers before consumers") -- the qualifier has to sit right at that instruction,
    not rely on the system prompt alone, since that's exactly what was ignored."""
    from rag.graph import prompts
    body = prompts.user_generate("q", "GET /x", "1. GET /organizations\n2. GET /networks")
    assert "NOT already given above as a concrete value" in body
    assert "skip any step here that would rediscover one you already have" in body


def test_env_value_guidance_precedes_the_dependency_block_in_the_composed_prompt():
    """The "already given above" wording is only true if the runtime-value guidance
    actually lands earlier in the message than the dependency block -- verify the real
    assembly, not just each piece in isolation."""
    from web.services.generate import _full_prompt
    question = _full_prompt(
        "radio test on @AP2",
        [{"name": "AP2", "serial": "Q2KD-DEMR-82P7", "model": "MR42", "mac": ""}],
        {"base_url": None, "org_id": "1628211", "network_ids": ["N_ephemeral"]},
    )
    from rag.graph import prompts as p
    body = p.user_generate(question, "GET /x", "1. GET /organizations\n2. GET /networks")
    assert body.index("MERAKI_NETWORK_ID") < body.index("Call-order dependencies")


# --- graph routing ---------------------------------------------------------------

def _pipeline():
    from rag.pipeline import AutoTestLLM
    with mock.patch("rag.pipeline.build_chat_model"), mock.patch("rag.pipeline.build_vector_store"):
        return AutoTestLLM()


def test_repair_inputs_seed_endpoints_and_skip_retrieval():
    p = _pipeline()
    repair = {"code": "c", "status": "failed", "stage": "run", "output": "o"}
    inputs = p._inputs("q", "python", repair, ["GET /a", "GET /b"])
    assert inputs["repair"] == repair
    assert inputs["endpoints"] == ["GET /a", "GET /b"]


def test_normal_inputs_carry_no_repair_state():
    p = _pipeline()
    inputs = p._inputs("q", "python")
    assert "repair" not in inputs and "endpoints" not in inputs


def test_graph_has_a_repair_entry():
    p = _pipeline()
    nodes = set(p.graph.get_graph().nodes)
    assert "repair_context" in nodes


def test_docs_for_endpoints_preserves_order_and_drops_misses():
    p = _pipeline()
    p.vector_store = mock.Mock()
    p.vector_store.get.return_value = {
        "documents": ['{"summary": "b"}', '{"summary": "a"}'],
        "metadatas": [{"endpoint_id": "GET /b"}, {"endpoint_id": "GET /a"}],
    }
    docs = p.docs_for_endpoints(["GET /a", "GET /b", "GET /gone"])
    assert [d.metadata["endpoint_id"] for d in docs] == ["GET /a", "GET /b"]


def test_docs_for_endpoints_degrades_to_empty_when_the_store_fails():
    """No context is better than generating blind against nothing."""
    p = _pipeline()
    p.vector_store = mock.Mock()
    p.vector_store.get.side_effect = RuntimeError("chroma down")
    assert p.docs_for_endpoints(["GET /a"]) == []


# --- a failed repair must not destroy the test -----------------------------------

def test_abandon_generation_keeps_the_code_and_versions():
    db.init()
    u = db.create_user("repairkeep", "h")
    t = tests_store.create_test(u["id"], "n", "p", "f.py", "GOOD CODE", "py", ["GET /x"], [])
    tests_store.add_version(t["id"], "p", "f.py", "GOOD CODE", "py", ["GET /x"], None)
    tests_store.restart_generation(u["id"], t["id"], "p", "py")

    tests_store.abandon_generation(u["id"], t["id"])

    row = tests_store.get_test(u["id"], t["id"])
    assert row is not None, "a failed repair must never delete the test"
    assert row["code"] == "GOOD CODE"          # prior code intact
    assert row["status"] == "done"             # not stuck in 'generating'
    assert len(tests_store.list_versions(u["id"], t["id"])) == 1


# --- end to end through the graph (LLM + store mocked) ----------------------------

def test_repair_run_skips_retrieval_and_reuses_stored_endpoints():
    """The whole point of tracking everything: a repair grounds on what the first pass
    already retrieved, so it can't quietly drift onto different endpoints."""
    p = _pipeline()
    p.vector_store = mock.Mock()
    p.vector_store.get.return_value = {
        "documents": ['{"summary": "ssid list", "parameters": []}'],
        "metadatas": [{"endpoint_id": "GET /networks/{networkId}/wireless/ssids"}],
    }
    p.vector_store.similarity_search.side_effect = AssertionError("retrieval must not run on a repair")
    p.chat.invoke.return_value = mock.Mock(content="def test_fixed(): pass")

    state = p.graph.invoke(p._inputs(
        "list the ssids", "python",
        {"code": "def test_broken(): assert False", "status": "failed",
         "stage": "run", "output": "E assert False"},
        ["GET /networks/{networkId}/wireless/ssids"],
    ))

    assert state["endpoints"] == ["GET /networks/{networkId}/wireless/ssids"]
    assert "test_fixed" in state["tests"]
    p.vector_store.similarity_search.assert_not_called()
    # the failed code + its output reached the model
    sent = p.chat.invoke.call_args[0][0][-1].content
    assert "test_broken" in sent and "E assert False" in sent


def test_repair_does_not_redecide_hardware():
    """A code fix must not touch the run config — re-deciding would drop pinned devices."""
    p = _pipeline()
    p.vector_store = mock.Mock()
    p.vector_store.get.return_value = {
        "documents": ['{"summary": "s", "parameters": []}'],
        "metadatas": [{"endpoint_id": "GET /x"}],
    }
    p.chat.invoke.return_value = mock.Mock(content="def test_fixed(): pass")

    state = p.graph.invoke(p._inputs(
        "q", "python",
        {"code": "c", "status": "failed", "stage": "run", "output": "o"}, ["GET /x"]))

    assert "hardware" not in state, "the hardware node must be skipped on a repair"


def test_stream_events_keeps_hardware_unchanged_on_repair():
    """generate.stream_events must not re-pin hardware when repairing; the caller keeps
    the test's existing rows verbatim."""
    fake = mock.Mock()
    fake.stream_run.return_value = iter([("final", {
        "endpoints": ["GET /x"], "tests": "def test_fixed(): pass", "validation": None,
    })])
    with mock.patch.object(generate, "get_pipeline", return_value=(fake, None)):
        evs = list(generate.stream_events(
            "p", [{"serial": "Q2-A", "model": "MR16", "name": "AP"}], "py", {}, None,
            repair={"code": "c", "status": "failed", "stage": "run", "output": "o"},
            endpoints=["GET /x"]))
    vm = evs[-1]["vm"]
    assert vm["hardware"] == [], "repair must not invent hardware; the caller restores it"
    # and the repair reached the pipeline with its endpoints
    assert fake.stream_run.call_args.kwargs["endpoints"] == ["GET /x"]
    assert fake.stream_run.call_args.kwargs["repair"]["status"] == "failed"
