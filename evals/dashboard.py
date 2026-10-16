"""Build the results page: evals/results/dashboard.html (one self-contained file, no server). No API calls.

    python -m evals.dashboard

Reads the cached calls, gate.json, curve.json, the judge checks and the labels, and embeds them as
JSON in evals/dashboard_template.html. Open the output in a browser. Transcripts are included for
replay 0 of every call, plus every replay that missed a red flag or broke a rule.
"""

import json
from collections import defaultdict

from discharge import config, judge
from discharge.candidates import candidates
from discharge.conditions import CONDITIONS, red_flag
from discharge.scenario import load_scenarios
from evals.experiments import PLAN, scenarios_for
from evals.flag_categories import summary as flag_summary
from evals.label import FLAG_LABELS, agreement
from evals.run import SUITES, evaluate

TEMPLATE = config.ROOT / "evals" / "dashboard_template.html"
OUT = config.RESULTS_DIR / "dashboard.html"
ARTIFACT = config.RESULTS_DIR / "dashboard_artifact.html"
ORDER = ["C0", "C1", "C3", "C4", "C2"]


def _load(name: str):
    p = config.RESULTS_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def scenario_card(sc) -> dict:
    facts = []
    for f in sc.facts:
        d = {"id": f.id, "domain": f.domain, "phrasing": f.phrasing, "disclosure": f.disclosure,
             "truth": f.truth, "red_flag": f.red_flag}
        if f.red_flag:
            d["severity"] = red_flag(f.red_flag)["severity"]
        facts.append(d)
    return {
        "id": sc.id, "block": sc.block, "condition": CONDITIONS[sc.condition], "jurisdiction": sc.jurisdiction,
        "party": sc.party.model_dump(), "patient": {"name": sc.patient.name, "age": sc.patient.age,
                                                    "sex": sc.patient.sex, "language": sc.patient.language,
                                                    "literacy": sc.patient.literacy, "hearing": sc.patient.hearing},
        "discharge_date": sc.discharge_date, "call_date": sc.today(), "part2": sc.part2_record,
        "discharge_meds": [m.to_med().line() for m in sc.discharge_meds],
        "home_meds": [{"line": m.to_med().line()} for m in sc.home_meds],
        "facts": facts, "events": [e.model_dump() for e in sc.events],
        "expected": sc.expected.model_dump(), "tags": sc.tags,
    }


def call_summary(entry: dict, s: dict) -> dict:
    return {"r": entry["r"], "expected": s["disposition_expected"], "got": s["disposition_got"],
            "ok": s["escalation_ok"], "under": s["under_triage"], "false_esc": s["false_escalation"],
            "heard": s["escalated_for_reason"], "elicited": s["red_flags_elicited"],
            "med_expected": s["med_expected"], "med_caught": s["med_caught"], "med_missed": s["med_missed"],
            "med_false": s["med_false_flags"], "ended_on_911": s["ended_on_emergency"],
            "violations": s["violations"], "told": s["told_next_step"], "crisis": s["crisis_line"],
            "coverage": s["coverage"], "ended_by": entry["record"]["ended_by"],
            "turns": entry["record"]["agent_turns"], "cost": entry["cost_usd"]}


def transcript(entry: dict, s: dict) -> list[dict]:
    marks = defaultdict(list)
    events = entry["record"]["events"]
    for v in s["violation_detail"]:
        idx = v.get("event")
        if idx is None and v.get("text"):
            quote = v["text"][:60]
            idx = next((i for i, e in enumerate(events) if e["kind"] == "speech" and e["who"] == "agent"
                        and quote and quote in e["text"]), None)
        if idx is not None:
            marks[idx].append({"rule": v["rule"], "evidence": v.get("evidence", ""), "quote": v.get("text", "")})
    out = []
    for i, e in enumerate(events):
        if e["kind"] == "speech":
            row = {"k": "s", "w": "a" if e["who"] == "agent" else "p", "t": e["text"]}
            if e.get("disclosed"):
                row["rev"] = [d.strip("[] ") for d in e["disclosed"]]
        elif e["kind"] == "tool_call":
            row = {"k": "c", "n": e["name"], "i": e["input"]}
        elif e["kind"] == "tool_result":
            row = {"k": "r", "n": e["name"], "t": e["text"]}
        else:
            row = {"k": "e", "t": e.get("text", "")}
        if marks.get(i):
            row["v"] = marks[i]
        out.append(row)
    return out


