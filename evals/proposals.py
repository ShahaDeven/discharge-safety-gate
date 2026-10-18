"""Proposed changes: someone edits the agent's prompt (or model, or effort) and the gate decides.

    python -m evals.proposals list
    python -m evals.proposals baseline            # run the current agent on the sealed holdout once (~$0.22)

The console's Propose page calls these functions. A proposal is untrusted input:
- size and format limits on every field (pydantic, unknown fields rejected),
- only allowed models and effort levels,
- it runs as a new candidate id (P-001, ...); built-in candidates are never touched,
- the agent only ever sees the EHR-style call context; answer keys stay with the simulator and the scorer,
- it is judged on the visible scenarios AND on a sealed holdout (discharge/scenarios/holdout.jsonl) that
  the console never shows. A change that passes what it can see but fails what it can't is held: its
  results don't generalize (it may be tuned to the test).
"""

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentgate import llm
from agentgate.audit import AuditLog, content_hash
from agentgate.gate import GATE_VERSION, HOLD, PROMOTE, REJECT, baseline_profile, gate, noise_floor, split_aa
from agentgate.ledger import Ledger
from agentgate.replay import Candidate, Replayer, load_cache
from discharge import clinical_policy, config, judge
from discharge.gate_metrics import SPECS, group
from discharge.guard import manipulation
from discharge.persona import DischargeSuite
from discharge.reference_agent.agent import CONTEXT_VERSION, ReferenceAgent
from discharge.scenario import Scenario, load_scenarios
from discharge.tools import TOOLS
from evals.run import SUITES, evaluate

DIR = config.RESULTS_DIR / "proposals"
HOLDOUT_PATH = config.SCENARIO_DIR / "holdout.jsonl"
BASELINE_HOLDOUT_K = 4
MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5")
EFFORTS = ("low", "medium", "high")

# Quick set: 30 visible scenarios chosen to cover every block and disclosure style, plus 10 sealed ones.
QUICK_VISIBLE = ["HF-01", "HF-04", "HF-07", "HF-10", "HF-13", "COPD-02", "COPD-05", "COPD-08", "COPD-11", "COPD-14",
                 "PNA-03", "PNA-06", "PNA-09", "PNA-12", "PNA-15", "AMI-01", "AMI-04", "AMI-07", "AMI-10", "AMI-16",
                 "CMP-01", "CMP-05", "CMP-09", "CMP-13", "CMP-16", "CMP-19", "PSY-01", "PSY-05", "FAIR-01", "FAIR-06"]
SETS = {
    "quick": {"visible": QUICK_VISIBLE, "holdout": 10, "k": 2},
    "full": {"visible": None, "holdout": 20, "k": 3},
    # Red-team attacks: a few scenarios are enough to show a new kind of violation (one is a rejection).
    "redteam": {"visible": ["HF-01", "COPD-05", "PNA-09", "AMI-04", "CMP-01", "CMP-05", "CMP-09", "CMP-16"],
                "holdout": 6, "k": 1},
    # Teach-to-the-test attack: red flags patients only mention when asked, where shortcuts show.
    "redteam_tt": {"visible": ["HF-09", "HF-10", "HF-11", "HF-12", "COPD-09", "COPD-10", "COPD-11", "COPD-12",
                               "AMI-09", "AMI-10"], "holdout": 10, "k": 2},
}


class ProposalIn(BaseModel):
    """What the Propose page may send. Anything else is rejected."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9 .,'()\-:/]+$")
    note: str = Field(default="", max_length=500)
    prompt: str = Field(min_length=200, max_length=12000)
    model: Literal["claude-haiku-5-5", "claude-sonnet-5-5"] = "claude-haiku-5-5"
    effort: Literal["low", "medium", "high"] = "medium"
    scenario_set: Literal["quick", "full"] = "quick"


def baseline_prompt() -> str:
    return (config.PROMPT_DIR / "c0_baseline.md").read_text(encoding="utf-8")


def prompt_flags(prompt: str) -> list[str]:
    """Manipulation written into the lines a proposal adds (OWASP LLM01). Checked before any call is placed:
    a prompt that tells the agent to work on the evaluator is rejected even if today's model ignores it."""
    base = set(baseline_prompt().splitlines())
    return [f'{why}: "{line.strip()[:120]}"' for line in prompt.splitlines() if line not in base
            for why in manipulation(line)]


