"""The gate on synthetic metric groups, and the ledger. Offline, no cache needed."""

from agentgate.audit import AuditLog, verify
from agentgate.gate import HOLD, PROMOTE, REJECT, MetricSpec, baseline_profile, gate, noise_floor, split_aa
from agentgate.ledger import Ledger

SPECS = [MetricSpec("recall", "safety", "Recall"), MetricSpec("coverage", "quality", "Coverage")]


def calls(recall: list, coverage: float = 0.8, hard: list | None = None) -> list[dict]:
    return [{"metrics": {"recall": r, "coverage": coverage}, "hard": list(hard or [])} for r in recall]


def suite(n: int, recall: list, coverage: float = 0.8, hard_on: dict | None = None) -> dict:
    hard_on = hard_on or {}
    return {f"S{i:02d}": calls(recall, coverage, hard_on.get(f"S{i:02d}")) for i in range(n)}


def test_identical_runs_promote():
    base = suite(40, [1.0, 1.0, 1.0, 1.0])
    assert gate(base, suite(40, [1.0, 1.0, 1.0]), SPECS).verdict == PROMOTE


def test_one_new_hard_violation_rejects():
    base = suite(40, [1.0] * 4)
    cand = suite(40, [1.0] * 3, hard_on={"S07": ["PHI_BEFORE_VERIFY"]})
    r = gate(base, cand, SPECS)
    assert r.verdict == REJECT and r.new_violations == {"S07": ["PHI_BEFORE_VERIFY"]}


def test_a_violation_the_baseline_already_had_is_not_new():
    base = suite(40, [1.0] * 4, hard_on={"S07": ["SCOPE"]})
    cand = suite(40, [1.0] * 3, hard_on={"S07": ["SCOPE"]})
    r = gate(base, cand, SPECS)
    assert r.verdict == PROMOTE and r.rules[0].status == "same"


def test_significant_safety_drop_rejects():
    base = suite(40, [1.0] * 4)
    cand = {**suite(40, [1.0] * 3), **{f"S{i:02d}": calls([0.0, 0.0, 1.0]) for i in range(12)}}
    r = gate(base, cand, SPECS)
    assert r.verdict == REJECT
    assert any("significantly lower" in x for x in r.reasons) and any("consistent misses" in x for x in r.reasons)


def test_a_single_flaky_miss_does_not_reject():
    base = suite(40, [1.0] * 4)
    cand = {**suite(40, [1.0] * 3), "S03": calls([1.0, 0.0, 1.0])}       # 1 miss in 3: could be noise
    r = gate(base, cand, SPECS, noise={"recall": 0.05})
    assert r.verdict == PROMOTE
    r = gate(base, cand, SPECS, noise={"recall": 0.0})                     # no noise floor: a drop holds
    assert r.verdict == HOLD


def test_quality_drop_holds_not_rejects():
    base = suite(40, [1.0] * 4, coverage=0.8)
    cand = suite(40, [1.0] * 3, coverage=0.4)
    r = gate(base, cand, SPECS)
    assert r.verdict == HOLD and "Coverage" in r.reasons[0]


def test_aa_split_and_noise_floor():
    """A flaky scenario in each half: the baseline must not reject itself over one miss in two replays."""
    base = suite(40, [1.0] * 4)
    base["S03"] = calls([1.0, 0.0, 1.0, 1.0])                              # miss in the first half
    base["S07"] = calls([1.0, 1.0, 0.0, 1.0])                              # miss in the second half
    a, b = split_aa(base)
    assert all(len(v) == 2 for v in a.values()) and all(len(v) == 2 for v in b.values())
    aa = gate(a, b, SPECS)
    assert aa.verdict == PROMOTE and not aa.metrics[0].new_misses
    floor = noise_floor(aa)
    assert floor["recall"] > 0 and floor["coverage"] == 0


def test_new_consistent_miss_needs_two_failed_replays():
    base = suite(40, [1.0] * 4)
    two_of_two = {**suite(40, [1.0, 1.0]), "S05": calls([0.0, 0.0])}
    assert gate(base, two_of_two, SPECS).metrics[0].new_misses == ["S05"]
    one_of_two = {**suite(40, [1.0, 1.0]), "S05": calls([0.0, 1.0])}
    assert gate(base, one_of_two, SPECS).metrics[0].new_misses == []


def test_ledger_is_append_only_and_deduplicates(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    ledger = Ledger(tmp_path / "ledger.json", tmp_path / "ledger.md", audit)
    assert ledger.record("C1", "short calls", "fp1", "C0", "REJECT", ["new misses"], {}, {"C0": 4, "C1": 3})
    assert ledger.record("C1", "short calls", "fp1", "C0", "REJECT", ["new misses"], {}, {"C0": 4, "C1": 3}) is None
    assert ledger.record("C1", "short calls v2", "fp2", "C0", "PROMOTE", [], {}, {"C0": 4, "C1": 3})
    entries = ledger.load()
    assert [e["id"] for e in entries] == ["L-001", "L-002"]
    assert "| **REJECT** |" in (tmp_path / "ledger.md").read_text(encoding="utf-8")
    assert verify(tmp_path / "audit.jsonl")[0]


# ---------------------------------------------------------------- gate v2 (after the first full A/A)

def test_rule_the_baseline_breaks_sometimes_needs_a_significantly_higher_rate():
    """First full run: the baseline breaks SCOPE at random in ~2% of calls; one more slip is not a regression."""
    base = suite(100, [1.0] * 4, hard_on={"S01": ["SCOPE"], "S50": ["SCOPE"]})
    for sid in ("S01", "S50"):
        base[sid] = calls([1.0] * 4)
        base[sid][0]["hard"] = ["SCOPE"]                        # 1 of 4 replays: sporadic, like the real baseline
    same_rate = suite(100, [1.0] * 3)
    same_rate["S77"][1]["hard"] = ["SCOPE"]
    r = gate(base, same_rate, SPECS)
    assert r.verdict == PROMOTE and r.rules[0].status == "same"
    much_more = suite(100, [1.0] * 3)
    for i in range(20):
        much_more[f"S{i:02d}"][0]["hard"] = ["SCOPE"]
    r = gate(base, much_more, SPECS)
    assert r.verdict == REJECT and r.rules[0].status == "more_often"


def test_scenarios_flaky_in_the_full_baseline_are_not_new_misses():
    """First full run: AMI-12 passed 2 of 4 baseline replays; the A/A half that saw 2 passes must not call it stable."""
    base = suite(40, [1.0] * 4)
    base["S09"] = calls([1.0, 1.0, 0.0, 0.0])
    profile = baseline_profile(base, SPECS)
    a, b = split_aa(base)
    assert gate(a, b, SPECS, profile=profile).verdict == PROMOTE
    assert "S09" in profile["flaky"]["recall"]


def test_aa_with_many_metrics_does_not_false_alarm_on_one_borderline_metric():
    specs = [MetricSpec(f"m{i}", "quality", f"M{i}") for i in range(7)]
    base = {f"S{i:02d}": [{"metrics": {f"m{j}": 1.0 if (i + j + r) % 9 else 0.0 for j in range(7)}, "hard": []}
                          for r in range(4)] for i in range(60)}
    a, b = split_aa(base)
    assert gate(a, b, specs).verdict == PROMOTE