def build_data(with_transcripts: bool = True) -> dict:
    """Everything the results page and the console show. The console fetches transcripts per call,
    so it passes with_transcripts=False."""
    suite = load_scenarios(SUITES["suite"])
    gate = _load("gate.json") or {}
    curve = _load("curve.json") or {}
    cands = candidates()
    data = {"candidates": [], "scenarios": {}, "calls": {}, "transcripts": {}, "gate": gate, "curve": curve,
            "ledger": _load("ledger.json") or [], "judge_check": _load("judge_check.json"),
            "judge_agreement": agreement(), "findings": [], "cost": {}}
    by = {}
    for step in PLAN:
        rows = evaluate(step.cand, scenarios_for(step, suite), step.k)
        by[step.cand] = rows
        data["candidates"].append({"id": step.cand, "description": cands[step.cand].description, "k": step.k,
                                   "scenarios": len({sc.id for sc, _, _ in rows}), "calls": len(rows),
                                   "why": step.why})
        data["cost"][step.cand] = round(sum(e["cost_usd"] for _, e, _ in rows), 3)
        for sc, e, s in rows:
            data["scenarios"].setdefault(sc.id, scenario_card(sc))
            data["calls"].setdefault(step.cand, {}).setdefault(sc.id, []).append(call_summary(e, s))
            bad = (s["disposition_expected"] in ("same_day_clinician", "emergency_now") and not s["escalation_ok"]) \
                or s["violations"] or s["told_next_step"] is False or s["crisis_line"] is False
            if with_transcripts and (e["r"] == 0 or bad):
                data["transcripts"][f"{step.cand}|{sc.id}|{e['r']}"] = transcript(e, s)

    if FLAG_LABELS.exists():
        data["judge_flags"] = flag_summary(json.loads(FLAG_LABELS.read_text(encoding="utf-8")))

    # Findings: picked from the data, each opens a real call.
    def first(cand, pred):
        return next(((sc, e, s) for sc, e, s in by.get(cand, []) if pred(sc, s)), None)

    f = first("C3", lambda sc, s: "CAREGIVER_SCOPE" in s["violations"]) or \
        first("C3", lambda sc, s: "PHI_BEFORE_VERIFY" in s["violations"])
    if f:
        data["findings"].append({"cand": "C3", "sid": f[0].id, "r": f[1]["r"], "compare": True,
                                 "title": "A warmer greeting told a relative why the hospital was calling",
                                 "body": "The change asked the agent to greet patients by name and mention their stay. "
                                         "Escalation stayed perfect, but on most calls the agent named the condition "
                                         "before checking who had picked up."})
    misses = next((m["new_misses"] for m in gate.get("C1", {}).get("metrics", []) if m["name"] == "emergency_recall"), [])
    f = first("C1", lambda sc, s: sc.id in misses and not s["escalation_ok"])
    if f:
        data["findings"].append({"cand": "C1", "sid": f[0].id, "r": f[1]["r"], "compare": True,
                                 "title": "Shorter calls stopped asking about the warning signs",
                                 "body": "Told to keep calls under three minutes, the agent asked \"how are you?\" and "
                                         "moved on. Patients who only mention a symptom when asked about it were never "
                                         "asked, and emergencies were missed."})
    f = first("C0", lambda sc, s: s["ended_on_emergency"] and "clopidogrel" in s["med_expected"]) or \
        first("C0", lambda sc, s: s["ended_on_emergency"])
    if f:
        data["findings"].append({"cand": "C0", "sid": f[0].id, "r": f[1]["r"], "compare": False,
                                 "title": "Sent to 911, but the medicine problem never reached the team",
                                 "body": "The current agent handles emergencies well, then hangs up. In "
                                         f"{sum(s['ended_on_emergency'] for _, _, s in by['C0'])} of {len(by['C0'])} "
                                         "calls a planted medicine problem was never reported, such as a stopped blood "
                                         "thinner that likely caused the chest pain."})
    f = first("C0", lambda sc, s: s["crisis_line"] is False)
    if f:
        data["findings"].append({"cand": "C0", "sid": f[0].id, "r": f[1]["r"], "compare": False,
                                 "title": "A patient mentioned suicidal thoughts and heard nothing back",
                                 "body": "The agent escalated and transferred the call correctly, but said nothing: no "
                                         "988, no \"I'm connecting you to a nurse\". Caught by a human reviewer, now "
                                         "a safety check of its own."})
        for sc, e, s in by["C0"]:
            if with_transcripts and sc.id == f[0].id:
                data["transcripts"].setdefault(f"C0|{sc.id}|{e['r']}", transcript(e, s))
    return data


def main() -> None:
    data = build_data()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    body = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    # Content-only version for publishing as a claude.ai artifact (the host adds <html>/<head>/<body>).
    ARTIFACT.write_text(body, encoding="utf-8")
    # Full document for opening locally in any browser.
    head, rest = body.split('<div class="page">', 1)
    OUT.write_text('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                   '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                   f'{head}</head>\n<body>\n<div class="page">{rest}\n</body>\n</html>\n', encoding="utf-8")
    print(f"dashboard -> {OUT}  ({len(body) / 1e6:.1f} MB, {len(data['transcripts'])} transcripts, "
          f"{len(data['scenarios'])} scenarios)")


if __name__ == "__main__":
    main()
