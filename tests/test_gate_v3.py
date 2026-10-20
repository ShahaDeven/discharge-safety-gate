"""Gate v3: scenario-level rule test, INCOMPLETE, and the edges a release gate must not get wrong. Offline."""

import json

import pytest

from agentgate.gate import HOLD, INCOMPLETE, PROMOTE, REJECT, MetricSpec, gate
from agentgate.stats import bootstrap_test, fisher_exact, sign_flip_test
from discharge import config
from discharge.scenario import load_scenarios
from discharge.scoring import score
from evals import proposals

SPECS = [MetricSpec("recall", "safety", "Recall"), MetricSpec("coverage", "quality", "Coverage")]


def calls(n: int, recall: float = 1.0, hard_on: tuple = ()) -> list[dict]:
    """n replays; replay i breaks SCOPE if i is in hard_on."""
    return [{"metrics": {"recall": recall, "coverage": 0.8}, "hard": ["SCOPE"] if i in hard_on else []} for i in range(n)]


def suite(n: int, k: int, hard: dict | None = None) -> dict:
    hard = hard or {}
    return {f"S{i:02d}": calls(k, hard_on=hard.get(f"S{i:02d}", ())) for i in range(n)}


# ---------------------------------------------------------------- scenario-level rule test

def test_replays_of_one_scenario_are_not_independent_evidence():
    """A rule broken on every replay of 2 scenarios: 8 calls, but 2 pieces of evidence."""
    base = suite(20, 4, {"S10": (0,)})                                    # baseline breaks SCOPE once
    cand = suite(20, 4, {"S00": (0, 1, 2, 3), "S01": (0, 1, 2, 3)})
    assert fisher_exact(8, 72, 1, 79) < 0.05                              # the old call-level test would reject
    r = gate(base, cand, SPECS)
    assert r.rules[0].status == "same" and r.verdict == PROMOTE
    assert r.rules[0].lo <= 0 < r.rules[0].hi


def test_the_same_rate_spread_across_scenarios_still_rejects():
    base = suite(20, 4, {"S10": (0,)})
    cand = suite(20, 4, {f"S{i:02d}": (0,) for i in range(14)})          # once on each of 14 scenarios
    r = gate(base, cand, SPECS)
    assert r.verdict == REJECT and r.rules[0].status == "more_often"
    assert "scenario by scenario" in r.reasons[0]


def test_bootstrap_test_basics():
    assert bootstrap_test([]) == (0.0, 0.0, 0.0, 1.0)
    assert bootstrap_test([0.0] * 10)[3] == 1.0                            # nothing moved
    mean, lo, hi, p = bootstrap_test([0.5] * 10 + [0.25] * 10)
    assert lo > 0 and p == 0.0
    mean, lo, hi, p = bootstrap_test([0.5, -0.5] * 10)
    assert lo < 0 < hi and p > 0.5


def test_sign_flip_test_is_honest_with_rare_events():
    assert sign_flip_test([0.0] * 10) == 1.0
    assert sign_flip_test([0.25] * 5 + [0.0] * 50) == 2 / 32                # 5 scenarios: never below 0.0625
    assert sign_flip_test([0.25] * 13 + [0.0] * 40) < 0.01                 # 13 all one way: clear
    assert sign_flip_test([0.25, -0.25] * 8) == 1.0                        # balanced: no evidence
    # The case that made the bootstrap the wrong choice: few moved scenarios, all one way.
    rare = [1 / 3] * 4 + [1 / 3 - 1 / 4] + [0.0] * 97
    assert bootstrap_test(rare, alpha=0.025)[1] > 0 and sign_flip_test(rare) > 0.025


def test_a_rule_never_broken_before_still_rejects_on_one_call():
    base = {f"S{i:02d}": [{"metrics": {"recall": 1.0, "coverage": 0.8}, "hard": []}] * 4 for i in range(20)}
    cand = {sid: [dict(c) for c in v] for sid, v in base.items()}
    cand["S03"] = [{"metrics": {"recall": 1.0, "coverage": 0.8}, "hard": ["PHI_BEFORE_VERIFY"]}] + cand["S03"][1:]
    r = gate(base, cand, SPECS)
    assert r.verdict == REJECT and r.rules[0].status == "new_kind"


# ---------------------------------------------------------------- INCOMPLETE

def test_a_missing_scenario_is_incomplete_not_promote():
    base, cand = suite(20, 4), suite(19, 3)                                # S19 never ran for the candidate
    r = gate(base, cand, SPECS, expected=[f"S{i:02d}" for i in range(20)], min_k=(4, 3))
    assert r.verdict == INCOMPLETE and "S19" in r.reasons[0]
    assert gate(base, cand, SPECS).verdict == PROMOTE                     # without a plan: the old, silent behaviour


def test_too_few_replays_is_incomplete():
    base, cand = suite(20, 4), suite(20, 3)
    cand["S05"] = cand["S05"][:1]
    r = gate(base, cand, SPECS, expected=sorted(base), min_k=(4, 3))
    assert r.verdict == INCOMPLETE and "S05" in r.reasons[0]


def test_harm_already_found_rejects_even_when_incomplete():
    base = suite(20, 4)
    cand = suite(19, 3)
    cand["S02"][0]["hard"] = ["PHI_BEFORE_VERIFY"]
    r = gate(base, cand, SPECS, expected=sorted(base), min_k=(4, 3))
    assert r.verdict == REJECT
    assert any(x.startswith("Incomplete") for x in r.reasons)              # and it still says what's missing


def test_nothing_to_compare_is_incomplete():
    assert gate(suite(20, 4), {}, SPECS).verdict == INCOMPLETE
    assert gate(suite(5, 4), {f"S{i:02d}": [] for i in range(5)}, SPECS).verdict == INCOMPLETE


def test_complete_run_is_unchanged_by_the_check():
    base, cand = suite(20, 4), suite(20, 3)
    assert gate(base, cand, SPECS, expected=sorted(base), min_k=(4, 3)).verdict == PROMOTE


# ---------------------------------------------------------------- verdict semantics for proposals and agents

@pytest.mark.parametrize("visible,sealed,failed,want", [
    ("PROMOTE", "PROMOTE", False, PROMOTE),
    ("PROMOTE", "HOLD", False, HOLD),              # passes what it saw, not the sealed set
    ("HOLD", "PROMOTE", False, HOLD),
    ("PROMOTE", "INCOMPLETE", False, INCOMPLETE),
    ("PROMOTE", "PROMOTE", True, INCOMPLETE),      # failed or unlabeled calls
    ("REJECT", "INCOMPLETE", True, REJECT),        # harm found wins
    ("PROMOTE", "REJECT", False, REJECT),
])
def test_combined_verdicts(visible, sealed, failed, want):
    assert proposals.combine(visible, sealed, failed) == want


# ---------------------------------------------------------------- edges in the inputs

def test_duplicate_scenario_ids_are_refused(tmp_path):
    line = (config.SCENARIO_DIR / "suite.jsonl").read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "dupes.jsonl"
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate scenario ids"):
        load_scenarios(path)


def test_a_call_with_no_events_scores_as_a_miss_not_a_crash():
    sc = next(s for s in load_scenarios(config.SCENARIO_DIR / "suite.jsonl")
              if s.expected.disposition in ("same_day_clinician", "emergency_now"))
    s = score(sc, {"events": [], "ended_by": "agent_error", "agent_turns": 0, "usage": []},
              {"flags": []})
    assert s["escalation_ok"] is False and s["under_triage"] is True
    json.dumps(s)                                                          # serializable for the cache and the API
