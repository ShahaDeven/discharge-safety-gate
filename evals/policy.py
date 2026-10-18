"""Clinical policy settings: which kinds of line count as giving medical advice (discharge/clinical_policy.py).

    python -m evals.policy rejudge --dry-run      # how many calls still need category labels, and the cost
    python -m evals.policy rejudge                # label every cached call once (about $0.35, cap $0.50)
    python -m evals.policy show                   # the active policy, flag counts, judge vs reviewer
    python -m evals.policy preview --on hold_off  # what every verdict would be with a switch changed (free)
    python -m evals.policy apply --name "Strict" --on reassurance --on hold_off   # make it active (free)

Applying a policy re-scores every cached call, rewrites gate.json, re-gates finished proposals and outside
agents without placing a single call (spending cap $0), and records each verdict in the ledger with the
policy's name and fingerprint. The console's Policy page calls the same functions.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentgate import llm
from agentgate.audit import AuditLog
from discharge import clinical_policy, config, judge
from discharge.clinical_policy import CATEGORIES, EXTENSIONS, Policy
from discharge.scenario import load_scenarios
from evals import agents, flag_categories, gate, proposals
from evals.experiments import PLAN, scenarios_for
from evals.run import SUITES, evaluate

PER_CALL = 0.0003              # measured: $0.263 for 998 calls, rounded up for the longer label output
SKIP_CACHES = {"judge.json", "sonnet_pilot_v1.json"}


class PolicyIn(BaseModel):
    """What the Policy page may send. Locked categories are always on, whatever is sent."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Draft", min_length=3, max_length=60, pattern=r"^[A-Za-z0-9 .,'()\-:/]+$")
    note: str = Field(default="", max_length=500)
    scope: list[Literal[clinical_policy.SCOPE_KEYS]] = Field(default_factory=list, max_length=len(clinical_policy.SCOPE_KEYS))
    extend: list[Literal[clinical_policy.EXTENSION_KEYS]] = Field(default_factory=list,
                                                                  max_length=len(clinical_policy.EXTENSION_KEYS))

    def policy(self) -> Policy:
        return Policy(self.name, frozenset(self.scope), frozenset(self.extend))


# ---------------------------------------------------------------- labels (the only step that costs)

def unjudged() -> list[tuple]:
    """(scenario, cached entry) for every cached call without category labels from the current judge."""
    by_id = {s.id: s for path in (SUITES["suite"], proposals.HOLDOUT_PATH) for s in load_scenarios(path)}
    verdicts, todo = judge.load_cache(), []
    for path in sorted(config.CACHE_DIR.glob("*.json")):
        if path.name in SKIP_CACHES:
            continue
        for e in json.loads(path.read_text(encoding="utf-8")).values():
            sc = by_id.get(e.get("scenario_id"))
            if sc and e.get("record") and judge.cached(e["record"], verdicts) is None:
                todo.append((sc, e))
    return todo


def rejudge(max_usd: float, workers: int = 8, dry_run: bool = False) -> float:
    todo = unjudged()
    print(f"{len(todo)} calls to label, about ${len(todo) * PER_CALL:.2f} (cap ${max_usd:.2f})")
    if dry_run or not todo:
        return 0.0
    verdicts, log, spent = judge.load_cache(), [], [0.0]

    def one(item):
        sc, e = item
        if spent[0] >= max_usd:
            return
        entry: list = []
        try:
            judge.judge(sc, e["record"], verdicts, log=entry)
        except Exception as exc:                   # one bad label should not stop the rest
            print(f"  {sc.id}#{e['r']}: judge error {type(exc).__name__}: {exc}")
        spent[0] += llm.cost_usd(entry)
        log.extend(entry)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    left = len(unjudged())
    print(f"labeled {len(log)} calls, spent ${llm.cost_usd(log):.3f}" + (f"; {left} still unlabeled (cap reached?)" if left else ""))
    return llm.cost_usd(log)


# ---------------------------------------------------------------- what the switches change (free)

def _labels_version() -> float:
    return judge.CACHE.stat().st_mtime if judge.CACHE.exists() else 0.0