def _path(pid: str):
    if not re.fullmatch(r"P-\d{3}", pid):
        raise ValueError("bad proposal id")
    return DIR / f"{pid}.json"


def load(pid: str) -> dict:
    return json.loads(_path(pid).read_text(encoding="utf-8"))


def save(p: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    _path(p["id"]).write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")


def all_proposals() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(DIR.glob("P-*.json"))] if DIR.exists() else []


def create(p: ProposalIn, token_id: str, scenario_set: str | None = None) -> dict:
    """Save a proposal (does not run it)."""
    existing = all_proposals()
    pid = f"P-{len(existing) + 1:03d}"
    rec = {"id": pid, "name": p.name, "note": p.note, "prompt": p.prompt, "model": p.model, "effort": p.effort,
           "scenario_set": scenario_set or p.scenario_set, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "token_id": token_id, "status": "saved", "result": None}
    rec["fingerprint"] = candidate(rec).fingerprint
    rec["prompt_flags"] = prompt_flags(p.prompt)
    save(rec)
    AuditLog(config.AUDIT_PATH).append("proposal", {"id": pid, "name": p.name, "model": p.model, "effort": p.effort,
                                                    "prompt_sha256": content_hash(p.prompt), "token_id": token_id})
    return rec


def candidate(rec: dict) -> Candidate:
    fp = content_hash([rec["prompt"], rec["model"], rec["effort"], config.FALLBACKS, [t.anthropic() for t in TOOLS],
                       CONTEXT_VERSION])
    est = config.EST_COST_PER_CALL.get(rec["model"], 0.025) * (1.5 if rec["effort"] == "high" else 1.0)
    make = partial(ReferenceAgent, rec["prompt"], TOOLS, model=rec["model"], effort=rec["effort"], fallbacks=config.FALLBACKS)
    return Candidate(rec["id"], rec["name"], fp, make, est)


def scenario_sets(name: str) -> tuple[list[Scenario], list[Scenario]]:
    visible_all = load_scenarios(SUITES["suite"])
    ids = SETS[name]["visible"]
    visible = visible_all if ids is None else [s for s in visible_all if s.id in set(ids)]
    holdout = load_scenarios(HOLDOUT_PATH)[: SETS[name]["holdout"]]
    return visible, holdout


def _replayer(scs: list[Scenario], max_usd: float) -> Replayer:
    return Replayer(DischargeSuite(scs), config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                    max_agent_turns=config.MAX_AGENT_TURNS, max_usd=max_usd)


def estimate(rec: dict, check_prompt: bool = True) -> dict:
    """Calls still to run and their cost: the proposal itself, plus the baseline on the holdout if missing.
    A prompt the prompt check flags places no calls."""
    flags = prompt_flags(rec["prompt"]) if check_prompt else []
    if flags:
        return {"calls": 0, "baseline_calls": 0, "visible": 0, "holdout": 0, "k": 0, "usd": 0.0, "blocked": flags}
    visible, holdout = scenario_sets(rec["scenario_set"])
    k = SETS[rec["scenario_set"]]["k"]
    cand = candidate(rec)
    jobs = _replayer(visible + holdout, 99).pending(cand, [s.model_dump() for s in visible + holdout], k,
                                                    load_cache(config.CACHE_DIR / f"{cand.id}.json"))
    base = _baseline_candidate()
    base_jobs = _replayer(holdout, 99).pending(base, [s.model_dump() for s in holdout], BASELINE_HOLDOUT_K,
                                                load_cache(config.CACHE_DIR / "C0.json"))
    judge_cost = (len(jobs) + len(base_jobs)) * 0.0004
    cost = len(jobs) * cand.est_cost_per_call + len(base_jobs) * base.est_cost_per_call + judge_cost
    return {"calls": len(jobs), "baseline_calls": len(base_jobs), "visible": len(visible), "holdout": len(holdout),
            "k": k, "usd": round(cost, 3), "blocked": []}


def _baseline_candidate() -> Candidate:
    from discharge.candidates import candidates
    return candidates()["C0"]


def _judge(rows: list, max_usd: float) -> float:
    verdicts, log = judge.load_cache(), []
    for sc, e, _ in rows:
        if llm.cost_usd(log) >= max_usd:
            break
        if judge.cached(e["record"], verdicts) is None:
            judge.judge(sc, e["record"], verdicts, log=log)
    return llm.cost_usd(log)


