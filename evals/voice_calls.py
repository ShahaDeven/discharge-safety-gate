"""Day 3: voice inside full calls. Does the agent still behave when the patient's words reach it through speech,
a phone line and transcription, and does the gate catch it when they don't?

    python -m evals.voice_calls plan                         # calls and estimated cost per candidate (free)
    python -m evals.voice_calls smoke                        # 2 real calls (VC, VH) to measure true cost
    python -m evals.voice_calls run --only VC,VH             # the comparison; caps on credits and dollars
    python -m evals.voice_calls results --only VC,VH         # recompute verdicts from the cache (free)
    python -m evals.voice_calls show                         # the last results (free)

Every candidate is the same agent as C0; only the input path changes. The baseline is C0's cached TEXT calls,
which asks the real question: does putting a voice pipeline in front of the agent make it less safe?

    VC  Roger -> clean phone line -> transcription        expected PROMOTE (harmless)
    VE  Eric  -> clean phone line -> transcription        expected PROMOTE (a different voice)
    VN  Roger -> noisy line (15 dB, 3% loss)               expected REJECT
    VH  Roger -> harsher line (10 dB, 5% loss)             expected REJECT

The simulated patient hears its own real words; only the agent gets the transcript. Every miss is attributed
to transcription, the agent, or "not said" (discharge/voice_attribution.py). Sound-event tags are off.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone

from agentgate.audit import AuditLog, content_hash
from agentgate.gate import INCOMPLETE, REJECT, baseline_profile, gate, noise_floor, split_aa
from agentgate.replay import Candidate, Replayer, load_cache
from agentgate.voice import elevenlabs
from agentgate.voice.base import VoiceCache, VoiceError
from agentgate.voice.channel import VoiceChannel
from agentgate.voice.phone import phone_line
from discharge import config
from discharge.candidates import candidates, reference
from discharge.gate_metrics import SPECS, group
from discharge.persona import DischargeSuite
from discharge.scenario import load_scenarios
from discharge.voice_attribution import GARBLED, attribute, identity_failed, wer
from evals import proposals
from evals.run import SUITES, evaluate

OUT = config.RESULTS_DIR / "voice_calls.json"
CACHE = VoiceCache(config.CACHE_DIR / "voice")
ROGER, ERIC = "CwhRBWXzGAHq8TQ4Fs17", "cjVigY5qzO86Huf0OWal"
# Where voice should matter most: Spanish speakers, medicine problems named by drug, red flags only hinted at or
# mentioned when asked (ranked on 2026-10-20; voicemail scenarios excluded: nothing to transcribe).
SCENARIOS = ["FAIR-02", "FAIR-01", "FAIR-03", "AMI-05", "AMI-06", "AMI-09", "AMI-11", "COPD-05", "COPD-08",
             "FAIR-04", "FAIR-06", "FAIR-07", "HF-05", "HF-06", "HF-07", "HF-08"]
LINES = {  # name: (description, voice, snr_db, loss, agent prompt)
    "VC": ("Roger, clean phone line", ROGER, None, 0.0, "c0_baseline.md"),
    "VE": ("Eric, clean phone line", ERIC, None, 0.0, "c0_baseline.md"),
    "VN": ("Roger, noisy line (15 dB, 3% loss)", ROGER, 15, 0.03, "c0_baseline.md"),
    "VH": ("Roger, harsher line (10 dB, 5% loss)", ROGER, 10, 0.05, "c0_baseline.md"),
    # The fix for what VC found (identity checks failing on misheard names): same line, C0's prompt plus a
    # section on transcribed calls (ask them to spell their name, verify once more).
    "VF": ("Roger, clean phone line, voice-aware prompt", ROGER, None, 0.0, "c0_voice.md"),
    # Both name fixes (PLAN 13.8): the transcriber is told the names it should expect (patient, medicines,
    # hospital), and the agent spells back names and dates of birth when a check fails.
    "VK": ("Roger, clean phone line, keyterms + voice-aware prompt", ROGER, None, 0.0, "c0_voice.md"),
}
KEYTERMS = {"VK"}
EXPECTED = {"VC": "PROMOTE", "VE": "PROMOTE", "VN": "REJECT", "VH": "REJECT", "VF": "PROMOTE", "VK": "PROMOTE"}


def call_keyterms(ctx) -> list[str]:
    """What the transcriber should expect on this call: the patient's name, the discharge medicines (and their
    brand names), and the hospital. Never the names of other people, so a stranger can't be "heard" as them."""
    terms = [ctx.patient_name, config.ORGANIZATION.split(",")[0]]
    for m in ctx.discharge_meds:
        terms.append(m.name)
    return list(dict.fromkeys(t for t in terms if t))
