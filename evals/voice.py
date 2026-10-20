"""Voice benchmark: do the critical facts in a patient's words survive speech, a phone line and transcription?

    python -m evals.voice voices                       # the voices this account can use (free)
    python -m evals.voice benchmark --dry-run          # what's not cached yet, and its estimated credits (free)
    python -m evals.voice benchmark                    # voice and transcribe what's missing (cap 3,000 credits)
    python -m evals.voice show                         # the last run's tables, from the results file (free)

Every line of discharge/scenarios/voice_benchmark.jsonl is voiced by each voice once (cached). Each clip then
goes through every CONDITION: an input pipeline (channel + transcription settings). Each line's critical
facts (negations, drug names, numbers, late symptoms, corrections, Spanish) are checked on every transcript by
deterministic rules (discharge/voice_facts.py), and every condition is compared with "clear" on the SAME clips
with an exact McNemar test. No agent is involved yet; that is Day 3.
"""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from agentgate.stats import mcnemar_exact, wilson
from agentgate.voice import elevenlabs
from agentgate.voice.base import Audio, VoiceCache, VoiceError
from agentgate.voice.phone import phone_line
from discharge import config
from discharge.voice_facts import preserved

BENCHMARK = config.SCENARIO_DIR / "voice_benchmark.jsonl"
CACHE = VoiceCache(config.CACHE_DIR / "voice")
OUT = config.RESULTS_DIR / "voice_benchmark.json"
VOICES = {"CwhRBWXzGAHq8TQ4Fs17": "Roger (male, middle-aged, American)",
          "XrExE9yKIg1WjnnlVkGX": "Matilda (female, middle-aged, American)"}
CREDITS_PER_CHAR = 0.2          # text to speech, Flash v2.5: measured 525 credits for 2,628 characters
CREDITS_PER_MINUTE = 21         # transcription, Scribe v2: measured about 104 credits for 4.9 minutes of audio
SECONDS_PER_CHAR = 0.056        # speech length: measured 146 seconds for 2,628 characters
ORDER = ["negation", "sound_alike", "number", "late_symptom", "correction", "spanish", "control"]


@dataclass(frozen=True)
class Condition:
    name: str                                   # unique; "group#n" for the n-th noise sample of a group
    label: str
    channel: Callable[[Audio], Audio]
    stt: dict                                   # ElevenLabsSTT settings

    @property
    def group(self) -> str:
        return self.name.split("#")[0]


# Sound-event tags are OFF everywhere except the one condition that documents them: with tags on (the API
# default), 11 of 800 transcripts in the first full run were nothing but a tag ("[outro jingle]") and the
# patient's sentence was gone; with tags off, 0 of 200. Run 2026-10-19, see PLAN.md 13.2.
NO_TAGS = {"tag_audio_events": False}
SEEDS = (7, 8, 9)                               # three independent noise and packet-loss samples per clip


def _noisy(snr: float, loss: float, seed: int) -> Callable[[Audio], Audio]:
    return lambda a: phone_line(a, snr_db=snr, loss=loss, seed=seed)


CONDITIONS = [
    Condition("clear", "as voiced", lambda a: a, NO_TAGS),
    Condition("event_tags", "as voiced, sound-event tags on (API default)", lambda a: a, {}),
    Condition("phone", "clean phone line (8 kHz, mu-law)", phone_line, NO_TAGS),
    *[Condition(f"phone_noisy#{i + 1}", "noisy, lossy phone line (15 dB SNR, 3% packets lost)",
                _noisy(15, 0.03, s), NO_TAGS) for i, s in enumerate(SEEDS)],
    *[Condition(f"phone_harsh#{i + 1}", "harsher phone line (10 dB SNR, 5% packets lost)",
                _noisy(10, 0.05, s), NO_TAGS) for i, s in enumerate(SEEDS)],
]


def load() -> list[dict]:
    return [json.loads(line) for line in BENCHMARK.read_text(encoding="utf-8").splitlines() if line.strip()]


