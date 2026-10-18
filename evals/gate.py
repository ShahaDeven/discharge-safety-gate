"""Push every candidate through the gate against the baseline (C0). No API calls.

    python -m evals.gate                 # verdicts, metric tables, ledger entries
    python -m evals.gate --cand C1       # one candidate, with per-scenario detail

First the baseline is compared with itself (replays 0-1 vs 2-3, "A/A"): that must PROMOTE,
and the size of what moved is the noise floor every real change has to beat. Results go
to evals/results/gate.json; each verdict is appended to the ledger (ledger.md) and to the
hash-chained audit log, under the active clinical policy (evals/policy.py), whose name and fingerprint
every verdict records.
"""

import argparse
import json

from agentgate.audit import AuditLog
from agentgate.gate import GATE_VERSION, baseline_profile, gate, noise_floor, split_aa
from agentgate.ledger import Ledger
from discharge import clinical_policy, config
from discharge.candidates import candidates
from discharge.gate_metrics import SPECS, group
from discharge.scenario import load_scenarios
from evals.experiments import K_FOR, PLAN, scenarios_for
from evals.run import SUITES, evaluate

OUT = config.RESULTS_DIR / "gate.json"
LEDGER = Ledger(config.RESULTS_DIR / "ledger.json", config.RESULTS_DIR / "ledger.md", AuditLog(config.AUDIT_PATH))


def load_groups(pol=None) -> tuple[dict, dict, dict]:
    """{cand: grouped metrics}, {cand: rows}, {cand: judged share} for every step with cached calls."""
    suite = load_scenarios(SUITES["suite"])
    groups, rows_by, judged = {}, {}, {}
    for step in PLAN:
        rows = evaluate(step.cand, scenarios_for(step, suite), step.k, pol)
        if rows:
            rows_by[step.cand] = rows
            groups[step.cand] = group(rows)
            judged[step.cand] = sum(s["judged"] for _, _, s in rows) / len(rows)
    return groups, rows_by, judged


def run_all(n_boot: int = 2000, pol=None) -> dict:
    pol = pol or clinical_policy.active()
    groups, rows_by, judged = load_groups(pol)
    if "C0" not in groups:
        raise SystemExit("no cached C0 calls: run `python -m evals.experiments run --only C0` first")
    profile = baseline_profile(groups["C0"], SPECS)        # from ALL baseline replays
    a, b = split_aa(groups["C0"])
    aa = gate(a, b, SPECS, profile=profile, n_boot=n_boot)
    floor = noise_floor(aa)
    results = {"gate_version": GATE_VERSION, "policy": pol.to_dict(),
               "A/A": {"description": "baseline vs itself (replays 0-1 vs 2-3)", **aa.to_dict()},
               "noise_floor": floor, "judged_share": judged,
               "baseline_profile": {"known_rules": sorted(profile["known_rules"]),
                                    "flaky": {k: sorted(v) for k, v in profile["flaky"].items()}}}
    for step in PLAN[1:]:
        if step.cand not in groups:
            continue
        r = gate(groups["C0"], groups[step.cand], SPECS, noise=floor, profile=profile, n_boot=n_boot)
        results[step.cand] = {"description": candidates()[step.cand].description, "k": step.k, **r.to_dict()}
    return results


def print_result(name: str, r: dict, detail: bool = False) -> None:
    print(f"\n{name}: {r['description']}  ->  {r['verdict']}   ({r['scenarios']} shared scenarios, gate v{r['version']})")
    for reason in r["reasons"]:
        print(f"   - {reason}")
    print(f"   {'metric':<44} {'base':>6} {'cand':>6} {'diff':>7}  {'CI*':>17}  status")
    for m in r["metrics"]:
        base = "-" if m["base"] is None else f"{m['base']:.0%}"
        cand = "-" if m["cand"] is None else f"{m['cand']:.0%}"
        print(f"   {m['label'] + ' (' + m['tier'] + ')':<44} {base:>6} {cand:>6} {m['diff']:>+7.1%}  "
              f"{m['lo']:>+7.1%} to {m['hi']:>+6.1%}  {m['status']}")
    for rr in r["rules"]:
        print(f"   rule {rr['rule']:<24} base {rr['base_calls']}/{rr['base_n']}  cand {rr['cand_calls']}/{rr['cand_n']}"
              f"  p={rr['p']:.3g}  {rr['status']}")
    if detail:
        for sid, rules in r["new_violations"].items():
            print(f"   new violation {sid}: {rules}")
        for m in r["metrics"]:
            if m["new_misses"]:
                print(f"   new consistent misses ({m['name']}): {m['new_misses']}")


def baseline_label(version: int, pol: dict) -> str:
    return f"C0 (gate v{version}, policy {pol['name']} [{pol['fingerprint']}])"


def record(cand: str, r: dict, pol: dict) -> None:
    """Ledger entry for a built-in candidate's verdict (skipped if the same verdict is already recorded)."""
    step = next(s for s in PLAN if s.cand == cand)
    summary = {m["name"]: [m["base"], m["cand"], m["status"]] for m in r["metrics"]}
    LEDGER.record(cand, r["description"], candidates()[cand].fingerprint, baseline_label(r["version"], pol), r["verdict"],
                  r["reasons"], summary, {"C0": K_FOR["C0"], cand: step.k})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cand", help="show one candidate in detail")
    args = ap.parse_args()
    results = run_all()
    OUT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"clinical policy: {results['policy']['name']} [{results['policy']['fingerprint']}]")

    low = [c for c, share in results["judged_share"].items() if share < 1.0]
    if low:
        print(f"WARNING: scope not judged on every call for {low}; run `python -m evals.experiments judge` first")
    print_result("A/A", results["A/A"], detail=args.cand == "A/A")
    if results["A/A"]["verdict"] != "PROMOTE":
        print("   !! the baseline does not pass against itself: the gate is reacting to noise")
    for step in PLAN[1:]:
        if step.cand in results and (not args.cand or args.cand == step.cand):
            r = results[step.cand]
            print_result(step.cand, r, detail=bool(args.cand))
            record(step.cand, r, results["policy"])
    print(f"\ngate results -> {OUT}\nledger -> {LEDGER.path_md}")


if __name__ == "__main__":
    main()
