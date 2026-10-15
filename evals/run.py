"""Run candidates over scenarios and print what happened.

    python -m evals.run pilot --dry-run          # what would run, nothing is called
    python -m evals.run pilot                    # C0 over the 10 pilot scenarios, K=2 (live API calls)
    python -m evals.run pilot --k 1 --only P03   # one scenario, one replay
    python -m evals.run pilot --batch            # same calls via Message Batches (50% off, slower; see agentgate/batch.py)
    python -m evals.run show P03#0               # print a cached transcript with its score
    python -m evals.run audit                    # verify the hash-chained audit log

Cached replays are reused (see agentgate/replay.py), so rerunning costs nothing until a
prompt, model, persona or scenario changes. Transcripts are also written as markdown
to evals/results/transcripts/<candidate>/ for reading.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from agentgate import llm
from agentgate.audit import AuditLog, verify
from agentgate.batch import BatchTransport
from agentgate.replay import Replayer, load_cache
from agentgate.stats import wilson
from discharge import config
from discharge.candidates import candidates
from discharge.persona import DischargeSuite
from discharge.scenario import Scenario, load_scenarios
from discharge.scoring import score

PILOT = config.SCENARIO_DIR / "pilot.yaml"
TRANSCRIPT_DIR = config.RESULTS_DIR / "transcripts"


def render_markdown(sc: Scenario, entry: dict, s: dict) -> str:
    rec = entry["record"]
    lines = [f"# {sc.id} replay {entry['r']}  ({sc.condition}, {sc.jurisdiction}, party: {sc.party.role})", "",
             f"- expected: **{s['disposition_expected']}**, got: **{s['disposition_got']}**"
             f" ({'ok' if s['escalation_ok'] else 'WRONG'}"
             + (", red flags never came up" if s["escalation_ok"] and s["disposition_expected"] in
                ("same_day_clinician", "emergency_now") and not s["escalated_for_reason"] else "") + ")",
             f"- meds: caught {s['med_caught']}, missed {s['med_missed']}, false flags {s['med_false_flags']}"
             + (f", invented by simulator {s['sim_invented_meds']}" if s["sim_invented_meds"] else "")
             + (" (call ended on a 911 instruction before the medicine review)" if s["ended_on_emergency"] else ""),
             f"- violations: {s['violations'] or 'none'}",
             f"- simulator leaks: {s['sim_leaks'] or 'none'}",
             f"- ended by: {rec['ended_by']} after {rec['agent_turns']} agent turns,"
             f" cost ${entry['cost_usd']:.4f}", ""]
    for e in rec["events"]:
        if e["kind"] == "speech":
            who = "AGENT" if e["who"] == "agent" else sc.party.role.upper()
            extra = ""
            if e.get("disclosed"):
                extra = f"  _(revealed {e['disclosed']})_"
            lines.append(f"**{who}:** {e['text']}{extra}")
        elif e["kind"] == "tool_call":
            lines.append(f"> `{e['name']}` {json.dumps(e['input'], ensure_ascii=False)}")
        elif e["kind"] == "tool_result":
            lines.append(f"> -> {e['text']}")
        elif e["kind"] == "error":
            lines.append(f"> ERROR: {e['text']}")
        lines.append("")
    return "\n".join(lines)


def summarize(scenarios: list[Scenario], cache: dict, k: int, cand_id: str) -> None:
    rows, costs, usage = [], [], []
    for sc in scenarios:
        for r in range(k):
            entry = cache.get(f"{sc.id}#{r}")
            if not entry:
                continue
            s = score(sc, entry["record"])
            rows.append((sc, entry, s))
            costs.append(entry["cost_usd"])
            usage.extend(entry["record"]["usage"])
            out = TRANSCRIPT_DIR / cand_id / f"{sc.id}__r{r}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(sc, entry, s), encoding="utf-8")

    if not rows:
        print("no cached replays to summarize")
        return
    print(f"\n{'call':<8} {'expected':<19} {'got':<19} {'esc':<5} {'meds':<14} {'violations':<28} {'leaks':<6} "
          f"{'turns':>5} {'end':<16} {'$':>7}")
    for sc, entry, s in rows:
        meds = (f"{len(s['med_caught'])}/{len(s['med_expected'])}"
                + (f" +{len(s['med_false_flags'])}fp" if s["med_false_flags"] else "")
                + (f" +{len(s['sim_invented_meds'])}sim" if s["sim_invented_meds"] else "")
                + (" (911)" if s["ended_on_emergency"] else ""))
        print(f"{sc.id}#{entry['r']:<5} {s['disposition_expected']:<19} {s['disposition_got']:<19} "
              f"{'ok' if s['escalation_ok'] else 'MISS':<5} {meds:<14} {','.join(s['violations']) or '-':<28} "
              f"{len(s['sim_leaks']):<6} {s['agent_turns']:>5} {s['ended_by']:<16} {entry['cost_usd']:>7.4f}")

    n = len(rows)
    esc_ok = sum(s["escalation_ok"] for _, _, s in rows)
    pos = [s for _, _, s in rows if s["disposition_expected"] in ("same_day_clinician", "emergency_now")]
    under = sum(s["under_triage"] for s in pos)
    med_exp = sum(len(s["med_expected"]) for _, _, s in rows)
    med_hit = sum(len(s["med_caught"]) for _, _, s in rows)
    viol = sum(len(s["violations"]) for _, _, s in rows)
    reviewed = [s for _, _, s in rows if not s["ended_on_emergency"]]
    med_exp_r = sum(len(s["med_expected"]) for s in reviewed)
    med_hit_r = sum(len(s["med_caught"]) for s in reviewed)
    fp = sum(len(s["med_false_flags"]) for _, _, s in rows)
    invented = sum(len(s["sim_invented_meds"]) for _, _, s in rows)
    leaks = sum(len(s["sim_leaks"]) for _, _, s in rows)
    lo, hi = wilson(esc_ok, n)
    for_reason = sum(s["escalated_for_reason"] for s in pos)
    by_role: dict[str, list[float]] = {}
    for e in usage:
        by_role.setdefault(e["role"], []).append(llm.cost_usd([e]))
    read = sum(e.get("cache_read", 0) for e in usage)
    total_in = read + sum(e["input_tokens"] + e.get("cache_write", 0) for e in usage)

    print(f"\ncalls: {n}   escalation correct: {esc_ok}/{n} (95% CI {lo:.0%}-{hi:.0%})   "
          f"under-triage: {under}/{len(pos)} positives   hard-rule violations: {viol}")
    print(f"positives escalated with every red flag uncovered on the call: {for_reason}/{len(pos)}")
    print(f"meds caught: {med_hit}/{med_exp} ({med_hit_r}/{med_exp_r} on calls that reached the medicine review)   "
          f"agent false flags: {fp}   simulator: {invented} invented medicines, {leaks} only-if-asked leaks")
    print(f"cost: total ${sum(costs):.3f}, per call ${sum(costs) / n:.4f} "
          f"(agent ${sum(by_role.get('agent', [])):.3f}, simulator ${sum(by_role.get('simulator', [])):.3f}); "
          f"cache reads {read / total_in:.0%} of input tokens" if total_in else "")
    print(f"transcripts: {TRANSCRIPT_DIR / cand_id}")


def cmd_pilot(args) -> None:
    scenarios = load_scenarios(Path(args.scenarios))
    if args.only:
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    cand = candidates()[args.cand]
    suite = DischargeSuite(scenarios)
    replayer = Replayer(suite, config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                        max_agent_turns=config.MAX_AGENT_TURNS, max_usd=args.max_usd)
    dicts = [s.model_dump() for s in scenarios]
    cache = load_cache(replayer.cache_path(cand.id))
    jobs = replayer.pending(cand, dicts, args.k, cache)
    print(f"{cand.id} ({cand.description}): {len(scenarios)} scenarios x K={args.k}; "
          f"{len(jobs)} calls to run, {len(scenarios) * args.k - len(jobs)} cached")
    print(f"agent {config.AGENT_MODEL} effort={config.AGENT_EFFORT} fallbacks={config.FALLBACKS}; "
          f"simulator {config.SIM_MODEL} effort={config.SIM_EFFORT}; turn cap {config.MAX_AGENT_TURNS}")
    est = len(jobs) * config.EST_COST_PER_CALL.get(config.AGENT_MODEL, 0.025)
    print(f"estimated cost: ${est:.2f}   spending cap: ${args.max_usd:.2f}"
          + ("  (the cap only stops calls that have not started; batch calls all start at once)" if args.batch else ""))
    if args.dry_run:
        return
    started = time.perf_counter()

    def progress(entry: dict) -> None:
        if entry.get("error"):
            print(f"  {entry['scenario_id']}#{entry['r']}: ERROR {entry['error']}", flush=True)
        else:
            rec = entry["record"]
            print(f"  {entry['scenario_id']}#{entry['r']}: {rec['ended_by']} after {rec['agent_turns']} turns, "
                  f"${entry['cost_usd']:.4f}", flush=True)

    transport = BatchTransport(poll_seconds=args.poll) if args.batch else None
    if transport:
        print("batch mode: one Message Batch per conversation step; expect a few minutes per round")
    cache = replayer.run(cand, dicts, args.k, workers=args.workers, on_done=progress, transport=transport)
    print(f"done in {time.perf_counter() - started:.0f}s")
    summarize(scenarios, cache, args.k, cand.id)


def cmd_show(args) -> None:
    sid, _, r = args.call.partition("#")
    cand = candidates()[args.cand]
    scenarios = {s.id: s for s in load_scenarios(Path(args.scenarios))}
    cache = load_cache(config.CACHE_DIR / f"{cand.id}.json")
    entry = cache.get(f"{sid}#{r or 0}")
    if not entry:
        sys.exit(f"no cached replay {args.call} for {cand.id}")
    print(render_markdown(scenarios[sid], entry, score(scenarios[sid], entry["record"])))


def cmd_audit(args) -> None:
    ok, n, msg = verify(config.AUDIT_PATH)
    print(f"{'OK' if ok else 'BROKEN'}: {n} entries, {msg}")
    sys.exit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pilot", help="run a candidate over the pilot scenarios")
    p.add_argument("--cand", default="C0")
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--only", help="comma-separated scenario ids")
    p.add_argument("--scenarios", default=str(PILOT))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-usd", type=float, default=config.MAX_USD, help="stop starting new calls past this spend")
    p.add_argument("--batch", action="store_true", help="send requests through the Message Batches API")
    p.add_argument("--poll", type=int, default=15, help="seconds between batch status checks")
    p.set_defaults(func=cmd_pilot)
    s = sub.add_parser("show", help="print one cached call")
    s.add_argument("call", help="e.g. P03#0")
    s.add_argument("--cand", default="C0")
    s.add_argument("--scenarios", default=str(PILOT))
    s.set_defaults(func=cmd_show)
    a = sub.add_parser("audit", help="verify the audit log")
    a.set_defaults(func=cmd_audit)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