_memo: dict = {}


def memo(fn):
    """Recompute only when the judge's labels change."""
    def wrapped():
        key = (fn.__name__, _labels_version())
        if key not in _memo:
            _memo[key] = fn()
        return _memo[key]
    wrapped.__name__ = fn.__name__
    return wrapped


@memo
def flag_counts() -> dict:
    """Per category: lines the judge labeled and calls they appear in, across the built-in candidates."""
    suite = load_scenarios(SUITES["suite"])
    verdicts = judge.load_cache()
    out = {c.key: {"lines": 0, "calls": 0} for c in CATEGORIES}
    out["therapy"] = {"lines": 0, "calls": 0}
    judged = total = 0
    for step in PLAN:
        for sc, e, _ in evaluate(step.cand, scenarios_for(step, suite), step.k, clinical_policy.DEFAULT):
            total += 1
            j = judge.cached(e["record"], verdicts)
            if j is None:
                continue
            judged += 1
            cats = [f["category"] for f in j["flags"]]
            for c in set(cats):
                out[c]["calls"] += 1
            for c in cats:
                out[c]["lines"] += 1
    return {"categories": out, "judged": judged, "calls": total}


@memo
def reviewer_agreement() -> dict | None:
    """Judge category vs the reviewer's category, on the lines reviewed one by one (flag_categories.py).
    A reviewed call counts as agreeing if the judge gave its call that category (for "misread" lines:
    if the judge now labels nothing in that call)."""
    if not flag_categories.CATEGORIES:
        return None
    suite = load_scenarios(SUITES["suite"])
    verdicts = judge.load_cache()
    labels = {}
    for step in PLAN:
        for sc, e, _ in evaluate(step.cand, scenarios_for(step, suite), step.k, clinical_policy.DEFAULT):
            j = judge.cached(e["record"], verdicts)
            if j is not None:
                labels[f"{step.cand}:{sc.id}#{e['r']}"] = {f["category"] for f in j["flags"]}
    rows, agree, n = [], 0, 0
    for cat, (label, kind, keys) in flag_categories.CATEGORIES.items():
        seen = [labels[k] for k in keys if k in labels]
        hits = sum((not cats) if kind == "error" else (cat in cats) for cats in seen)
        rows.append({"category": cat, "label": label, "lines": len(seen), "judge_agrees": hits})
        agree, n = agree + hits, n + len(seen)
    return {"rows": rows, "agree": agree, "n": n}


_preview: dict = {}


def preview(pol: Policy) -> dict:
    """Every built-in verdict under this policy, next to the active one's. A few seconds; cached per policy."""
    if pol.fingerprint not in _preview:
        r = gate.run_all(pol=pol)                      # same bootstrap as the real gate, so verdicts match
        if len(_preview) > 32:
            _preview.clear()
        _preview[pol.fingerprint] = {c: {"verdict": v["verdict"], "reasons": v["reasons"][:3],
                                         "rules": {x["rule"]: [x["base_calls"], x["base_n"], x["cand_calls"], x["cand_n"]]
                                                   for x in v["rules"]}}
                                     for c, v in r.items() if isinstance(v, dict) and "verdict" in v}
    return _preview[pol.fingerprint]


# ---------------------------------------------------------------- apply

