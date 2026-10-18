"""Run candidates over scenarios, judge them, and print what happened.

    python -m evals.run run --cand C0 --dry-run              # what would run and its estimated cost
    python -m evals.run run --cand C0 --k 1                  # C0 over the ~100-scenario suite, one replay each
    python -m evals.run run --cand C0 --block compliance     # one block (clinical|compliance|psych|fairness)
    python -m evals.run run --cand C0 --only HF-03,CMP-07    # chosen scenarios
    python -m evals.run pilot                                # the 10 hand-written pilot scenarios, K=2
    python -m evals.run judge --cand C0                      # scope / no-therapy judge over cached calls
    python -m evals.run summary --cand C0                    # re-print the summary from the cache (free)
    python -m evals.run show HF-03#0 --cand C0               # one call with its score
    python -m evals.run audit                                # verify the hash-chained audit log

Every command that calls the API prints an estimate first and stops at --max-usd.
Cached replays and verdicts are reused, so rerunning costs nothing until a prompt,
model, persona or scenario changes. Transcripts are written as markdown to
evals/results/transcripts/<candidate>/.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentgate import llm
from agentgate.audit import AuditLog, verify
from agentgate.batch import BatchTransport
from agentgate.replay import Replayer, load_cache
from agentgate.stats import wilson
from discharge import config, judge
from discharge.candidates import candidates
from discharge.persona import DischargeSuite
from discharge.scenario import Scenario, load_scenarios
from discharge.scoring import score

SUITES = {"suite": config.SCENARIO_DIR / "suite.jsonl", "pilot": config.SCENARIO_DIR / "pilot.yaml"}
TRANSCRIPT_DIR = config.RESULTS_DIR / "transcripts"
EST_JUDGE_COST = {"claude-haiku-5-5": 0.0006, "claude-sonnet-5-5": 0.008}
POSITIVE = ("same_day_clinician", "emergency_now")


# ---------------------------------------------------------------- loading and scoring

def select(args) -> list[Scenario]:
    scenarios = load_scenarios(SUITES[args.suite])
    if getattr(args, "only", None):
        wanted = set(args.only.split(","))
        scenarios = [s for s in scenarios if s.id in wanted]
    if getattr(args, "block", None):
        scenarios = [s for s in scenarios if s.block == args.block]
    return scenarios


def evaluate(cand_id: str, scenarios: list[Scenario], k: int, pol=None) -> list[tuple[Scenario, dict, dict]]:
    """(scenario, cached entry, score) for every cached replay r < k, with judge labels when cached,
    under a clinical policy (default: the active one)."""
    cache = load_cache(config.CACHE_DIR / f"{cand_id}.json")
    verdicts = judge.load_cache()
    rows = []
    for sc in scenarios:
        for r in range(k):
            entry = cache.get(f"{sc.id}#{r}")
            if entry:
                rows.append((sc, entry, score(sc, entry["record"], judge.cached(entry["record"], verdicts), pol)))
    return rows


# ---------------------------------------------------------------- output

def render_markdown(sc: Scenario, entry: dict, s: dict) -> str:
    rec = entry["record"]
    reason = (", red flags never came up" if s["escalation_ok"] and s["disposition_expected"] in POSITIVE
              and not s["escalated_for_reason"] else "")
    lines = [f"# {sc.id} replay {entry['r']}  ({sc.block}, {sc.condition}, {sc.jurisdiction}, party: {sc.party.role})",
             "", f"- grid cell: {sc.tags or '-'}",
             f"- expected: **{s['disposition_expected']}**, got: **{s['disposition_got']}**"
             f" ({'ok' if s['escalation_ok'] else 'WRONG'}{reason})",
             f"- meds: caught {s['med_caught']}, missed {s['med_missed']}, false flags {s['med_false_flags']}"
             + (f", invented by simulator {s['sim_invented_meds']}" if s["sim_invented_meds"] else "")
             + (" (call ended on a 911 instruction before the medicine review)" if s["ended_on_emergency"] else ""),
             f"- violations: {s['violations'] or 'none'}" + ("" if s["judged"] else " (scope not judged yet)"),
             *[f"  - {v['rule']}: \"{v['text'][:200]}\"" for v in s["violation_detail"] if v["text"]],
             f"- checklist coverage: {s['coverage']}   simulator leaks: {s['sim_leaks'] or 'none'}",
             f"- ended by: {rec['ended_by']} after {rec['agent_turns']} agent turns, cost ${entry['cost_usd']:.4f}", ""]
    for e in rec["events"]:
        if e["kind"] == "speech":
            who = "AGENT" if e["who"] == "agent" else sc.party.role.upper()
            extra = f"  _(revealed {e['disclosed']})_" if e.get("disclosed") else ""
            lines.append(f"**{who}:** {e['text']}{extra}")
        elif e["kind"] == "tool_call":
            lines.append(f"> `{e['name']}` {json.dumps(e['input'], ensure_ascii=False)}")
        elif e["kind"] == "tool_result":
            lines.append(f"> -> {e['text']}")
        elif e["kind"] == "error":
            lines.append(f"> ERROR: {e['text']}")
        lines.append("")
    return "\n".join(lines)


def _rate(k: int, n: int) -> str:
    if not n:
        return "  -  "
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({k / n:.0%}, CI {lo:.0%}-{hi:.0%})"


def summarize(cand_id: str, rows: list, table: bool = False) -> None:
    if not rows:
        print("no cached calls to summarize")
        return
    for sc, entry, s in rows:
        out = TRANSCRIPT_DIR / cand_id / f"{sc.id}__r{entry['r']}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(sc, entry, s), encoding="utf-8")

    if table or len(rows) <= 30:
        print(f"\n{'call':<11} {'expected':<19} {'got':<19} {'esc':<6} {'meds':<12} {'violations':<30} {'cov':>4} "
              f"{'turns':>5}  {'$':>7}")
        for sc, entry, s in rows:
            meds = (f"{len(s['med_caught'])}/{len(s['med_expected'])}"
                    + (f" +{len(s['med_false_flags'])}fp" if s["med_false_flags"] else "")
                    + (" (911)" if s["ended_on_emergency"] else ""))
            esc = ("ok" if s["escalated_for_reason"] or s["disposition_expected"] not in POSITIVE else "ok*") \
                if s["escalation_ok"] else "MISS"
            cov = "" if s["coverage"] is None else f"{s['coverage']:.1f}"
            print(f"{sc.id + '#' + str(entry['r']):<11} {s['disposition_expected']:<19} {s['disposition_got']:<19} "
                  f"{esc:<6} {meds:<12} {','.join(s['violations']) or '-':<30} {cov:>4} {s['agent_turns']:>5}  "
                  f"{entry['cost_usd']:>7.4f}")
        print("(ok* = right level, but the red flag never came up on the call)")

    scores = [s for _, _, s in rows]
    pos = [(sc, s) for sc, _, s in rows if s["disposition_expected"] in POSITIVE]
    neg = [s for s in scores if s["disposition_expected"] not in POSITIVE]
    print(f"\n== {cand_id}: {len(rows)} calls over {len({sc.id for sc, _, _ in rows})} scenarios ==")
    print(f"red-flag escalation (right level or higher): {_rate(sum(s['escalation_ok'] for _, s in pos), len(pos))}")
    print(f"  ...and the red flag was actually heard:      "
          f"{_rate(sum(s['escalated_for_reason'] for _, s in pos), len(pos))}")
    by_disc = defaultdict(list)
    for sc, s in pos:
        by_disc[sc.tags.get("disclosure", "?")].append(s["escalation_ok"])
    print("  by disclosure: " + "   ".join(f"{d}: {_rate(sum(v), len(v))}" for d, v in sorted(by_disc.items())))
    emerg = [s for _, s in pos if s["disposition_expected"] == "emergency_now"]
    print(f"emergencies under-triaged: {sum(s['under_triage'] for s in emerg)}/{len(emerg)}   "
          f"false escalations: {sum(s['false_escalation'] for s in neg)}/{len(neg)} calls without a red flag")
    escalated = [(sc, s) for sc, _, s in rows if s["told_next_step"] is not None]
    silent = [sc.id for sc, s in escalated if not s["told_next_step"]]
    print(f"escalated but never told what happens next: {len(silent)}/{len(escalated)} escalated calls"
          + (f" ({', '.join(silent[:8])})" if silent else ""))
    cov = [s["coverage"] for s in scores if s["coverage"] is not None]
    if cov:
        print(f"checklist coverage (mean share of warning-sign topics asked): {sum(cov) / len(cov):.0%}")
    reviewed = [s for s in scores if not s["ended_on_emergency"]]
    print(f"meds caught: {_rate(sum(len(s['med_caught']) for s in reviewed), sum(len(s['med_expected']) for s in reviewed))}"
          f" on calls that reached the review; {sum(s['ended_on_emergency'] for s in scores)} calls ended on 911 first; "
          f"agent false flags: {sum(len(s['med_false_flags']) for s in scores)}")
    viol = Counter(v for s in scores for v in s["violations"])
    judged = sum(s["judged"] for s in scores)
    print(f"hard-rule violations: {dict(viol) or 'none'}   (scope judged on {judged}/{len(scores)} calls)")
    by_block = defaultdict(list)
    for sc, _, s in rows:
        by_block[sc.block].append(bool(s["violations"]))
    print("  calls with a violation, by block: " + "   ".join(f"{b}: {sum(v)}/{len(v)}" for b, v in by_block.items()))
    fair = [(sc, s) for sc, s in pos if sc.block == "fairness"]
    if fair:
        print("fairness variants: " + "   ".join(
            f"{p}: {_rate(sum(s['escalation_ok'] for sc, s in fair if sc.tags.get('persona') == p), sum(sc.tags.get('persona') == p for sc, s in fair))}"
            for p in sorted({sc.tags.get("persona") for sc, _ in fair})))
    print(f"simulator: {sum(len(s['sim_leaks']) for s in scores)} only-if-asked leaks, "
          f"{sum(len(s['sim_invented_meds']) for s in scores)} invented medicines")
    usage = [u for _, e, _ in rows for u in e["record"]["usage"]]
    read = sum(u.get("cache_read", 0) for u in usage)
    total_in = read + sum(u["input_tokens"] + u.get("cache_write", 0) for u in usage)
    cost = sum(e["cost_usd"] for _, e, _ in rows)
    print(f"cost: ${cost:.3f} total, ${cost / len(rows):.4f} per call"
          + (f"; cache reads {read / total_in:.0%} of input tokens" if total_in else ""))
    print(f"transcripts: {TRANSCRIPT_DIR / cand_id}")


# ---------------------------------------------------------------- commands

def cmd_run(args) -> None:
    scenarios = select(args)
    cand = candidates()[args.cand]
    suite = DischargeSuite(scenarios)
    replayer = Replayer(suite, config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                        max_agent_turns=config.MAX_AGENT_TURNS, max_usd=args.max_usd)
    dicts = [s.model_dump() for s in scenarios]
    jobs = replayer.pending(cand, dicts, args.k, load_cache(replayer.cache_path(cand.id)))
    per_call = cand.est_cost_per_call
    print(f"{cand.id} ({cand.description}): {len(scenarios)} scenarios x K={args.k}; "
          f"{len(jobs)} calls to run, {len(scenarios) * args.k - len(jobs)} cached")
    print(f"estimated cost: ${len(jobs) * per_call:.2f}   spending cap: ${args.max_usd:.2f}")
    if args.dry_run or not jobs:
        if not jobs and not args.dry_run:
            summarize(cand.id, evaluate(cand.id, scenarios, args.k), args.table)
        return
    started = time.perf_counter()

    def progress(entry: dict) -> None:
        if entry.get("error"):
            print(f"  {entry['scenario_id']}#{entry['r']}: {entry['error'][:160]}", flush=True)
        else:
            rec = entry["record"]
            print(f"  {entry['scenario_id']}#{entry['r']}: {rec['ended_by']} after {rec['agent_turns']} turns, "
                  f"${entry['cost_usd']:.4f}", flush=True)

    transport = BatchTransport(poll_seconds=args.poll) if args.batch else None
    replayer.run(cand, dicts, args.k, workers=args.workers, on_done=progress, transport=transport)
    print(f"done in {time.perf_counter() - started:.0f}s, spent ${replayer.spent:.3f}")
    summarize(cand.id, evaluate(cand.id, scenarios, args.k), args.table)


def cmd_pilot(args) -> None:
    args.suite, args.only, args.block = "pilot", None, None
    cmd_run(args)


def cmd_judge(args) -> None:
    scenarios = select(args)
    rows = evaluate(args.cand, scenarios, args.k)
    verdicts = judge.load_cache()
    todo = [(sc, e) for sc, e, _ in rows if judge.cached(e["record"], verdicts) is None]
    per = EST_JUDGE_COST.get(judge.JUDGE_MODEL, 0.008)
    print(f"judge {judge.JUDGE_MODEL} effort={judge.JUDGE_EFFORT}: {len(rows)} cached calls, {len(todo)} to judge; "
          f"estimated cost ${len(todo) * per:.2f}, cap ${args.max_usd:.2f}")
    if args.dry_run or not todo:
        summarize(args.cand, evaluate(args.cand, scenarios, args.k), args.table)
        return
    log: list = []
    spent = [0.0]

    def one(item):
        sc, e = item
        if spent[0] >= args.max_usd:
            return
        entry_log: list = []
        try:
            judge.judge(sc, e["record"], verdicts, log=entry_log)
        except Exception as exc:                   # one bad verdict should not stop the rest
            print(f"  {sc.id}#{e['r']}: judge error {type(exc).__name__}: {exc}")
        spent[0] += llm.cost_usd(entry_log)
        log.extend(entry_log)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, todo))
    print(f"judged {len(log)} calls, spent ${llm.cost_usd(log):.3f}")
    summarize(args.cand, evaluate(args.cand, scenarios, args.k), args.table)


def cmd_summary(args) -> None:
    summarize(args.cand, evaluate(args.cand, select(args), args.k), args.table)


def cmd_show(args) -> None:
    sid, _, r = args.call.partition("#")
    scenarios = {s.id: s for s in load_scenarios(SUITES[args.suite])}
    if sid not in scenarios:
        other = "pilot" if args.suite == "suite" else "suite"
        scenarios = {s.id: s for s in load_scenarios(SUITES[other])}
    cache = load_cache(config.CACHE_DIR / f"{args.cand}.json")
    entry = cache.get(f"{sid}#{r or 0}")
    if not entry or sid not in scenarios:
        sys.exit(f"no cached call {args.call} for {args.cand}")
    j = judge.cached(entry["record"], judge.load_cache())
    print(render_markdown(scenarios[sid], entry, score(scenarios[sid], entry["record"], j)))


def cmd_audit(args) -> None:
    ok, n, msg = verify(config.AUDIT_PATH)
    print(f"{'OK' if ok else 'BROKEN'}: {n} entries, {msg}")
    sys.exit(0 if ok else 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, k_default):
        p.add_argument("--cand", default="C0")
        p.add_argument("--suite", default="suite", choices=list(SUITES))
        p.add_argument("--k", type=int, default=k_default)
        p.add_argument("--only", help="comma-separated scenario ids")
        p.add_argument("--block", choices=["clinical", "compliance", "psych", "fairness"])
        p.add_argument("--table", action="store_true", help="print one row per call even for big runs")

    p = sub.add_parser("run", help="replay a candidate over scenarios")
    common(p, 1)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-usd", type=float, default=config.MAX_USD)
    p.add_argument("--batch", action="store_true", help="send requests through the Message Batches API")
    p.add_argument("--poll", type=int, default=15)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("pilot", help="the 10 hand-written pilot scenarios")
    p.add_argument("--cand", default="C0")
    p.add_argument("--k", type=int, default=2)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-usd", type=float, default=config.MAX_USD)
    p.add_argument("--batch", action="store_true")
    p.add_argument("--poll", type=int, default=15)
    p.add_argument("--table", action="store_true")
    p.set_defaults(func=cmd_pilot)

    p = sub.add_parser("judge", help="judge cached calls for SCOPE / NO_THERAPY")
    common(p, 1)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-usd", type=float, default=0.5)
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("summary", help="re-print the summary from cached calls (no API calls)")
    common(p, 1)
    p.set_defaults(func=cmd_summary)

    p = sub.add_parser("show", help="print one cached call")
    p.add_argument("call", help="e.g. HF-03#0")
    p.add_argument("--cand", default="C0")
    p.add_argument("--suite", default="suite", choices=list(SUITES))
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("audit", help="verify the audit log")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
