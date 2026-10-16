"""Experiments: run every candidate the gate will judge, in one place, with costs up front.

    python -m evals.experiments plan                 # what will run, per step and in total ($0 - nothing is called)
    python -m evals.experiments run                  # every step in order (each stops at its own cap)
    python -m evals.experiments run --only C1,C3     # chosen steps
    python -m evals.experiments run --only C3 --batch   # same calls via Message Batches (50% off if the cache stays warm)
    python -m evals.experiments judge                # judge every cached call of every step
    then: python -m evals.gate  and  python -m evals.report

Steps (approved budget: Standard + Sonnet comparison, ~$4.80):
    C0  baseline            all 102 scenarios   K=4   (split 2+2 for the noise floor)
    C1  short calls         all 102             K=3
    C3  warm greeting       compliance 24 + 20 clinical   K=3
    C4  effort high         clinical 64         K=2
    C2  Sonnet              32 hard positives (implicit / only-if-asked red flags)   K=2
"""

import argparse
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from agentgate import llm
from agentgate.audit import AuditLog
from agentgate.batch import BatchTransport
from agentgate.replay import Replayer, load_cache
from discharge import config, judge
from discharge.candidates import candidates
from discharge.persona import DischargeSuite
from discharge.scenario import Scenario, load_scenarios
from evals.run import EST_JUDGE_COST, SUITES, evaluate


def _c3_subset(s: Scenario) -> bool:
    # Every compliance case, plus the first 5 clinical scenarios of each condition (patient picks up).
    return s.block == "compliance" or (s.block == "clinical" and int(s.id.split("-")[1]) <= 5)


SUBSETS = {
    "all": lambda s: True,
    "clinical": lambda s: s.block == "clinical",
    "c3": _c3_subset,
    "hard_positives": lambda s: s.block == "clinical" and s.tags.get("disclosure") in ("implicit", "only_if_asked"),
}


@dataclass(frozen=True)
class Step:
    cand: str
    subset: str
    k: int
    why: str


PLAN = [
    Step("C0", "all", 4, "baseline; replays split 2+2 give the noise floor"),
    Step("C1", "all", 3, "short calls: no warning-sign checklist"),
    Step("C3", "c3", 3, "warm greeting that names the hospital stay"),
    Step("C4", "clinical", 2, "same prompt, effort high (a change that should not hurt)"),
    Step("C2", "hard_positives", 2, "same prompt on Sonnet 5.5, on the hardest red flags"),
]
K_FOR = {s.cand: s.k for s in PLAN}


def scenarios_for(step: Step, suite: list[Scenario]) -> list[Scenario]:
    return [s for s in suite if SUBSETS[step.subset](s)]


def estimate(step: Step, suite: list[Scenario]) -> tuple[int, int, float]:
    cand = candidates()[step.cand]
    scs = scenarios_for(step, suite)
    replayer = Replayer(DischargeSuite(scs), config.CACHE_DIR, AuditLog(config.AUDIT_PATH))
    jobs = replayer.pending(cand, [s.model_dump() for s in scs], step.k, load_cache(replayer.cache_path(cand.id)))
    return len(scs), len(jobs), len(jobs) * cand.est_cost_per_call


def cmd_plan(args) -> None:
    suite = load_scenarios(SUITES["suite"])
    total, total_calls = 0.0, 0
    print(f"{'step':<5} {'scenarios':>9} {'K':>2} {'calls to run':>13} {'est. $':>7}  why")
    for step in PLAN:
        n, jobs, cost = estimate(step, suite)
        total += cost
        total_calls += jobs
        print(f"{step.cand:<5} {n:>9} {step.k:>2} {jobs:>13} {cost:>7.2f}  {step.why}")
    judge_cost = sum(n * k for n, k in ((len(scenarios_for(s, suite)), s.k) for s in PLAN)) \
        * EST_JUDGE_COST.get(judge.JUDGE_MODEL, 0.008)
    print(f"{'judge':<5} {'':>9} {'':>2} {'':>13} {judge_cost:>7.2f}  scope / no-therapy verdicts (cached ones are free)")
    print(f"\ntotal: {total_calls} calls, about ${total + judge_cost:.2f}")


