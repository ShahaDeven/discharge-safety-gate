"""Voice inside calls: the input channel in the conversation loop, the voice channel, replay with a channel,
failure attribution and the Day 3 candidates. Offline: fake providers, scripted agent and patient."""

import os

os.environ.setdefault("DSG_TOKEN", "test-token-0123456789")

import numpy as np

from agentgate.conversation import run_call
from agentgate.replay import Candidate, Replayer
from agentgate.audit import AuditLog
from agentgate.simulator import ScriptedSimulator
from agentgate.voice.base import Audio, Transcript, VoiceCache
from agentgate.voice.channel import VoiceChannel
from discharge.tools import DischargeHarness
from discharge.voice_attribution import attribute, wer

from conftest import ScriptedAgent, say, tool


def upper(text):
    return text.upper(), {"channel": "test", "credits": 1}


# ---------------------------------------------------------------- the channel in the conversation loop

class RecordingSim(ScriptedSimulator):
    def reply(self, transcript, sim_turn):
        self.seen = [t["text"] for t in transcript if t["who"] == "party"]
        return super().reply(transcript, sim_turn)


def test_agent_hears_the_channel_and_everyone_else_keeps_what_was_said(pilot):
    sc = pilot["P01"]
    agent = ScriptedAgent([[say("Hi, who am I speaking with?")], [say("Thanks.")]])
    sim = RecordingSim("Hello?", ["This is Rosa.", "I take Plavix."])
    rec = run_call(agent, sim, DischargeHarness(sc), sc.context("t"), max_agent_turns=3, channel=upper)
    assert agent.heard[:2] == ["HELLO?", "THIS IS ROSA."]                    # the agent got the channel's output
    party = [e for e in rec.events if e["kind"] == "speech" and e["who"] == "party"]
    assert party[0]["text"] == "Hello?" and party[0]["heard"] == "HELLO?" and party[0]["voice"]["credits"] == 1
    assert sim.seen == ["Hello?", "This is Rosa."]                            # the patient remembers its own words


def test_no_channel_changes_nothing(pilot):
    sc = pilot["P01"]
    agent = ScriptedAgent([[say("Hi.")]])
    rec = run_call(agent, ScriptedSimulator("Hello?", ["Yes."]), DischargeHarness(sc), sc.context("t"), max_agent_turns=2)
    assert agent.heard[0] == "Hello?" and all("heard" not in e for e in rec.events)


def test_a_line_the_patient_hangs_up_on_is_not_sent_through_the_channel(pilot):
    sc = pilot["P01"]
    sent = []
    agent = ScriptedAgent([[say("Hi.")], [say("Still there?")]])
    run_call(agent, ScriptedSimulator("Hello?", ["Bye now."], hang_up_after=1), DischargeHarness(sc), sc.context("t"),
             max_agent_turns=3, channel=lambda t: (sent.append(t) or t, {}))
    assert sent == ["Hello?"]


# ---------------------------------------------------------------- the voice channel

