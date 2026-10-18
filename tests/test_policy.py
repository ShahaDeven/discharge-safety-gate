"""Clinical policy: which judge labels count, extensions of state rules, storage, and applying without calls.
Offline: fake labels and fake gate results, temp folders for anything written."""

import os

os.environ.setdefault("DSG_TOKEN", "test-token-0123456789")     # before web.security reads it at import

import pytest
from fastapi.testclient import TestClient

from discharge import clinical_policy, config, judge
from discharge.clinical_policy import DEFAULT, LOCKED, STRICT, Policy
from discharge.scoring import compliance, score
from evals import policy as policies
from web import security
from web.api import app

from conftest import event_speech

TOKEN = {"X-DSG-Token": security.TOKEN}


def record(events):
    return {"events": events, "ended_by": "end_call", "agent_turns": 1, "usage": []}


def labels(*cats):
    return {"flags": [{"line": i + 1, "category": c, "quote": f"line {i + 1}"} for i, c in enumerate(cats)]}


def rules(sc, j, pol):
    return [v["rule"] for v in judge.judged_violations(sc, j, pol)]


@pytest.fixture
def ny_psy(suite):
    return next(s for s in suite.values() if s.condition == "PSY" and s.jurisdiction == "NY")


@pytest.fixture
def suite():
    from discharge.scenario import load_scenarios
    return {s.id: s for s in load_scenarios(config.SCENARIO_DIR / "suite.jsonl")}


# ---------------------------------------------------------------- what a policy counts

def test_dosing_advice_and_diagnosis_can_never_be_switched_off():
    loose = Policy("Loose", frozenset())
    assert LOCKED <= loose.scope == LOCKED
    with pytest.raises(ValueError):
        Policy("Typo", frozenset({"sign-of"}))


def test_defaults_follow_the_line_by_line_review():
    assert {"sign_of", "said_normal"} <= DEFAULT.scope
    assert not {"reassurance", "hold_off"} & DEFAULT.scope


def test_switches_decide_which_labels_count(pilot):
    sc = pilot["P01"]
    j = labels("sign_of", "hold_off", "reassurance")
    assert rules(sc, j, DEFAULT) == ["SCOPE"]                                 # only "sign of" counts by default
    assert rules(sc, j, Policy("Allow sign of", DEFAULT.scope - {"sign_of"})) == []
    assert rules(sc, j, STRICT) == ["SCOPE"] * 3
    assert score(sc, record([]), j, STRICT)["violations"].count("SCOPE") == 3


def test_fingerprint_follows_the_switches_not_the_name():
    a = Policy("A", frozenset({"hold_off"}))
    assert a.fingerprint == Policy("B", frozenset({"hold_off"})).fingerprint != DEFAULT.fingerprint


# ---------------------------------------------------------------- stricter than the law, never looser

def test_therapy_rule_can_be_extended_to_every_state(ny_psy):
    j = labels("therapy")
    assert rules(ny_psy, j, DEFAULT) == []                                    # the law: Illinois only
    assert rules(ny_psy, j, Policy("Everywhere", DEFAULT.scope, frozenset({"no_therapy_everywhere"}))) == ["NO_THERAPY"]


def test_ai_disclosure_can_be_extended_but_not_removed(pilot, suite):
    ny = pilot["P01"]
    events = [event_speech("agent", "Hello, this is Lakeside General calling for Rosa.")]
    everywhere = Policy("Everywhere", DEFAULT.scope, frozenset({"ai_disclosure_everywhere"}))
    assert "AI_DISCLOSURE" not in [v["rule"] for v in compliance(ny, events, DEFAULT)]
    assert "AI_DISCLOSURE" in [v["rule"] for v in compliance(ny, events, everywhere)]
    ca = next(s for s in suite.values() if s.jurisdiction == "CA")
    loosest = Policy("Loosest", frozenset(), frozenset())
    assert "AI_DISCLOSURE" in [v["rule"] for v in compliance(ca, events, loosest)]   # the law still applies


# ---------------------------------------------------------------- storage

def test_active_policy_is_saved_with_its_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    assert clinical_policy.active() == DEFAULT                               # nothing applied yet
    clinical_policy.set_active(Policy("Cardiology", frozenset({"hold_off"})), note="test", owner="t1")
    pol = clinical_policy.active()
    assert pol.name == "Cardiology" and "hold_off" in pol.scope and LOCKED <= pol.scope
    assert [h["name"] for h in clinical_policy.history()] == ["Cardiology"]


# ---------------------------------------------------------------- applying places no calls

def test_apply_regates_from_cache_without_placing_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(config, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(policies.gate, "OUT", tmp_path / "gate.json")
    (tmp_path / "gate.json").write_text('{"C1": {"verdict": "PROMOTE"}}', encoding="utf-8")
    monkeypatch.setattr(policies.gate, "run_all", lambda pol=None, **kw: {
        "policy": pol.to_dict(), "A/A": {"verdict": "PROMOTE"}, "C1": {"verdict": "REJECT", "reasons": [], "metrics": []}})
    monkeypatch.setattr(policies.gate, "record", lambda *a: None)
    rec = {"id": "P-001", "name": "Proposal", "status": "done", "result": {"verdict": "PROMOTE", "visible": {"x": 1}}}
    monkeypatch.setattr(policies.proposals, "all_proposals", lambda: [rec])
    monkeypatch.setattr(policies.proposals, "candidate", lambda r: "cand")
    monkeypatch.setattr(policies.agents, "all_agents", lambda: [])
    seen = []

    def fake_gate(kind, r, cand, max_usd, progress, place_calls=True):
        seen.append((max_usd, place_calls))
        return {"verdict": "HOLD"}
    monkeypatch.setattr(policies.proposals, "gate_candidate", fake_gate)
    out = policies.apply(Policy("Strict", STRICT.scope), note="test")
    assert seen == [(0.0, False)]                                             # no calls, no spend
    assert {(c["id"], c["before"], c["after"]) for c in out["changes"]} == {("C1", "PROMOTE", "REJECT"),
                                                                           ("P-001", "PROMOTE", "HOLD")}
    assert clinical_policy.active().name == "Strict"
    assert "policy_applied" in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


# ---------------------------------------------------------------- API

@pytest.fixture
def client():
    return TestClient(app, base_url="http://localhost:8001")


def test_policy_input_is_checked_and_applying_needs_the_token(client):
    assert client.post("/api/policy/preview", json={"scope": ["not_a_category"]}).status_code == 422
    assert client.post("/api/policy/preview", json={"scope": [], "evil": 1}).status_code == 422
    assert client.post("/api/policy", json={"name": "No token", "scope": []}).status_code == 401
    assert client.post("/api/policy", json={"name": "<b>x</b>", "scope": []}, headers=TOKEN).status_code == 422


def test_read_only_mode_cannot_apply_a_policy(client, monkeypatch):
    monkeypatch.setattr(security, "READ_ONLY", True)
    assert client.post("/api/policy", json={"name": "Read only", "scope": []}, headers=TOKEN).status_code == 403
