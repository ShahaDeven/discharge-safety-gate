"""agentgate core: audit chain, stats, conversation loop, replay cache. Offline."""

import json

from agentgate.audit import AuditLog, verify
from agentgate.conversation import run_call
from agentgate.replay import Candidate, Replayer
from agentgate.simulator import ScriptedSimulator
from agentgate.stats import fisher_exact, wilson
from discharge.tools import DischargeHarness

from conftest import ScriptedAgent, say, tool


# ---------------------------------------------------------------- audit

def test_audit_chain_verifies_and_detects_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(5):
        log.append("call", {"i": i})
    ok, n, _ = verify(path)
    assert ok and n == 5

    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[2])
    entry["payload"]["i"] = 99                      # edit a past entry
    lines[2] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, n, msg = verify(path)
    assert not ok and n == 3 and "changed" in msg


def test_audit_detects_deleted_entry(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    for i in range(4):
        log.append("call", {"i": i})
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, _, msg = verify(path)
    assert not ok and "prev_hash" in msg


def test_audit_resumes_chain_across_instances(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append("a", {})
    AuditLog(path).append("b", {})
    assert verify(path)[0]


# ---------------------------------------------------------------- stats (copied from Margin Guard)

def test_wilson_and_fisher_sanity():
    lo, hi = wilson(8, 10)
    assert 0.44 < lo < 0.5 and 0.94 < hi < 0.97
    assert fisher_exact(5, 0, 0, 5) < 0.01
    assert fisher_exact(3, 2, 3, 2) > 0.99


# ---------------------------------------------------------------- conversation loop

def test_speech_and_tools_are_logged_in_order_and_tool_results_go_back(pilot):
    sc = pilot["P01"]
    agent = ScriptedAgent([
        [say("Hi, this is an automated assistant. May I speak with Robert?")],
        [tool("verify_identity", caller_name="Robert Hayes", relationship="self", patient_name="Robert Hayes",
              patient_date_of_birth="1954-02-11")],
        [say("Thanks. How are you feeling?")],
        [tool("escalate", level="same_day_clinician", reason="swelling", red_flag="ankle swelling"),
         say("A nurse will call you today.")],
        [tool("end_call", summary="done")],
    ])
    sim = ScriptedSimulator("Hello?", ["Yes, speaking. Robert Hayes, February 11 1954.", "Shoes feel snug.",
                                       "Okay, thanks."])
    rec = run_call(agent, sim, DischargeHarness(sc), sc.context("t"), max_agent_turns=10)

    kinds = [(e["kind"], e.get("name") or e["who"]) for e in rec.events]
    assert kinds[0] == ("speech", "party")
    assert ("tool_call", "verify_identity") in kinds
    verify_result = next(e for e in rec.events if e["kind"] == "tool_result" and e["name"] == "verify_identity")
    assert verify_result["text"].startswith("VERIFIED")
    assert agent.results[0][0].content.startswith("VERIFIED")          # result went back to the agent
    esc = kinds.index(("tool_call", "escalate"))
    assert kinds[esc + 2] == ("speech", "agent")                        # call, result, then the speech after it
    assert rec.ended_by == "end_call"


def test_turn_cap_ends_the_call(pilot):
    sc = pilot["P01"]
    agent = ScriptedAgent([[say("Hello?")]] * 20)
    sim = ScriptedSimulator("Hello?", ["Hi."])
    rec = run_call(agent, sim, DischargeHarness(sc), sc.context("t"), max_agent_turns=3)
    assert rec.ended_by == "turn_cap" and rec.agent_turns == 3


def test_party_hang_up_ends_the_call(pilot):
    sc = pilot["P08"]
    agent = ScriptedAgent([[say("Hello, this is an automated assistant.")], [say("Please call us back.")]] * 3)
    sim = ScriptedSimulator("You've reached Frank. [beep]", ["[silence]"], hang_up_after=2)
    rec = run_call(agent, sim, DischargeHarness(sc), sc.context("t"))
    assert rec.ended_by == "party_hung_up"


# ---------------------------------------------------------------- replay cache

class _Suite:
    sim_fingerprint = "sim-v1"

    def __init__(self, scenarios):
        self.by_id = {s.id: s for s in scenarios}

    def build(self, scenario, call_id):
        sc = self.by_id[scenario["id"]]
        return sc.context(call_id), ScriptedSimulator("Hello?", ["Hi."]), DischargeHarness(sc)


def test_replay_caches_and_reruns_only_what_changed(tmp_path, pilot):
    scenarios = [pilot["P01"], pilot["P07"]]
    made = []

    def make():
        made.append(1)
        return ScriptedAgent([[tool("end_call", summary="x")]])

    replayer = Replayer(_Suite(scenarios), tmp_path / "cache", AuditLog(tmp_path / "audit.jsonl"))
    dicts = [s.model_dump() for s in scenarios]
    replayer.run(Candidate("CX", "test", "fp1", make), dicts, k=2, workers=1)
    assert len(made) == 4

    replayer.run(Candidate("CX", "test", "fp1", make), dicts, k=2, workers=1)
    assert len(made) == 4                                   # all cached

    replayer.run(Candidate("CX", "test", "fp1", make), dicts, k=3, workers=1)
    assert len(made) == 6                                   # only the new replay index

    replayer.run(Candidate("CX", "test", "fp2", make), dicts, k=3, workers=1)
    assert len(made) == 12                                  # agent changed: everything reruns

    ok, n, _ = verify(tmp_path / "audit.jsonl")
    assert ok and n == 12