STT = {"tag_audio_events": False}
CREDITS_PER_CHAR, SECONDS_PER_CHAR, STT_PER_MIN = 0.2, 0.056, 21
CLAUDE_PER_CALL = 0.0031                     # agent + simulated patient + judge, Haiku (measured on C0)


def voice_candidate(cid: str) -> Candidate:
    desc, voice, snr, loss, prompt = LINES[cid]
    c0 = candidates()["C0"] if prompt == "c0_baseline.md" else reference(f"{cid}-agent", desc, prompt)
    line = lambda a, seed: phone_line(a, snr_db=snr, loss=loss, seed=seed)
    hints = cid in KEYTERMS
    make = lambda ctx: VoiceChannel(elevenlabs.ElevenLabsTTS(voice),
                                    elevenlabs.ElevenLabsSTT(**STT, **({"keyterms": call_keyterms(ctx)} if hints else {})),
                                    CACHE, line, cid)
    fp = content_hash([c0.fingerprint, "voice-channel-v1", voice, elevenlabs.TTS_MODEL, elevenlabs.STT_MODEL, STT,
                       "phone", snr, loss] + (["keyterms-v1"] if hints else []))
    who = "C0" if prompt == "c0_baseline.md" else f"C0 + {prompt}"
    return Candidate(cid, f"{who} heard through: {desc}", fp, c0.make_agent, CLAUDE_PER_CALL, make_channel=make)


def scenarios(n: int) -> list:
    by_id = {s.id: s for s in load_scenarios(SUITES["suite"])}
    return [by_id[i] for i in SCENARIOS[:n]]


def _replayer(scs, max_usd: float) -> Replayer:
    return Replayer(DischargeSuite(scs), config.CACHE_DIR, AuditLog(config.AUDIT_PATH),
                    max_agent_turns=config.MAX_AGENT_TURNS, max_usd=max_usd)


def estimate(cids: list[str], scs: list, k: int) -> dict:
    """Calls still to run, Claude dollars, and ElevenLabs credits (from how much patients said in C0's text calls)."""
    c0 = load_cache(config.CACHE_DIR / "C0.json")
    chars = {}
    for e in c0.values():
        lines = [ev["text"] for ev in e["record"]["events"] if ev["kind"] == "speech" and ev["who"] == "party"]
        chars.setdefault(e["scenario_id"], []).append(sum(len(t) for t in lines))
    per = {sid: sum(v) / len(v) for sid, v in chars.items()}
    out, total_usd, total_cr = {}, 0.0, 0.0
    for cid in cids:
        cand = voice_candidate(cid)
        jobs = _replayer(scs, 99).pending(cand, [s.model_dump() for s in scs], k, load_cache(config.CACHE_DIR / f"{cid}.json"))
        ch = sum(per.get(sc["id"], 900) for sc, _, _ in jobs)
        credits = ch * CREDITS_PER_CHAR + ch * SECONDS_PER_CHAR / 60 * STT_PER_MIN
        out[cid] = {"calls": len(jobs), "usd": round(len(jobs) * CLAUDE_PER_CALL, 2), "credits": round(credits)}
        total_usd += len(jobs) * CLAUDE_PER_CALL
        total_cr += credits
    return {"candidates": out, "usd": round(total_usd, 2), "credits": round(total_cr * 1.2)}


def _balance() -> int | None:
    try:
        return elevenlabs.subscription()["used"]
    except VoiceError:
        return None


