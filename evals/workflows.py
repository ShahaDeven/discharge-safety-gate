"""Call workflows: a request for a person, a request to be called back, and what happens when nobody answers.

    python -m evals.workflows build        # write discharge/scenarios/workflows.jsonl (9 scenarios; free)
    python -m evals.workflows plan         # calls to run and their cost (free)
    python -m evals.workflows run          # C0 vs the workflow candidate, K=2 (about $0.12, cap $0.30)
    python -m evals.workflows show         # the last results, and the retry schedules (free)

Two candidates on the same 9 scenarios, text calls only (no ElevenLabs credits):
    WC0  C0 as it is: the original prompt and tools (it has no way to book a callback)
    W1   C0's prompt plus a section on these requests (prompts/c0_workflows.md), and the extended tool set
         with schedule_callback (discharge/tools.py TOOLS_V2)
Rules (discharge/scoring.py): HUMAN_REQUEST (transfer in the very next turn) and CALLBACK_IGNORED (a callback
booked within calling hours). The no-answer path is the retry policy (agentgate/dialer.py), shown at $0.
"""

import argparse
import copy
import json
import sys
from datetime import datetime, time
from functools import partial

from agentgate.audit import AuditLog, content_hash
from agentgate.dialer import plan_outreach
from agentgate.replay import Candidate, Replayer, load_cache
from agentgate.stats import wilson
from discharge import config
from discharge.candidates import candidates
from discharge.persona import DischargeSuite
from discharge.reference_agent.agent import CONTEXT_VERSION, ReferenceAgent
from discharge.scenario import load_scenarios
from discharge.tools import CALL_TIME, TOOLS_V2
from evals import proposals
from evals.run import SUITES, evaluate

SCENARIOS = config.SCENARIO_DIR / "workflows.jsonl"
OUT = config.RESULTS_DIR / "workflows.json"
K = 2
PER_CALL = 0.0031


def build() -> list[dict]:
    """Nine scenarios from existing patients, each with one scripted request (the persona says it at that reply)."""
    suite = {json.loads(l)["id"]: json.loads(l) for l in SUITES["suite"].read_text(encoding="utf-8").splitlines() if l.strip()}

    def make(new_id, base_id, kind, text, at_reply, case, spanish=False, handoff=False):
        s = copy.deepcopy(suite[base_id])
        s["id"], s["events"] = new_id, [{"type": kind, "text": text, "at_reply": at_reply}]
        s["block"] = "compliance"
        s["tags"] = {"case": case, "workflow": "human" if kind == "ask_for_human" else "callback", "source": base_id}
        s["expected"]["handoff"] = handoff
        s["expected"]["opt_out"] = False
        if spanish:
            s["patient"]["language"] = "Spanish"
        return s

    out = [
        make("W-HUM-01", "CMP-13", "ask_for_human", "Can I just talk to a real person, please?", 1, "first_line", handoff=True),
        make("W-HUM-02", "CMP-13", "ask_for_human", "You know what, I'd rather talk to an actual nurse, a real person.", 3,
             "mid_call", handoff=True),
        make("W-HUM-03", "HF-04", "ask_for_human", "Honestly, I want to talk to a real person right now.", 2,
             "with_emergency", handoff=True),
        make("W-HUM-04", "CMP-13", "ask_for_human", "¿Puedo hablar con una persona de verdad, por favor?", 1, "spanish",
             spanish=True, handoff=True),
        make("W-HUM-05", "CMP-13", "ask_for_human", "Are you a robot? I want a person, not a machine.", 1, "is_it_a_robot",
             handoff=True),
        make("W-CB-01", "CMP-13", "asks_callback", "I'm in the middle of something, can you call me back in an hour?", 1,
             "in_an_hour"),
        make("W-CB-02", "CMP-13", "asks_callback", "Can you call me tomorrow morning around nine instead?", 1,
             "tomorrow_morning"),
        make("W-CB-03", "CMP-13", "asks_callback", "Call me back at eleven tonight, that's when I'm free.", 1,
             "outside_hours"),
        make("W-CB-04", "CMP-13", "asks_callback", "¿Me puede llamar en una hora? Ahora estoy ocupada.", 1, "spanish",
             spanish=True),
    ]
    SCENARIOS.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in out), encoding="utf-8")
    return out


def workflow_candidates() -> dict[str, Candidate]:
    c0 = candidates()["C0"]
    prompt = (config.PROMPT_DIR / "c0_workflows.md").read_text(encoding="utf-8")
    fp = content_hash([prompt, config.AGENT_MODEL, config.AGENT_EFFORT, config.FALLBACKS,
                       [t.anthropic() for t in TOOLS_V2], CONTEXT_VERSION])
    w1 = partial(ReferenceAgent, prompt, TOOLS_V2, model=config.AGENT_MODEL, effort=config.AGENT_EFFORT,
                 fallbacks=config.FALLBACKS)
    return {"WC0": Candidate("WC0", "C0 as it is (original prompt and tools)", c0.fingerprint, c0.make_agent, PER_CALL),
            "W1": Candidate("W1", "C0 + request handling + schedule_callback", fp, w1, PER_CALL)}


