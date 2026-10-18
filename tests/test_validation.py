"""Validation pack: intervals, absolute rates, subgroups, and what the API will build a pack for.
Offline: a hand-made overview, nothing read from cached results."""

import os

os.environ.setdefault("DSG_TOKEN", "test-token-0123456789")     # before web.security reads it at import

import pytest
from fastapi.testclient import TestClient

from discharge import config
from evals import validation
from web.api import app


def test_wilson_interval_behaves_at_the_edges():
    assert validation.wilson(0, 0) == (0.0, 0.0)
    lo, hi = validation.wilson(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35
    lo, hi = validation.wilson(10, 10)
    assert hi == 1.0 and 0.65 < lo < 1
    lo, hi = validation.wilson(50, 100)
    assert lo < 0.5 < hi and hi - lo < 0.2


def call(ok=True, expected="same_day_clinician", violations=(), med_expected=(), med_missed=(), ended_on_911=False):
    return {"ok": ok, "expected": expected, "false_esc": False, "violations": list(violations), "told": True,
            "crisis": None, "med_expected": list(med_expected), "med_missed": list(med_missed), "ended_on_911": ended_on_911}


def scenario(block="clinical", disclosure="implicit", source=None, persona=None, disposition="same_day_clinician"):
    tags = {"disclosure": disclosure, **({"source": source, "persona": persona} if source else {})}
    return {"block": block, "condition": "HF", "jurisdiction": "NY", "party": {"role": "patient"}, "tags": tags,
            "expected": {"disposition": disposition}}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(validation.policies, "reviewer_agreement", lambda: None)
    monkeypatch.setattr(validation.label, "agreement", lambda: {})
    gate = {"verdict": "PROMOTE", "reasons": [], "version": 2, "scenarios": 2, "metrics": [], "rules": []}
    return {
        "candidates": [{"id": "C0", "description": "Baseline", "k": 2, "calls": 4}, {"id": "C1", "description": "Change", "k": 2, "calls": 4}],
        "scenarios": {"S1": scenario(), "F1": scenario("fairness", source="S1", persona="Spanish")},
        "calls": {
            "C0": {"S1": [call(), call()], "F1": [call(), call()]},
            "C1": {"S1": [call(), call(med_expected=["x"], med_missed=["x"], ended_on_911=True)],
                   "F1": [call(ok=False), call(violations=["SCOPE"])]},
        },
        "gate": {"A/A": {**gate, "description": "C0 vs itself"}, "C1": {**gate, "verdict": "HOLD", "reasons": ["why"]}},
        "ledger": [{"id": "L-1", "candidate": "C1", "baseline": "C0", "verdict": "HOLD", "reasons": []}],
        "judge_check": None, "redteam": None,
        "policy": {"name": "Reviewer policy", "fingerprint": "abc", "scope": ["dosing_advice"], "extend": []},
    }


def test_pack_reports_absolute_rates_and_the_fairness_pair(data):
    v = validation.build(data, "C1")
    assert v["verdict"]["verdict"] == "HOLD" and not v["verdict"]["baseline"]
    rates = {a["label"]: (a["k"], a["n"]) for a in v["performance"]["absolute"]}
    assert rates["Escalated when it should have (red flag present)"] == (3, 4)
    assert rates["Call with no rule violation"] == (3, 4)
    assert "Caught the seeded medicine problem, when the call reached the medicine review" not in rates   # ended on 911
    assert rates["Caught it on every call with one (calls that ended on a 911 instruction included)"] == (0, 1)
    pair = v["subgroups"]["repeats"][0]
    assert (pair["id"], pair["persona"], pair["variant"], pair["original"]) == ("F1", "Spanish", (0, 2), (2, 2))
    assert [e["id"] for e in v["changes"]] == ["L-1"]
    assert {r["rule"] for r in v["rules"]} >= {"PHI_BEFORE_VERIFY", "SCOPE", "AI_DISCLOSURE", "NO_THERAPY"}


def test_current_agent_pack_uses_the_noise_check(data):
    v = validation.build(data, "C0")
    assert v["verdict"]["baseline"] and v["verdict"]["verdict"] == "PROMOTE"


def test_no_pack_without_a_gate_result(data):
    with pytest.raises(KeyError):
        validation.build(data, "C9")


def test_api_never_builds_a_pack_for_sealed_or_unknown_versions():
    client = TestClient(app, base_url="http://localhost:8001")
    assert client.get("/api/validation/H-01").status_code == 404
    assert client.get("/api/validation/A-999").status_code == 404