class FakeTTS:
    provider, model, voice, rate = "fake", "m", "v", 16000

    def speak(self, text):
        return Audio(b"\x01\x00" * (40 * len(text)), 16000, {"credits": len(text) // 5})


class FakeSTT:
    provider, model, language, settings = "fake", "s", None, {}

    def transcribe(self, audio):
        return Transcript(f"heard {len(audio.pcm)}")


def test_voice_channel_skips_stage_directions_counts_credits_once_and_seeds_by_text(tmp_path):
    seeds = []
    ch = VoiceChannel(FakeTTS(), FakeSTT(), VoiceCache(tmp_path), line=lambda a, s: seeds.append(s) or a)
    heard, meta = ch("I take Plavix. [beep]")
    assert heard.startswith("heard") and meta["said"] == "I take Plavix." and meta["credits"] > 0
    _, again = ch("I take Plavix. [beep]")
    assert again["credits"] == 0 and again["tts_cached"] and again["stt_cached"]     # cached: free
    assert seeds[0] == seeds[1]                                                      # same sentence, same noise
    ch("Something else.")
    assert seeds[2] != seeds[0]
    assert ch("[silence]") == ("[silence]", {"channel": "voice", "skipped": "nothing to say aloud"})


def test_replay_with_a_channel_records_heard_lines_and_voice_credits(tmp_path, pilot):
    sc = pilot["P01"]

    class Suite:
        sim_fingerprint = "t"

        def build(self, scenario, call_id):
            return sc.context(call_id), ScriptedSimulator("Hello?", ["Yes."]), DischargeHarness(sc)

    cand = Candidate("VX", "voice", "fp", lambda: ScriptedAgent([[say("Hi.")]]), make_channel=lambda ctx: upper)
    rp = Replayer(Suite(), tmp_path, AuditLog(tmp_path / "audit.jsonl"), max_agent_turns=2)
    entry = rp.replay_once(cand, {"id": sc.id}, 0, "fp")
    assert entry["error"] is None and entry["voice_credits"] >= 1
    assert any(e.get("heard") == "HELLO?" for e in entry["record"]["events"])


# ---------------------------------------------------------------- attribution

def test_word_error_rate():
    assert wer("I take Plavix since the stent", "I take Plavix since the stent") == 0
    assert wer("I take Plavix since the stent", "I take Clopix as the tenth") == 0.5
    assert wer("I can't catch my breath", "") == 1.0


def _record(lines):
    return {"events": [{"kind": "speech", "who": "party", **ln} for ln in lines]}


def _score(**kw):
    return {"under_triage": False, "med_missed": [], **kw}


def test_attribution_separates_transcription_agent_and_not_said(pilot):
    sc = next(s for s in pilot.values() if any(f.red_flag for f in s.facts))
    fid = next(f.id for f in sc.facts if f.red_flag)
    garbled = _record([{"text": "My ankles are really swollen today.", "heard": "[outro jingle]", "disclosed": [fid]}])
    assert attribute(sc, garbled, _score(under_triage=True))[0]["cause"] == "transcription"
    clear = _record([{"text": "My ankles are really swollen today.", "heard": "My ankles are really swollen today",
                      "disclosed": [f"[{fid}]"]}])
    assert attribute(sc, clear, _score(under_triage=True))[0]["cause"] == "agent"
    never = _record([{"text": "I'm fine.", "heard": "I'm fine"}])
    assert attribute(sc, never, _score(under_triage=True))[0]["cause"] == "not said"
    assert attribute(sc, clear, _score()) == []                               # nothing missed, nothing to attribute


def test_attribution_for_a_missed_medicine(pilot):
    sc = next(s for s in pilot.values() if s.discharge_meds)
    drug = sc.discharge_meds[0].name
    name = drug.split()[0]
    lost = _record([{"text": f"I stopped the {name} last week.", "heard": "I stopped the stuff last week"}])
    assert attribute(sc, lost, _score(med_missed=[drug]))[0]["cause"] == "transcription"
    kept = _record([{"text": f"I stopped the {name} last week.", "heard": f"I stopped the {name} last week"}])
    assert attribute(sc, kept, _score(med_missed=[drug]))[0]["cause"] == "agent"


# ---------------------------------------------------------------- the Day 3 candidates

def test_voice_candidates_differ_only_in_their_input_path():
    from discharge.candidates import candidates
    from evals.voice_calls import EXPECTED, LINES, voice_candidate
    c0 = candidates()["C0"]
    fps = {cid: voice_candidate(cid).fingerprint for cid in LINES}
    assert len(set(fps.values())) == len(LINES) and c0.fingerprint not in fps.values()
    assert all(voice_candidate(cid).make_agent.args[0] == c0.make_agent.args[0] for cid in ("VC", "VE", "VN", "VH"))
    assert voice_candidate("VF").make_agent.args[0] != c0.make_agent.args[0]   # VF changes the prompt, nothing else
    assert voice_candidate("VK").fingerprint != voice_candidate("VF").fingerprint  # VK adds keyterms on top
    assert EXPECTED == {"VC": "PROMOTE", "VE": "PROMOTE", "VN": "REJECT", "VH": "REJECT", "VF": "PROMOTE", "VK": "PROMOTE"}
    assert LINES["VC"][2] is None and LINES["VH"][2] == 10
    assert LINES["VF"][1:4] == LINES["VC"][1:4]                              # same voice, noise and loss as VC


def test_accented_names_match_without_the_accent_and_misspellings_still_fail():
    from discharge.tools import name_matches
    assert name_matches("Micaela Rendon", "Micaela Rendón") and name_matches("micaela rendón", "Micaela Rendón")
    assert not name_matches("Michaela Rendon", "Micaela Rendón") and not name_matches("BB Simonis", "Bebe Simonis")


def test_a_failed_identity_check_on_a_misheard_name_is_named_as_the_cause(pilot):
    from discharge.voice_attribution import identity_failed
    sc = next(s for s in pilot.values() if s.party.role == "patient" and any(f.red_flag for f in s.facts))
    first = sc.patient.name.split()[0]
    rec = {"events": [
        {"kind": "speech", "who": "party", "text": "Hello?", "heard": "Hello"},
        {"kind": "speech", "who": "party", "text": f"This is {first}.", "heard": "This is B B."},
        {"kind": "tool_result", "name": "verify_identity", "text": "NOT VERIFIED: the details do not match the record."}]}
    broke = identity_failed(sc, rec)
    assert broke and broke["said"] == f"This is {first}."                    # the line with the name, not "Hello?"
    misses = attribute(sc, rec, _score(under_triage=True))
    assert misses[0]["cause"] == "identity check (transcription)"
    rec["events"].append({"kind": "tool_result", "name": "verify_identity", "text": "VERIFIED: you are speaking with the patient."})
    assert identity_failed(sc, rec) is None


def test_keyterms_hint_the_patient_medicines_and_hospital_never_other_people(pilot):
    from evals.voice_calls import call_keyterms, voice_candidate
    sc = next(s for s in pilot.values() if s.party.name)
    terms = call_keyterms(sc.context("t"))
    assert sc.patient.name in terms and sc.discharge_meds[0].name in terms and sc.party.name not in terms
    ch = voice_candidate("VK").make_channel(sc.context("t"))
    assert ch.stt.keyterms[0] == sc.patient.name and ch.stt.tag_audio_events is False
    assert voice_candidate("VC").make_channel(sc.context("t")).stt.keyterms == ()


# ---------------------------------------------------------------- replaying voice calls in the console

def _saved(tmp_path, monkeypatch, text):
    """A voice-call cache holding one recording of `text`, as Roger (the voice of VC, VH and VK)."""
    from agentgate.voice import elevenlabs
    from evals import voice_calls

    class Saved:                                  # stands in for ElevenLabs once, to put the recording in the cache
        provider, model, voice, rate = "elevenlabs", elevenlabs.TTS_MODEL, voice_calls.ROGER, 16000

        def speak(self, t):
            return Audio((np.sin(np.arange(16000) / 5) * 8000).astype("<i2").tobytes(), 16000, {})

    cache = VoiceCache(tmp_path)
    cache.speak(Saved(), text)
    monkeypatch.setattr(voice_calls, "CACHE", cache)
    return voice_calls


def test_playback_reads_saved_audio_and_never_calls_the_provider(tmp_path, monkeypatch):
    vc = _saved(tmp_path, monkeypatch, "This is Bebe.")
    monkeypatch.setattr("agentgate.voice.elevenlabs._key", lambda: (_ for _ in ()).throw(AssertionError("called ElevenLabs")))
    assert Audio.from_wav(vc.recording("VC", "This is Bebe.")).rate == 16000
    heard = vc.recording("VH", "This is Bebe.", as_heard=True)
    assert Audio.from_wav(heard).rate == 8000 and heard == vc.recording("VH", "This is Bebe.", as_heard=True)
    assert vc.recording("VC", "Never voiced.") is None


def test_console_rows_show_what_was_heard_and_mark_the_misheard_name(pilot):
    from evals.dashboard import row, transcript
    sc = next(s for s in pilot.values() if s.party.role == "patient")
    said = f"This is {sc.patient.name.split()[0]}."
    events = [
        {"kind": "speech", "who": "agent", "text": "Who am I speaking with?"},
        {"kind": "speech", "who": "party", "text": said, "heard": "This is B B.", "voice": {"said": said}},
        {"kind": "tool_result", "name": "verify_identity", "text": "NOT VERIFIED: the details do not match the record."}]
    assert row(events[1], 1) == {"k": "s", "w": "p", "t": said, "h": "This is B B.", "au": 1}
    assert "au" not in row(events[1]) and "h" not in row(events[0], 0)       # live calls and text calls: unchanged
    rows = transcript({"record": {"events": events}}, {"violation_detail": []}, sc)
    assert rows[1]["idf"] and not any(r.get("idf") for r in (rows[0], rows[2]))
    assert not any(r.get("idf") for r in transcript({"record": {"events": events}}, {"violation_detail": []}))


def test_console_serves_voice_audio_read_only_and_never_places_voice_calls(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from web import api
    _saved(tmp_path, monkeypatch, "This is Bebe.")
    sid = next(iter(api.SCENARIOS))
    entry = {"record": {"events": [{"kind": "speech", "who": "party", "text": "This is Bebe. [beep]",
                                    "heard": "This is BB", "voice": {"said": "This is Bebe."}}]}}
    monkeypatch.setattr(api, "_cached", lambda cand: {f"{sid}#0": entry})
    c = TestClient(api.app, base_url="http://localhost:8001")
    ok = c.get(f"/api/voice/VK/{sid}/0/0")
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/wav"
    assert Audio.from_wav(c.get(f"/api/voice/VK/{sid}/0/0?line=phone").content).rate == 8000
    assert c.get(f"/api/voice/C0/{sid}/0/0").status_code == 404                 # only voice calls have audio
    assert c.get(f"/api/voice/VK/{sid}/0/5").status_code == 404
    assert c.get(f"/api/voice/VK/{sid}/0/0?line=raw").status_code == 400
    assert c.get(f"/api/stream?cand=VK&sid={sid}&mode=live").status_code == 400  # replay only: a click spends no credits


# ---------------------------------------------------------------- the voice section of the report and the pack

def test_report_voice_section_reads_saved_results_and_says_nothing_without_them():
    from evals.report import voice_section
    assert voice_section(None) == []
    v = {"benchmark": None, "workflows": None, "playback": ["VK"], "calls": {"k": 2, "scenarios": ["A", "B"], "baseline": "C0 in text",
         "candidates": [{"id": "VK", "description": "fixed", "verdict": "PROMOTE", "expected": "PROMOTE", "calls": 4,
                         "identity_failed": 0, "attribution": {"not said": 1}, "credits": 900,
                         "lines": {"heard": 10, "changed": 2, "garbled": 0},
                         "metrics": [{"name": "red_flag_recall", "label": "Red-flag escalation", "base": 1.0, "cand": 1.0}]}]}}
    md = "\n".join(voice_section(v))
    assert "| Red-flag escalation (text: 100%) | 100% |" in md and "**PROMOTE** (PROMOTE)" in md and "0/4" in md


def test_only_the_current_agents_pack_carries_the_voice_evidence(monkeypatch):
    from evals import validation
    monkeypatch.setattr(validation, "voice_summary", lambda: {"calls": None})
    data = {"gate": {"A/A": {"verdict": "PROMOTE", "reasons": [], "metrics": [], "rules": [], "scenarios": 1},
                     "C1": {"verdict": "REJECT", "reasons": [], "metrics": [], "rules": [], "scenarios": 1}},
            "calls": {"C0": {}, "C1": {}}, "candidates": [], "scenarios": {}}
    assert validation.build(data, "C0")["voice"] == {"calls": None}
    assert validation.build(data, "C1")["voice"] is None
