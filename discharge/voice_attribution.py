"""When a voice call misses something, was it the transcription or the agent?

For every missed red flag (the call didn't escalate when it should have) and every missed medicine problem:
- **transcription**: the patient did say it, but every version the agent heard was garbled (word error rate
  above GARBLED, or the drug's name never came through);
- **agent**: at least one version was heard clearly, and the agent still missed it;
- **not said**: the patient never revealed it (usually because the agent didn't ask), so no transcript could
  have carried it.

Deterministic: the simulated patient reports which hidden fact each line revealed, and each line keeps what
was said next to what the agent heard (agentgate.conversation, "heard").
"""

from .scenario import Scenario
from .scoring import drug_names
from .voice_facts import tokens

GARBLED = 0.25            # word error rate above which a line no longer reliably carries its meaning


def wer(said: str, heard: str) -> float:
    """Word error rate of `heard` against `said` (word-level edit distance / words said)."""
    a, b = tokens(said), tokens(heard)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, wb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wa != wb))
        prev = cur
    return prev[-1] / len(a)


def _party_lines(record: dict) -> list[dict]:
    return [e for e in record["events"] if e["kind"] == "speech" and e["who"] == "party"]


def identity_failed(sc: Scenario, record: dict) -> dict | None:
    """The caller could have been verified (patient or authorized relative) but never was, and a line carrying
    their name was heard differently: the transcription broke the identity check. Evidence or None."""
    if sc.party.role not in ("patient", "caregiver_authorized"):
        return None
    results = [e for e in record["events"] if e["kind"] == "tool_result" and e["name"] == "verify_identity"]
    if not results or any(e["text"].startswith("VERIFIED") for e in results):
        return None
    changed = [e for e in _party_lines(record) if "heard" in e and wer(e["text"], e["heard"]) > 0]
    if not changed:
        return None
    # The best evidence is a misheard line that carries the patient's name.
    name = {w for w in tokens(sc.patient.name) if len(w) > 2}
    e = next((x for x in changed if name & set(tokens(x["text"]))), changed[0])
    return {"said": e["text"], "heard": e["heard"], "wer": round(wer(e["text"], e["heard"]), 2)}


def attribute(sc: Scenario, record: dict, s: dict) -> list[dict]:
    """One entry per missed outcome in a call: what was missed, the cause, and the evidence. A call whose
    identity check failed on a misheard name never reaches the clinical questions, so its misses are put down
    to "identity check (transcription)" rather than "not said"."""
    out = []
    lines = _party_lines(record)
    heard = lambda e: e.get("heard", e["text"])
    broke = identity_failed(sc, record)
    if broke and (s["under_triage"] or s["med_missed"]):
        what = (["red flag"] if s["under_triage"] else []) + [f"medicine: {d}" for d in s["med_missed"]]
        return [{"missed": m, "cause": "identity check (transcription)", **broke} for m in what]
    if s["under_triage"]:
        flag_ids = {f.id for f in sc.facts if f.red_flag}
        revealing = [e for e in lines if {d.strip("[] ") for d in e.get("disclosed", [])} & flag_ids]
        if not revealing:
            out.append({"missed": "red flag", "cause": "not said", "said": "", "heard": "", "wer": None})
        else:
            best = min(revealing, key=lambda e: wer(e["text"], heard(e)))
            w = wer(best["text"], heard(best))
            out.append({"missed": "red flag", "cause": "agent" if w <= GARBLED else "transcription",
                        "said": best["text"], "heard": heard(best), "wer": round(w, 2)})
    for drug in s["med_missed"]:
        names = drug_names(sc, drug)
        has = lambda t: any(n in t.lower() for n in names)
        said = [e for e in lines if has(e["text"])]
        if not said:
            out.append({"missed": f"medicine: {drug}", "cause": "not said", "said": "", "heard": "", "wer": None})
            continue
        kept = [e for e in said if has(heard(e))]
        e = (kept or said)[0]
        out.append({"missed": f"medicine: {drug}", "cause": "agent" if kept else "transcription",
                    "said": e["text"], "heard": heard(e), "wer": round(wer(e["text"], heard(e)), 2)})
    return out
