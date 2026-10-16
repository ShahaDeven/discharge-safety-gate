"""How many replays does the gate need? Resample the cached replays at K = 1, 2, ... and re-run
the gate many times. No API calls.

    python -m evals.curve                 # writes evals/results/curve.json and prints the table

For each candidate: the share of draws at each K that reach the same verdict as the gate
on all replays. For the baseline vs itself (A/A): the share of draws where the gate raises
a false alarm (anything but PROMOTE). Single runs (K=1) are what most teams compare;
this shows what that costs.
"""

import json
import random
from collections import Counter

from agentgate.gate import PROMOTE, baseline_profile, gate, noise_floor, split_aa
from discharge import config
from discharge.gate_metrics import SPECS
from evals.experiments import PLAN
from evals.gate import load_groups

OUT = config.RESULTS_DIR / "curve.json"
DRAWS = 100
N_BOOT = 400


def _subsample(groups: dict, k: int, rng: random.Random) -> dict:
    return {sid: rng.sample(calls, k) for sid, calls in groups.items() if len(calls) >= k}


def main() -> None:
    groups, _, _ = load_groups()
    base = groups["C0"]
    profile = baseline_profile(base, SPECS)                # what the full baseline knows, for every draw
    a, b = split_aa(base)
    floor = noise_floor(gate(a, b, SPECS, profile=profile))
    rng = random.Random(23)
    out = {"draws": DRAWS, "A/A": {}, "candidates": {}}

    k_base = min(len(c) for c in base.values())
    for k in range(1, k_base // 2 + 1):
        verdicts = Counter()
        for _ in range(DRAWS):
            pair = {sid: rng.sample(calls, 2 * k) for sid, calls in base.items()}
            verdicts[gate({s: c[:k] for s, c in pair.items()}, {s: c[k:] for s, c in pair.items()}, SPECS,
                          noise=floor, profile=profile, n_boot=N_BOOT, seed=rng.randrange(10**6)).verdict] += 1
        out["A/A"][k] = {"false_alarm": 1 - verdicts[PROMOTE] / DRAWS, "verdicts": dict(verdicts)}
        print(f"A/A  K={k}: false alarm {1 - verdicts[PROMOTE] / DRAWS:.0%}  {dict(verdicts)}", flush=True)

    for step in PLAN[1:]:
        if step.cand not in groups:
            continue
        cand = groups[step.cand]
        full = gate(base, cand, SPECS, noise=floor, profile=profile).verdict
        row = {"full_verdict": full, "k": {}}
        for k in range(1, step.k + 1):
            verdicts = Counter()
            for _ in range(DRAWS):
                verdicts[gate(_subsample(base, k, rng), _subsample(cand, k, rng), SPECS, noise=floor,
                              profile=profile, n_boot=N_BOOT, seed=rng.randrange(10**6)).verdict] += 1
            agree = verdicts[full] / DRAWS
            row["k"][k] = {"agree_with_full": agree, "verdicts": dict(verdicts)}
            print(f"{step.cand} K={k}: same verdict as full ({full}) in {agree:.0%}  {dict(verdicts)}", flush=True)
        out["candidates"][step.cand] = row
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