def estimate(lines: list[dict], voices: list[str]) -> dict:
    """What a run would still pay for (cached audio and transcripts are free)."""
    chars, clips_todo, seconds = 0, 0, 0.0
    for v in voices:
        tts = elevenlabs.ElevenLabsTTS(v)
        for x in lines:
            secs = len(x["text"]) * SECONDS_PER_CHAR
            if not CACHE.has_audio(tts, x["text"]):
                chars += len(x["text"])
                clips_todo += 1
                seconds += secs * len(CONDITIONS)
            else:
                audio, _ = CACHE.speak(tts, x["text"])
                for c in CONDITIONS:
                    if not CACHE.has_transcript(elevenlabs.ElevenLabsSTT(**c.stt), c.channel(audio)):
                        seconds += audio.seconds
    tts_credits, stt_credits = chars * CREDITS_PER_CHAR, seconds / 60 * CREDITS_PER_MINUTE
    return {"clips": len(lines) * len(voices), "clips_to_voice": clips_todo, "characters": chars,
            "minutes_to_transcribe": round(seconds / 60, 1),
            "credits": round((tts_credits + stt_credits) * 1.2), "tts_credits": round(tts_credits),
            "stt_credits": round(stt_credits)}


def run(voices: list[str], max_credits: int) -> dict:
    lines = load()
    est = estimate(lines, voices)
    print(f"{est['clips_to_voice']} of {est['clips']} clips to voice ({est['characters']} characters), "
          f"{est['minutes_to_transcribe']} minutes to transcribe: about {est['credits']} credits; cap {max_credits}")
    if est["credits"] > max_credits:
        sys.exit(f"refused: estimate {est['credits']} credits is above the cap {max_credits} (raise --max-credits)")
    before, reported, rows = _balance(), 0, []
    stts = {c.name: elevenlabs.ElevenLabsSTT(**c.stt) for c in CONDITIONS}
    errors = []
    for v in voices:
        tts = elevenlabs.ElevenLabsTTS(v)
        for x in lines:
            name = VOICES.get(v, v).split(" ")[0]
            # One failed clip (after the adapter's retries) is recorded and skipped; the rest of the run goes on.
            # Everything finished is cached, so running again only pays for what failed.
            try:
                audio, hit = CACHE.speak(tts, x["text"])
                reported += 0 if hit else (audio.meta.get("credits") or 0)
                row = {k: x[k] for k in ("id", "category", "language", "text", "expect")}
                row.update(voice=v, audio_seconds=round(audio.seconds, 2), results={})
                for c in CONDITIONS:
                    tr, _ = CACHE.transcribe(stts[c.name], c.channel(audio))
                    ok = preserved(x["facts"], tr.text)
                    row["results"][c.name] = {"text": tr.text, "facts": ok, "kept": all(ok)}
            except VoiceError as exc:
                errors.append({"id": x["id"], "voice": v, "error": str(exc)})
                print(f"  {x['id']:<10} {name:<8} ERROR (skipped; run again to retry): {exc}")
                continue
            rows.append(row)
            lost = [c.name for c in CONDITIONS if not row["results"][c.name]["kept"]]
            print(f"  {x['id']:<10} {name:<8} " + (f"lost in: {', '.join(lost)}" if lost else "kept everywhere"))
    after = _balance()
    result = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "voices": {v: VOICES.get(v, v) for v in voices},
              "tts_model": elevenlabs.TTS_MODEL, "stt_model": elevenlabs.STT_MODEL,
              "conditions": {c.name: c.label for c in CONDITIONS},
              "credits": {"tts_reported": reported, "balance_before": before, "balance_after": after,
                          "note": "the balance can lag a few minutes; text-to-speech cost is reported with each reply"},
              "complete": not errors, "errors": errors, "summary": summarize(rows), "lines": rows}
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def _balance() -> int | None:
    try:
        return elevenlabs.subscription()["used"]
    except VoiceError:
        return None


