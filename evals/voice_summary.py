"""The voice evidence in one place, for the report and the validation pack. Reads saved results only (the
voice benchmark, the voice calls and the workflow calls); nothing is re-run and nothing is spent.

    summary() -> dict | None        # None until the voice benchmark or the voice calls have been run
"""

import json

from discharge import config
from evals import voice, voice_calls

SPEECH = ["negation", "sound_alike", "number", "late_symptom", "correction", "spanish", "control"]
IDENTITY = ["name", "dob", "name_decoy"]
CATEGORY = {
    "negation": "Negations (\"I have not taken...\")", "sound_alike": "Sound-alike drug names",
    "number": "Numbers and doses", "late_symptom": "A symptom mentioned late", "correction": "Corrections mid-sentence",
    "spanish": "Spanish", "control": "Plain control lines", "name": "Patient names", "dob": "Dates of birth",
    "name_decoy": "A stranger with a similar name (must not become the patient)",
}
LINE = {
    "clear": "Clear audio", "event_tags": "Clear audio, sound tags on (the default)", "phone": "Clean phone line",
    "phone_noisy": "Noisy phone line (15 dB, 3% lost)", "phone_harsh": "Harsher phone line (10 dB, 5% lost)",
    "clear_keyterms": "Clear audio, names hinted", "phone_keyterms": "Clean phone line, names hinted",
}
METRICS = ["red_flag_recall", "emergency_recall", "coverage", "med_catch"]


def _load(name: str) -> dict | None:
    p = config.RESULTS_DIR / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _benchmark(b: dict) -> dict:
    rows = b["lines"]
    speech = voice.summarize([r for r in rows if r["category"] in SPEECH])
    full = b["summary"]
    lines = []
    for g in ("clear", "phone", "phone_noisy", "phone_harsh", "event_tags"):
        s = speech.get(g, {}).get("all")
        if s:
            lines.append({"id": g, "label": LINE[g], "kept": s["kept"], "n": s["n"], "samples": s["samples"],
                          "lost": len(s["lost_vs_clear"]), "gained": len(s["gained_vs_clear"]), "p": s["p_vs_clear"]})
    by_category = [{"category": c, "label": CATEGORY[c],
                    **{g: [full[g][c]["kept"], full[g][c]["n"]] for g in ("clear", "phone", "phone_noisy", "phone_harsh")
                       if c in full.get(g, {})}} for c in SPEECH]
    names = [{"category": c, "label": CATEGORY[c],
              **{g: [full[g][c]["kept"], full[g][c]["n"]] for g in ("clear", "phone", "clear_keyterms", "phone_keyterms")
                 if c in full.get(g, {})}} for c in IDENTITY]
    return {"run_at": b["run_at"], "lines": len({r["id"] for r in rows}), "voices": list(b["voices"].values()),
            "speech_model": b["tts_model"], "transcription_model": b["stt_model"],
            "conditions": lines, "by_category": by_category, "names": names,
            "keyterms_p": full.get("clear_keyterms", {}).get("name", {}).get("p_vs_clear")}


def _calls(v: dict) -> dict:
    out = []
    for cid, c in v["candidates"].items():
        m = {x["name"]: x for x in c["metrics"]}
        out.append({"id": cid, "description": c["description"], "verdict": c["verdict"], "expected": c["expected"],
                    "calls": c["calls"], "identity_failed": c.get("identity_failed_on_misheard_name"),
                    "attribution": c["attribution"], "lines": c["lines"], "credits": c.get("credits"),
                    "metrics": [{"name": n, "label": m[n]["label"], "base": m[n]["base"], "cand": m[n]["cand"]}
                                for n in METRICS if n in m],
                    "reasons": c["reasons"][:3]})
    return {"run_at": v["run_at"], "k": v["k"], "scenarios": v["scenarios"], "baseline": "C0 in text",
            "candidates": out}


def _workflows(w: dict) -> dict:
    return {"k": w["k"], "candidates": [{"id": cid, "description": c["description"],
                                         "human": [c["rates"]["human"]["passed"], c["rates"]["human"]["n"]],
                                         "callback": [c["rates"]["callback"]["passed"], c["rates"]["callback"]["n"]]}
                                        for cid, c in w["candidates"].items()]}


def summary() -> dict | None:
    b, v, w = _load("voice_benchmark.json"), _load("voice_calls.json"), _load("workflows.json")
    if not b and not v:
        return None
    credits = sum(c.get("credits") or 0 for c in (v or {}).get("candidates", {}).values())
    return {"benchmark": _benchmark(b) if b else None, "calls": _calls(v) if v else None,
            "workflows": _workflows(w) if w else None, "call_credits": credits,
            "playback": list(voice_calls.PLAYBACK)}