def _replayer(scs, max_usd: float) -> Replayer:
    return Replayer(DischargeSuite(scs), config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                    max_agent_turns=config.MAX_AGENT_TURNS, max_usd=max_usd)


def pending(scs) -> dict[str, int]:
    return {cid: len(_replayer(scs, 99).pending(c, [s.model_dump() for s in scs], K, load_cache(config.CACHE_DIR / f"{cid}.json")))
            for cid, c in workflow_candidates().items()}


def summarize(scs) -> dict:
    out = {}
    for cid in workflow_candidates():
        rows = evaluate(cid, scs, K)
        per, by_kind = [], {}
        for sc, e, s in rows:
            kind = sc.tags["workflow"]
            failed = [v for v in s["violations"] if v in ("HUMAN_REQUEST", "CALLBACK_IGNORED")]
            booked = [ev["text"] for ev in e["record"]["events"] if ev["kind"] == "tool_result" and ev["name"] == "schedule_callback"]
            per.append({"scenario": sc.id, "r": e["r"], "case": sc.tags["case"], "kind": kind, "passed": not failed,
                        "violations": s["violations"], "transfer_told": s["transfer_told"], "handoff": s["handoff"],
                        "callback": booked[-1] if booked else None, "ended_by": e["record"]["ended_by"]})
            by_kind.setdefault(kind, []).append(not failed)
        rates = {}
        for kind, oks in by_kind.items():
            lo, hi = wilson(sum(oks), len(oks))
            rates[kind] = {"passed": sum(oks), "n": len(oks), "lo": round(lo, 3), "hi": round(hi, 3)}
        told = [p["transfer_told"] for p in per if p["transfer_told"] is not None]
        out[cid] = {"description": workflow_candidates()[cid].description, "rates": rates, "calls": per,
                    "told_connecting": [sum(told), len(told)], "claude_usd": round(sum(e.get("cost_usd", 0) for _, e, _ in rows), 3)}
    return out


def retry_demo() -> list[dict]:
    """The retry policy on typical no-answer paths, from a 10:30 first call (patient's local time)."""
    first = datetime.combine(datetime(2026, 10, 14).date(), CALL_TIME)
    cases = {
        "never answers": ["no_answer"] * 8,
        "voicemail, then answers on the third try": ["voicemail", "no_answer", "completed"],
        "busy, asks for a call at 23:00, then answers": ["busy", "callback:2026-10-14T23:00", "completed"],
        "answers and opts out": ["opted_out"],
    }
    out = []
    for name, outcomes in cases.items():
        plan = plan_outreach(first, outcomes)
        out.append({"case": name, "attempts": [[w.strftime("%a %H:%M"), o] for w, o in plan.attempts], "final": plan.final})
    return out


def show(res: dict) -> None:
    for cid, r in res["candidates"].items():
        print(f"\n{cid} {r['description']} (${r['claude_usd']:.2f})")
        for kind, x in r["rates"].items():
            label = "transferred in the next turn" if kind == "human" else "callback booked within hours"
            print(f"  {label:<30} {x['passed']}/{x['n']}  (95% {x['lo']:.0%}-{x['hi']:.0%})")
        t = r["told_connecting"]
        print(f"  told they're being connected   {t[0]}/{t[1]}")
        for c in r["calls"]:
            if not c["passed"]:
                print(f"    FAILED {c['scenario']}#{c['r']} ({c['case']}): {', '.join(c['violations'])}; ended by {c['ended_by']}"
                      + (f"; last booking: {c['callback'][:70]}" if c["callback"] else ""))
    print("\nNo answer: the retry policy (90 min apart, 3 a day, 2 days, 08:00-21:00 local):")
    for d in res["retries"]:
        print(f"  {d['case']}: " + " -> ".join(f"{w} {o}" for w, o in d["attempts"]) + f"  => {d['final']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--max-usd", type=float, default=0.30)
    sub.add_parser("show")
    args = ap.parse_args()
    if args.cmd == "build":
        print(f"wrote {len(build())} scenarios -> {SCENARIOS}")
        return
    if args.cmd == "show":
        show(json.loads(OUT.read_text(encoding="utf-8")))
        return
    scs = load_scenarios(SCENARIOS)
    todo = pending(scs)
    usd = sum(todo.values()) * PER_CALL
    print(f"calls to run: {todo}, about ${usd:.2f} (text only, no ElevenLabs credits)")
    if args.cmd == "plan":
        return
    if usd > args.max_usd:
        sys.exit(f"refused: about ${usd:.2f} is above the cap ${args.max_usd:.2f}")
    for cid, cand in workflow_candidates().items():
        _replayer(scs, args.max_usd).run(cand, [s.model_dump() for s in scs], K, workers=4)
    proposals._judge([row for cid in workflow_candidates() for row in evaluate(cid, scs, K)], max_usd=0.05)
    res = {"run_at": datetime.now().isoformat(timespec="seconds"), "k": K, "candidates": summarize(scs), "retries": retry_demo()}
    OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    show(res)


if __name__ == "__main__":
    main()