def summarize(rows: list[dict]) -> dict:
    """Per condition group and category: transcripts that kept every fact (Wilson 95% range), and vs "clear"
    on the same clips: lost only here, kept only here, and the exact McNemar p-value. A group with several
    noise samples ("phone_noisy#1".."#3") pools them: each sample is paired with its clip's clear transcript."""
    keys = list(rows[0]["results"]) if rows else []
    groups = list(dict.fromkeys(k.split("#")[0] for k in keys))
    out = {}
    for g in groups:
        members = [k for k in keys if k.split("#")[0] == g]
        out[g] = {}
        for cat in ORDER + ["all"]:
            sel = [r for r in rows if cat == "all" or r["category"] == cat]
            if not sel:
                continue
            pairs = [(r, k) for r in sel for k in members]
            kept = sum(r["results"][k]["kept"] for r, k in pairs)
            lo, hi = wilson(kept, len(pairs))
            tag = lambda r, k: f"{r['id']}/{r['voice'][:4]}" + (f"#{k.split('#')[1]}" if "#" in k else "")
            worse = [tag(r, k) for r, k in pairs if r["results"]["clear"]["kept"] and not r["results"][k]["kept"]]
            better = [tag(r, k) for r, k in pairs if not r["results"]["clear"]["kept"] and r["results"][k]["kept"]]
            out[g][cat] = {"kept": kept, "n": len(pairs), "samples": len(members), "lo": round(lo, 3), "hi": round(hi, 3),
                           "lost_vs_clear": worse, "gained_vs_clear": better,
                           "p_vs_clear": mcnemar_exact(len(worse), len(better))}
    return out


def show(result: dict) -> None:
    s = result["summary"]
    names = list(s)
    print(f"\nvoices: {', '.join(result['voices'].values())} | TTS {result['tts_model']} | STT {result['stt_model']}")
    print(f"{'category':<14}" + "".join(f"{n:>16}" for n in names))
    for cat in ORDER + ["all"]:
        if cat in s[names[0]]:
            print(f"{cat:<14}" + "".join(f"{s[n][cat]['kept']:>8}/{s[n][cat]['n']:<3} {s[n][cat]['kept'] / s[n][cat]['n']:>3.0%}" for n in names))
    labels = {k.split("#")[0]: v for k, v in result["conditions"].items()}
    print("\nvs clear, on the same clips (exact McNemar; noise groups pool their samples):")
    for n in names[1:]:
        a = s[n]["all"]
        print(f"  {n:<12} {labels[n]:<52} lost {len(a['lost_vs_clear']):>2}, gained {len(a['gained_vs_clear']):>2}, "
              f"p={a['p_vs_clear']:.2g}")
    by_id = {(r["id"], r["voice"][:4]): r for r in result["lines"]}
    for n in names[1:]:
        for key in s[n]["all"]["lost_vs_clear"][:4]:
            clip, _, sample = key.partition("#")
            r = by_id[tuple(clip.split("/"))]
            k = f"{n}#{sample}" if sample else n
            print(f"\n[{k}] {r['id']} ({r['category']}, {result['voices'][r['voice']].split(' ')[0]}): said \"{r['text']}\"\n"
                  f"   clear heard \"{r['results']['clear']['text']}\"\n   {k} heard \"{r['results'][k]['text']}\"")
    cr = result["credits"]
    print(f"\ncredits: text to speech reported {cr['tts_reported']}; balance {cr['balance_before']} -> {cr['balance_after']}")
    if result.get("errors"):
        print(f"INCOMPLETE: {len(result['errors'])} clips failed and are not in these numbers; run the benchmark "
              "again (finished clips are cached and free).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("voices")
    b = sub.add_parser("benchmark")
    b.add_argument("--voice", action="append", help="voice id (repeatable); default: the two benchmark voices")
    b.add_argument("--max-credits", type=int, default=3000)
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("show")
    args = ap.parse_args()
    try:
        if args.cmd == "voices":
            for v in elevenlabs.voices():
                print(f"{v['id']}  {v['name'][:40]:<40} {v.get('accent', ''):<12} {v.get('gender', ''):<8} {v.get('category', '')}")
        elif args.cmd == "show":
            show(json.loads(OUT.read_text(encoding="utf-8")))
        else:
            voices = args.voice or list(VOICES)
            if args.dry_run:
                est = estimate(load(), voices)
                print(f"{est['clips_to_voice']} of {est['clips']} clips to voice ({est['characters']} characters, about "
                      f"{est['tts_credits']} credits); {est['minutes_to_transcribe']} minutes to transcribe (about "
                      f"{est['stt_credits']} credits); total with a 20% margin: about {est['credits']} credits")
            else:
                show(run(voices, args.max_credits))
    except VoiceError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