def apply(pol: Policy, note: str = "", owner: str = "", progress=lambda *a: None) -> dict:
    """Make `pol` active and bring every verdict up to date under it. Places no calls and labels nothing."""
    before = json.loads(gate.OUT.read_text(encoding="utf-8")) if gate.OUT.exists() else {}
    entry = clinical_policy.set_active(pol, note=note, owner=owner)
    AuditLog(config.AUDIT_PATH).append("policy_applied", {**pol.to_dict(), "note": note, "owner": owner})
    progress("built-in agents", 0, 1)
    results = gate.run_all(pol=pol)
    gate.OUT.write_text(json.dumps(results, indent=1), encoding="utf-8")
    changes = []
    for c, r in results.items():
        if isinstance(r, dict) and "verdict" in r:
            if c != "A/A":
                gate.record(c, r, results["policy"])
            old = before.get(c, {}).get("verdict")
            if old and old != r["verdict"]:
                changes.append({"id": c, "before": old, "after": r["verdict"]})
    others = [(proposals.PROPOSAL, p, proposals.candidate(p)) for p in proposals.all_proposals()
              if p["status"] == "done" and p["result"].get("visible")]
    others += [(agents.AGENT, a, agents.candidate(a)) for a in agents.all_agents()
               if a["status"] == "done" and a["result"].get("visible")]
    for i, (kind, rec, cand) in enumerate(others):
        progress("proposals and outside agents", i, len(others))
        old = rec["result"]["verdict"]
        new = proposals.gate_candidate(kind, rec, cand, 0.0, lambda *a: None, place_calls=False)["verdict"]
        if new != old:
            changes.append({"id": rec["id"], "before": old, "after": new})
    progress("done", 1, 1)
    return {"policy": entry, "changes": changes}


def parse_switches(on: list[str], off: list[str], base: Policy | None = None) -> Policy:
    base = base or clinical_policy.active()
    scope, extend = set(base.scope), set(base.extend)
    for key in on:
        (extend if key in clinical_policy.EXTENSION_KEYS else scope).add(key)
    for key in off:
        (extend if key in clinical_policy.EXTENSION_KEYS else scope).discard(key)
    return Policy(base.name, frozenset(scope), frozenset(extend))


def show() -> None:
    pol = clinical_policy.active()
    print(f"active policy: {pol.label()}")
    counts = flag_counts()
    print(f"labels on {counts['judged']} of {counts['calls']} built-in calls")
    for c in CATEGORIES:
        state = "always" if c.locked else ("counts" if c.key in pol.scope else "allowed")
        n = counts["categories"][c.key]
        print(f"  {c.key:<14} {state:<8} {n['lines']:>4} lines in {n['calls']:>4} calls   {c.label}")
    n = counts["categories"]["therapy"]
    print(f"  {'therapy':<14} {'law/ext':<8} {n['lines']:>4} lines in {n['calls']:>4} calls")
    for e in EXTENSIONS:
        print(f"  {e.key:<26} {'on' if e.key in pol.extend else 'off':<4} {e.label} ({e.law})")
    agree = reviewer_agreement()
    if agree:
        print(f"judge category matches the reviewer's on {agree['agree']}/{agree['n']} reviewed lines")
        for r in agree["rows"]:
            print(f"  {r['category']:<12} {r['judge_agrees']}/{r['lines']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rejudge")
    r.add_argument("--max-usd", type=float, default=0.50)
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("show")
    for name in ("preview", "apply"):
        s = sub.add_parser(name)
        s.add_argument("--on", action="append", default=[])
        s.add_argument("--off", action="append", default=[])
        if name == "apply":
            s.add_argument("--name", required=True)
            s.add_argument("--note", default="")
    args = ap.parse_args()
    if args.cmd == "rejudge":
        rejudge(args.max_usd, args.workers, args.dry_run)
    elif args.cmd == "show":
        show()
    elif args.cmd == "preview":
        pol = parse_switches(args.on, args.off)
        now = preview(clinical_policy.active())
        for c, v in preview(pol).items():
            mark = "" if v["verdict"] == now[c]["verdict"] else f"   (now {now[c]['verdict']})"
            print(f"{c:<5} {v['verdict']}{mark}")
    else:
        if not re.fullmatch(r"[A-Za-z0-9 .,'()\-:/]{3,60}", args.name):
            raise SystemExit("name: 3-60 letters, digits and simple punctuation")
        pol = parse_switches(args.on, args.off)
        out = apply(Policy(args.name, pol.scope, pol.extend), note=args.note, owner="cli")
        print(f"active: {out['policy']['name']} [{out['policy']['fingerprint']}]")
        for c in out["changes"]:
            print(f"  {c['id']}: {c['before']} -> {c['after']}")
        if not out["changes"]:
            print("  no verdict changed")


if __name__ == "__main__":
    main()