def cmd_run(args) -> None:
    suite = load_scenarios(SUITES["suite"])
    only = set(args.only.split(",")) if args.only else None
    for step in PLAN:
        if only and step.cand not in only:
            continue
        cand = candidates()[step.cand]
        scs = scenarios_for(step, suite)
        n, jobs, est = estimate(step, suite)
        cap = round(max(0.10, est * 1.5), 2)
        mode = "batch (all calls start at once: the cap cannot stop a step midway)" if args.batch else "live"
        print(f"\n=== {step.cand}: {step.why} | {n} scenarios x K={step.k}, {jobs} calls to run, "
              f"est ${est:.2f} live, cap ${cap:.2f}, {mode}", flush=True)
        if not jobs:
            continue
        replayer = Replayer(DischargeSuite(scs), config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                            max_agent_turns=config.MAX_AGENT_TURNS, max_usd=cap)
        started, done, errors = time.perf_counter(), Counter(), []

        def progress(entry: dict) -> None:
            if entry.get("error"):
                errors.append(f"{entry['scenario_id']}#{entry['r']}: {entry['error'][:120]}")
            else:
                done[entry["record"]["ended_by"]] += 1
            finished = sum(done.values()) + len(errors)
            if finished % 25 == 0 or finished == jobs:
                print(f"  {finished}/{jobs} calls, ${replayer.spent:.3f} so far", flush=True)

        transport = BatchTransport(poll_seconds=20) if args.batch else None
        replayer.run(cand, [s.model_dump() for s in scs], step.k, workers=args.workers, on_done=progress,
                     transport=transport)
        print(f"  done in {time.perf_counter() - started:.0f}s, spent ${replayer.spent:.3f} "
              f"(live estimate was ${est:.2f}); endings {dict(done)}")
        if args.batch:
            usage = [u for e in load_cache(replayer.cache_path(cand.id)).values()
                     if e.get("mode") == "batch" for u in e["record"]["usage"]]
            read = sum(u.get("cache_read", 0) for u in usage)
            total_in = read + sum(u["input_tokens"] + u.get("cache_write", 0) for u in usage)
            if total_in:
                print(f"  batch cache reads: {read / total_in:.0%} of input tokens (live runs: ~86%; "
                      f"far below that means batch costs MORE than live)")
        for e in errors[:5]:
            print("  ", e)
        if errors:
            print(f"  {len(errors)} calls did not finish (rerun the step to retry them; cached calls are free)")


def cmd_judge(args) -> None:
    suite = load_scenarios(SUITES["suite"])
    verdicts = judge.load_cache()
    todo = []
    for step in PLAN:
        for sc, e, _ in evaluate(step.cand, scenarios_for(step, suite), step.k):
            if judge.cached(e["record"], verdicts) is None:
                todo.append((sc, e))
    per = EST_JUDGE_COST.get(judge.JUDGE_MODEL, 0.008)
    print(f"{len(todo)} calls to judge, est ${len(todo) * per:.2f}, cap ${args.max_usd:.2f}")
    spent, log = [0.0], []

    def one(item):
        sc, e = item
        if spent[0] >= args.max_usd:
            return
        entry_log: list = []
        try:
            judge.judge(sc, e["record"], verdicts, log=entry_log)
        except Exception as exc:
            print(f"  {sc.id}#{e['r']}: judge error {type(exc).__name__}: {exc}")
        spent[0] += llm.cost_usd(entry_log)
        log.extend(entry_log)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(one, todo))
    print(f"judged {len(log)} calls, spent ${llm.cost_usd(log):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(func=cmd_plan)
    p = sub.add_parser("run")
    p.add_argument("--only", help="comma-separated candidates, e.g. C0 or C1,C3")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--batch", action="store_true", help="send the requests through the Message Batches API")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("judge")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--max-usd", type=float, default=0.75)
    p.set_defaults(func=cmd_judge)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