def _hide_holdout_ids(reasons: list[str]) -> list[str]:
    return [re.sub(r"H-\d+", "a sealed scenario", r) for r in reasons]


@dataclass(frozen=True)
class Kind:
    """Where a gated candidate's record lives: proposals here, outside agents in evals/agents.py."""
    label: str
    save: Callable[[dict], None]
    audit: str


def run(pid: str, max_usd: float, progress: Callable[[str, int, int, float], None] = lambda *a: None,
        check_prompt: bool = True) -> dict:
    """Run a proposal end to end and return its result (also saved in the proposal file and the ledger).
    check_prompt=False skips the prompt check so the red team can also test the calls themselves."""
    rec = load(pid)
    flags = prompt_flags(rec["prompt"]) if check_prompt else []
    if flags:
        return finish(PROPOSAL, rec, REJECT, [f"[prompt] MANIPULATION: {f}" for f in flags], None,
                      {"verdict": "not run", "scenarios": 0, "reasons": []}, 0.0, 0, {}, progress, blocked=True)
    return gate_candidate(PROPOSAL, rec, candidate(rec), max_usd, progress)


def gate_candidate(kind: Kind, rec: dict, cand: Candidate, max_usd: float,
                   progress: Callable[[str, int, int, float], None], place_calls: bool = True) -> dict:
    """Replay a candidate on rec["scenario_set"] (visible + sealed), judge, gate against C0, record the verdict.
    place_calls=False re-gates from what is cached (a new clinical policy): no calls, no judge, $0."""
    visible, holdout = scenario_sets(rec["scenario_set"])
    k = SETS[rec["scenario_set"]]["k"]
    base = _baseline_candidate()
    spent = 0.0
    rec["status"] = "running"
    kind.save(rec)

    # 1. The current agent on the sealed holdout (once; cached afterwards).
    rb = _replayer(holdout, max_usd)
    jobs = rb.pending(base, [s.model_dump() for s in holdout], BASELINE_HOLDOUT_K, load_cache(rb.cache_path("C0")))
    done, failed = [0], []

    def tick(phase, total, errors=None):
        def on(entry):
            done[0] += 1
            if errors is not None and entry.get("error"):
                errors.append(entry["error"])
            progress(phase, done[0], total, spent + rb.spent + rc.spent)
        return on
    rc = _replayer(visible + holdout, max_usd)
    if jobs and place_calls:
        done[0] = 0
        rb.run(base, [s.model_dump() for s in holdout], BASELINE_HOLDOUT_K, workers=6,
               on_done=tick("current agent on the sealed set", len(jobs)))
    spent += rb.spent

    # 2. The candidate on visible + sealed scenarios. Failed calls are not cached (and not scored).
    rc.max_usd = max(0.0, max_usd - spent)
    jobs = rc.pending(cand, [s.model_dump() for s in visible + holdout], k, load_cache(rc.cache_path(cand.id)))
    done[0] = 0
    if place_calls:
        rc.run(cand, [s.model_dump() for s in visible + holdout], k, workers=6, on_done=tick("calls", len(jobs), failed))
    else:
        failed = ["never completed (re-gated from cache)"] * len(jobs)
    spent += rc.spent
    rows_c = evaluate(cand.id, visible + holdout, k)
    failure = (f"{len(failed)} of {len(jobs)} calls failed and were not scored (first error: {failed[0][:160]}). "
               "Failed calls are not cached: fix the agent and run again; finished calls are reused.") if failed else ""
    if not rows_c:
        return finish(kind, rec, HOLD, [failure or "No call completed."], None,
                      {"verdict": "not run", "scenarios": 0, "reasons": []}, spent, k, {}, progress)

    # 3. Judge the new calls (scope).
    progress("judging", 0, 1, spent)
    rows_b = evaluate("C0", visible, 4) + evaluate("C0", holdout, BASELINE_HOLDOUT_K)
    if place_calls:
        spent += _judge(rows_c + rows_b, max(0.0, max_usd - spent))

    # 4. Gate, separately on what the proposer could see and on the sealed set.
    full_c0 = group(evaluate("C0", load_scenarios(SUITES["suite"]), 4))
    profile = baseline_profile(full_c0, SPECS)
    a, b = split_aa(full_c0)
    floor = noise_floor(gate(a, b, SPECS, profile=profile))
    vis_ids, hold_ids = {s.id for s in visible}, {s.id for s in holdout}
    rows_c = evaluate(cand.id, visible + holdout, k)
    rows_b = evaluate("C0", visible, 4) + evaluate("C0", holdout, BASELINE_HOLDOUT_K)
    pick = lambda rows, ids: group([r for r in rows if r[0].id in ids])
    gv = gate(pick(rows_b, vis_ids), pick(rows_c, vis_ids), SPECS, noise=floor, profile=profile)
    hold_profile = baseline_profile(pick(rows_b, hold_ids), SPECS)
    hold_profile["known_rules"] |= profile["known_rules"]
    gh = gate(pick(rows_b, hold_ids), pick(rows_c, hold_ids), SPECS, noise=floor, profile=hold_profile)

    reasons = [f"[visible] {r}" for r in gv.reasons] + [f"[sealed] {r}" for r in _hide_holdout_ids(gh.reasons)]
    verdict = combine(gv.verdict, gh.verdict, failed=bool(failed))
    if gv.verdict == PROMOTE and gh.verdict != PROMOTE and gh.verdict != REJECT:
        reasons.append("Passes the scenarios it could see but not the sealed ones: results may not generalize "
                       "(the change may be tuned to the visible tests).")
    if failure:
        reasons.append(failure)
    sealed = {"verdict": gh.verdict, "scenarios": gh.scenarios, "reasons": _hide_holdout_ids(gh.reasons)}
    return finish(kind, rec, verdict, reasons, gv.to_dict(), sealed, spent, k, {"C0": 4, cand.id: k}, progress)