def run_calls(cids: list[str], scs: list, k: int, max_credits: int, max_usd: float) -> dict:
    est = estimate(cids, scs, k)
    print(f"{sum(c['calls'] for c in est['candidates'].values())} calls to run: about ${est['usd']:.2f} of Claude and "
          f"{est['credits']} ElevenLabs credits (with a 20% margin); caps ${max_usd:.2f} and {max_credits} credits")
    if est["credits"] > max_credits or est["usd"] > max_usd:
        sys.exit("refused: the estimate is above a cap (raise --max-credits or --max-usd, or run fewer)")
    spent = {"credits": 0.0, "failed": 0}
    for cid in cids:
        cand, rp = voice_candidate(cid), _replayer(scs, max_usd)

        def done(entry, rp=rp):
            spent["credits"] += entry.get("voice_credits") or 0
            if entry.get("error"):
                spent["failed"] += 1
                print(f"   {cid} {entry['scenario_id']}#{entry['r']} failed (not cached; run again): {entry['error'][:120]}")
            if spent["credits"] > max_credits:
                rp.max_usd = 0.0                    # the credit cap: calls not yet started are skipped
        rp.run(cand, [s.model_dump() for s in scs], k, workers=3, on_done=done)
        print(f"  {cid}: done (credits so far about {spent['credits']:.0f})")
    rows = [r for cid in cids for r in evaluate(cid, scs, k)]
    proposals._judge(rows, max_usd=0.25)
    return spent


def results(cids: list[str], scs: list, k: int) -> dict:
    """Gate each voice candidate against C0's text calls on the same scenarios, plus attribution and voice stats."""
    full_c0 = group(evaluate("C0", load_scenarios(SUITES["suite"]), 4))
    profile = baseline_profile(full_c0, SPECS)
    a, b = split_aa(full_c0)
    floor = noise_floor(gate(a, b, SPECS, profile=profile))
    ids = [s.id for s in scs]
    base = group(evaluate("C0", scs, 4))
    out = {}
    for cid in cids:
        rows = evaluate(cid, scs, k)
        if not rows:
            continue
        r = gate(base, group(rows), SPECS, noise=floor, profile=profile, expected=ids, min_k=(4, k))
        unlabeled = sum(not s["judged"] for _, _, s in rows)
        verdict, reasons = r.verdict, list(r.reasons)
        if unlabeled and verdict != REJECT:
            verdict = INCOMPLETE
            reasons.insert(0, f"Incomplete: {unlabeled} calls have no judge labels yet")
        misses = [dict(m, scenario=sc.id, r=e["r"]) for sc, e, s in rows for m in attribute(sc, e["record"], s)]
        not_verified = sum(identity_failed(sc, e["record"]) is not None for sc, e, _ in rows)
        heard = [ev for _, e, _ in rows for ev in e["record"]["events"] if "heard" in ev]
        errs = [wer(ev["text"], ev["heard"]) for ev in heard]
        voice = [ev["voice"] for ev in heard if ev.get("voice")]
        timing = lambda key: [v[key] for v in voice if v.get(key)]
        out[cid] = {
            "description": LINES[cid][0], "expected": EXPECTED[cid], "verdict": verdict, "reasons": reasons,
            "calls": len(rows), "scenarios": r.scenarios, "metrics": [m.__dict__ for m in r.metrics],
            "rules": [x.__dict__ for x in r.rules],
            "attribution": dict(Counter(m["cause"] for m in misses)), "misses": misses,
            "identity_failed_on_misheard_name": not_verified,
            "lines": {"heard": len(heard), "changed": sum(x > 0 for x in errs), "garbled": sum(x > GARBLED for x in errs),
                      "mean_wer": round(sum(errs) / len(errs), 3) if errs else None},
            "seconds": {"speech": _mean(timing("tts_s")), "transcription": _mean(timing("stt_s"))},
            "credits": round(sum(e.get("voice_credits") or 0 for _, e, _ in rows)),
            "claude_usd": round(sum(e.get("cost_usd") or 0 for _, e, _ in rows), 3),
        }
    return out


def _mean(xs: list) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def show(res: dict) -> None:
    print(f"\nvoice candidates vs C0 (text), {res['k']} replays, scenarios: {', '.join(res['scenarios'])}")
    for cid, r in res["candidates"].items():
        ok = "as expected" if r["verdict"] == r["expected"] else f"EXPECTED {r['expected']}"
        L = r["lines"]
        print(f"\n{cid} {r['description']}: {r['verdict']} ({ok}) | {r['calls']} calls | patient lines {L['heard']}: "
              f"{L['changed']} heard differently, {L['garbled']} garbled (word error > {GARBLED:.0%}) | "
              f"identity check failed on a misheard name in {r.get('identity_failed_on_misheard_name', 0)} calls | "
              f"~{r['credits']} credits, ${r['claude_usd']:.2f}")
        print(f"   misses by cause: {r['attribution'] or '-'}")
        for x in r["reasons"][:5]:
            print(f"   - {x}")
        for m in [m for m in r["misses"] if m["cause"] != "not said"][:3]:
            print(f"   [{m['scenario']}#{m['r']} {m['missed']}] said \"{m['said'][:110]}\"\n      heard \"{m['heard'][:110]}\"")
    if res.get("balance"):
        print(f"\nElevenLabs balance {res['balance'][0]} -> {res['balance'][1]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "run", "results"):
        p = sub.add_parser(name)
        p.add_argument("--only", default="VC,VH", help="comma-separated: VC, VE, VN, VH")
        p.add_argument("--scenarios", type=int, default=12)
        p.add_argument("--k", type=int, default=2)
        if name == "run":
            p.add_argument("--max-credits", type=int, default=12000)
            p.add_argument("--max-usd", type=float, default=1.0)
    sub.add_parser("smoke")
    sub.add_parser("show")
    args = ap.parse_args()
    try:
        if args.cmd == "show":
            show(json.loads(OUT.read_text(encoding="utf-8")))
            return
        if args.cmd == "smoke":
            scs, before = scenarios(1), _balance()
            run_calls(["VC", "VH"], scs, 1, max_credits=1000, max_usd=0.1)
            after = _balance()
            for cid in ("VC", "VH"):
                e = load_cache(config.CACHE_DIR / f"{cid}.json").get(f"{scs[0].id}#0")
                if not e:
                    continue
                said = sum(len(ev["text"]) for ev in e["record"]["events"] if "heard" in ev)
                print(f"{cid}: {said} characters spoken, ~{e.get('voice_credits', 0):.0f} credits by our count, ${e['cost_usd']:.4f} Claude")
                for ev in [ev for ev in e["record"]["events"] if "heard" in ev and ev["heard"] != ev["text"]][:4]:
                    print(f"   said  \"{ev['text'][:100]}\"\n   heard \"{ev['heard'][:100]}\"")
            print(f"balance {before} -> {after} (it can lag a few minutes)")
            return
        cids = [c.strip() for c in args.only.split(",") if c.strip()]
        bad = [c for c in cids if c not in LINES]
        if bad:
            sys.exit(f"unknown candidates {bad}; choose from {list(LINES)}")
        scs = scenarios(args.scenarios)
        if args.cmd == "results":                  # recompute verdicts and attribution from the cache: free
            prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
            res = {"run_at": prev.get("run_at"), "k": args.k, "scenarios": [s.id for s in scs],
                   "balance": prev.get("balance"), "failed": prev.get("failed", 0), "candidates": results(cids, scs, args.k)}
            OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
            show(res)
            return
        if args.cmd == "plan":
            est = estimate(cids, scs, args.k)
            for cid, c in est["candidates"].items():
                print(f"{cid} {LINES[cid][0]:<38} {c['calls']:>3} calls  ${c['usd']:.2f}  ~{c['credits']} credits")
            print(f"total: ${est['usd']:.2f} of Claude, about {est['credits']} credits with a 20% margin")
            return
        before = _balance()
        spent = run_calls(cids, scs, args.k, args.max_credits, args.max_usd)
        res = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "k": args.k,
               "scenarios": [s.id for s in scs], "balance": [before, _balance()], "failed": spent["failed"],
               "candidates": results(cids, scs, args.k)}
        OUT.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
        show(res)
    except VoiceError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