def combine(visible: str, sealed: str, failed: bool = False) -> str:
    """Either set rejects -> REJECT; passes what it saw but not the sealed set -> HOLD; any HOLD -> HOLD.
    A run with failed calls is never promoted: what wasn't scored can't be vouched for."""
    if REJECT in (visible, sealed):
        return REJECT
    if HOLD in (visible, sealed) or failed:
        return HOLD
    return PROMOTE


def finish(kind: Kind, rec: dict, verdict: str, reasons: list[str], visible_gate: dict | None, sealed: dict,
           spent: float, k: int, ks: dict, progress: Callable, blocked: bool = False) -> dict:
    result = {"verdict": verdict, "reasons": reasons, "visible": visible_gate, "sealed": sealed, "blocked": blocked,
              "spent_usd": round(spent, 4), "k": k, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec.update(status="done", result=result)
    kind.save(rec)
    pol = clinical_policy.active()
    result["policy"] = pol.to_dict()
    kind.save(rec)
    baseline = ("prompt check (no calls placed)" if blocked else "no calls completed" if visible_gate is None
                else f"C0 (gate v{GATE_VERSION}, visible + sealed, policy {pol.label()})")
    metrics = {m["name"]: [m["base"], m["cand"], m["status"]] for m in visible_gate["metrics"]} if visible_gate else {}
    Ledger(config.RESULTS_DIR / "ledger.json", config.RESULTS_DIR / "ledger.md", AuditLog(config.AUDIT_PATH)).record(
        rec["id"], f"{kind.label}: {rec['name']}", rec["fingerprint"], baseline, verdict, reasons, metrics, ks)
    AuditLog(config.AUDIT_PATH).append(kind.audit, {"id": rec["id"], "verdict": verdict, "spent_usd": round(spent, 4),
                                                    "blocked": blocked})
    progress("done", 1, 1, spent)
    return result


PROPOSAL = Kind("Proposal", save, "proposal_verdict")


def main() -> None:
    cmd = sys.argv[1:] or ["list"]
    if cmd[0] == "list":
        for p in all_proposals():
            print(p["id"], p["status"], p["name"], (p.get("result") or {}).get("verdict", ""))
    elif cmd[0] == "baseline":
        _, holdout = scenario_sets("full")
        r = _replayer(holdout, 0.5)
        base = _baseline_candidate()
        jobs = r.pending(base, [s.model_dump() for s in holdout], BASELINE_HOLDOUT_K, load_cache(r.cache_path("C0")))
        print(f"current agent on the sealed holdout: {len(jobs)} calls to run, about ${len(jobs) * base.est_cost_per_call:.2f}")
        if jobs:
            r.run(base, [s.model_dump() for s in holdout], BASELINE_HOLDOUT_K, workers=6)
            print(f"done, spent ${r.spent:.3f}")


if __name__ == "__main__":
    main()
