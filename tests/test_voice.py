"""Voice layer: phone-line simulation, caches, ElevenLabs adapters (mock transport), fact checks, benchmark.
Offline: fake providers and a mock HTTP transport; no network, no credits."""

import json
import math
import os

os.environ.setdefault("DSG_TOKEN", "test-token-0123456789")

import httpx
import numpy as np
import pytest

from agentgate.voice import elevenlabs
from agentgate.voice.base import Audio, Transcript, VoiceCache, VoiceError
from agentgate.voice.phone import PHONE_RATE, mu_law, phone_line
from discharge import config
from discharge.voice_facts import check, numbers, preserved, tokens

RATE = 16000


def tone(freqs: list[float], seconds: float = 0.5, rate: int = RATE) -> Audio:
    t = np.arange(int(seconds * rate)) / rate
    x = sum(np.sin(2 * math.pi * f * t) for f in freqs) / (len(freqs) + 0.5)
    return Audio((x * 32767).astype("<i2").tobytes(), rate)


def power_at(audio: Audio, hz: float) -> float:
    x = np.frombuffer(audio.pcm, dtype="<i2").astype(float)
    spec = np.abs(np.fft.rfft(x)) / len(x)
    freqs = np.fft.rfftfreq(len(x), 1 / audio.rate)
    return float(spec[np.argmin(np.abs(freqs - hz))])


# ---------------------------------------------------------------- phone line

def test_phone_line_is_narrowband_8khz_and_deterministic():
    a = tone([1000, 6000])                                                  # in band, and far above it
    p = phone_line(a)
    assert p.rate == PHONE_RATE and abs(p.seconds - a.seconds) < 0.01
    assert power_at(p, 1000) > 50 * max(power_at(p, 3900), 1e-9)          # speech band kept, the rest gone
    assert phone_line(a).pcm == p.pcm                                       # same input, same output
    assert phone_line(a, snr_db=15, seed=1).pcm == phone_line(a, snr_db=15, seed=1).pcm
    assert phone_line(a, snr_db=15, seed=1).pcm != p.pcm


def test_noise_and_packet_loss_degrade_as_set():
    a = tone([800], seconds=2.0)
    clean = np.frombuffer(phone_line(a).pcm, dtype="<i2").astype(float)
    noisy = np.frombuffer(phone_line(a, snr_db=10, seed=3).pcm, dtype="<i2").astype(float)
    snr = 10 * np.log10(np.mean(clean ** 2) / np.mean((noisy - clean) ** 2))
    assert 7 < snr < 13                                                     # about the 10 dB asked for
    lossy = np.frombuffer(phone_line(a, loss=0.25, seed=3).pcm, dtype="<i2").reshape(-1, 160)   # 20 ms packets
    silent = np.mean(np.all(np.abs(lossy) <= 8, axis=1))                  # mu-law silence decodes to ~2-3, not 0
    assert 0.1 < silent < 0.4                                               # about a quarter of packets dropped
    assert phone_line(a, loss=0.25, seed=3).pcm == phone_line(a, loss=0.25, seed=3).pcm


def test_mu_law_quantizes_but_keeps_the_signal():
    x = np.linspace(-1, 1, 1001)
    y = mu_law(x)
    assert np.max(np.abs(y - x)) < 0.05 and len(np.unique(np.round(y, 6))) <= 256


def test_phone_line_handles_empty_and_tiny_audio():
    assert phone_line(Audio(b"", RATE)).pcm == b""
    assert phone_line(Audio(b"\x00\x00" * 10, RATE)).rate == PHONE_RATE


def test_wav_round_trip():
    a = tone([440])
    assert Audio.from_wav(a.wav()).pcm == a.pcm


# ---------------------------------------------------------------- cache

class FakeTTS:
    provider, model, rate = "fake", "m1", RATE

    def __init__(self, voice="v1"):
        self.voice, self.calls = voice, 0

    def speak(self, text):
        self.calls += 1
        return tone([300 + len(text)])


class FakeSTT:
    provider, model, language = "fake", "s1", None

    def __init__(self, text="heard"):
        self.text, self.calls = text, 0

    def transcribe(self, audio):
        self.calls += 1
        return Transcript(self.text)


def test_cache_reuses_audio_and_transcripts_and_keys_on_every_setting(tmp_path):
    cache, tts, stt = VoiceCache(tmp_path), FakeTTS(), FakeSTT()
    a1, hit1 = cache.speak(tts, "hello")
    a2, hit2 = cache.speak(tts, "hello")
    assert (hit1, hit2, tts.calls) == (False, True, 1) and a1.pcm == a2.pcm
    assert a2.meta["voice"] == "v1" and a2.meta["sha256"] == a1.sha256
    cache.speak(FakeTTS(voice="v2"), "hello")                               # another voice: a new artifact
    assert len(list((tmp_path / "audio").glob("*.wav"))) == 2
    t1, h1 = cache.transcribe(stt, a1)
    t2, h2 = cache.transcribe(stt, phone_line(a1))                          # different audio: transcribed again
    t3, h3 = cache.transcribe(stt, a1)
    assert (h1, h2, h3, stt.calls) == (False, False, True, 2) and t3.meta["audio_sha256"] == a1.sha256
    assert cache.has_audio(tts, "hello") and not cache.has_audio(tts, "bye")
    other = FakeSTT()
    other.settings = {"tag_audio_events": False}                           # a setting changes the key
    assert cache.has_transcript(stt, a1) and not cache.has_transcript(other, a1)
    cache.transcribe(other, a1)
    assert other.calls == 1 and cache.has_transcript(other, a1)


# ---------------------------------------------------------------- ElevenLabs adapters (mock transport)

def test_elevenlabs_requests_are_shaped_right_and_errors_never_leak_the_key(monkeypatch):
    key = "sk_" + "a1" * 24
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/speech-to-text"):
            return httpx.Response(200, json={"text": " I have not taken it. ", "language_code": "eng"})
        return httpx.Response(200, content=b"\x00\x01" * 800, headers={"character-cost": "7", "request-id": "r1"})

    audio = elevenlabs.ElevenLabsTTS("voice123", transport=httpx.MockTransport(handler)).speak("hi there")
    assert audio.rate == 16000 and audio.meta["credits"] == 7
    tts_req = seen[0]
    assert tts_req.url.path == "/v1/text-to-speech/voice123" and tts_req.url.params["output_format"] == "pcm_16000"
    assert tts_req.headers["xi-api-key"] == key and json.loads(tts_req.content)["model_id"] == "eleven_flash_v2_5"
    tr = elevenlabs.ElevenLabsSTT(transport=httpx.MockTransport(handler)).transcribe(audio)
    assert tr.text == "I have not taken it." and b'name="model_id"' in seen[1].content and b"scribe_v2" in seen[1].content
    assert b"language_code" not in seen[1].content and b"tag_audio_events" not in seen[1].content   # API defaults
    elevenlabs.ElevenLabsSTT(language="en", tag_audio_events=False, transport=httpx.MockTransport(handler)).transcribe(audio)
    assert b'name="language_code"' in seen[2].content and b'name="tag_audio_events"' in seen[2].content
    assert elevenlabs.ElevenLabsSTT(tag_audio_events=False).settings == {"tag_audio_events": False}
    assert elevenlabs.ElevenLabsSTT().settings == {}

    def refuse(req):
        return httpx.Response(401, json={"detail": {"message": f"bad key {key[:6]}..."}})
    with pytest.raises(VoiceError) as e:
        elevenlabs.ElevenLabsTTS("v", transport=httpx.MockTransport(refuse)).speak("x")
    assert "HTTP 401" in str(e.value) and key not in str(e.value)


def test_transient_failures_are_retried_then_reported(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_" + "c3" * 24)
    monkeypatch.setattr(elevenlabs, "_sleep", lambda s: None)
    attempts = []

    def flaky(fails, status=None):
        def handler(req):
            attempts.append(1)
            if len(attempts) <= fails:
                if status:
                    return httpx.Response(status)
                raise httpx.RemoteProtocolError("server disconnected", request=req)
            return httpx.Response(200, json={"text": "ok"})
        return httpx.MockTransport(handler)

    audio = Audio(b"\x00\x00" * 100, 16000)
    assert elevenlabs.ElevenLabsSTT(transport=flaky(2)).transcribe(audio).text == "ok" and len(attempts) == 3
    attempts.clear()
    with pytest.raises(VoiceError, match="after 3 attempts: RemoteProtocolError"):
        elevenlabs.ElevenLabsSTT(transport=flaky(5)).transcribe(audio)
    attempts.clear()
    assert elevenlabs.ElevenLabsSTT(transport=flaky(1, status=429)).transcribe(audio).text == "ok"
    attempts.clear()
    with pytest.raises(VoiceError, match="HTTP 400"):                     # not transient: no retry
        elevenlabs.ElevenLabsSTT(transport=flaky(5, status=400)).transcribe(audio)
    assert len(attempts) == 1


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(VoiceError, match="ELEVENLABS_API_KEY"):
        elevenlabs.ElevenLabsTTS("v", transport=httpx.MockTransport(lambda r: httpx.Response(200))).speak("x")


def test_console_errors_redact_the_elevenlabs_key(monkeypatch):
    from web import security
    key = "sk_" + "b2" * 24
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    assert key not in security.redact(f"failed with {key}") and "[redacted]" in security.redact(key)


# ---------------------------------------------------------------- critical facts

def test_tokens_and_numbers():
    assert tokens("I haven't TAKEN it!") == ["i", "have", "not", "taken", "it"]
    assert numbers(tokens("a hundred and two")) == [(0, 102)]
    assert numbers(tokens("five pounds, 15 more, forty milligrams")) == [(0, 5), (2, 15), (4, 40)]
    assert numbers(tokens("subí dos kilos")) == [(1, 2)]


@pytest.mark.parametrize("fact,good,bad", [
    ({"kind": "negation", "near": ["taken"]}, "I have not taken the pill", "I have now taken the pill"),
    ({"kind": "negation", "near": ["taken"]}, "I haven't taken it", "I have taken it"),
    ({"kind": "negation", "near": ["tomado"]}, "No he tomado la pastilla", "Ya he tomado la pastilla"),
    ({"kind": "number", "value": 5, "near": ["pounds"]}, "I gained 5 pounds", "I gained nine pounds"),
    ({"kind": "number", "value": 102, "near": ["temperature"]}, "my temperature was 102", "my temperature was 100"),
    ({"kind": "term", "any": ["hydroxyzine"], "not_any": ["hydralazine"]}, "I take hydroxyzine", "I take hydralazine"),
    ({"kind": "term", "any": ["chest pain"]}, "no chest pain at all", "no chess pain at all"),
])
def test_each_fact_kind_catches_the_damage(fact, good, bad):
    assert check(fact, good) and not check(fact, bad)


def test_unknown_fact_kind_is_refused():
    with pytest.raises(ValueError):
        check({"kind": "vibe"}, "anything")


# ---------------------------------------------------------------- the benchmark itself

def test_benchmark_is_complete_and_every_line_passes_its_own_facts():
    lines = [json.loads(x) for x in (config.SCENARIO_DIR / "voice_benchmark.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 100 and len({x["id"] for x in lines}) == 100
    counts = {}
    for x in lines:
        counts[x["category"]] = counts.get(x["category"], 0) + 1
        assert x["facts"] and all(preserved(x["facts"], x["text"])), x["id"]
        assert x["language"] in ("en", "es") and x["expect"]
    assert counts == {"negation": 18, "sound_alike": 18, "number": 14, "late_symptom": 14, "correction": 10,
                      "spanish": 14, "control": 12}


def test_mcnemar_exact():
    from agentgate.stats import mcnemar_exact
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 1024)                 # 10 lost, none gained: clear
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(3, 0) == 0.25                                      # 3 is not enough on its own


def test_noise_samples_pool_into_one_group_paired_with_clear():
    from evals.voice import CONDITIONS, summarize
    assert [c.group for c in CONDITIONS].count("phone_noisy") == 3 and all(c.stt.get("tag_audio_events") is False
                                                                           for c in CONDITIONS if c.name != "event_tags")
    row = {"id": "A", "category": "spanish", "voice": "VVVVx", "results": {
        "clear": {"kept": True}, "noisy#1": {"kept": False}, "noisy#2": {"kept": True}, "noisy#3": {"kept": False}}}
    s = summarize([row])["noisy"]["spanish"]
    assert (s["kept"], s["n"], s["samples"]) == (1, 3, 3) and s["lost_vs_clear"] == ["A/VVVV#1", "A/VVVV#3"]


def test_benchmark_summary_compares_each_condition_with_clear_on_the_same_clips():
    from evals.voice import summarize

    def row(i, cat, **kept):
        return {"id": i, "category": cat, "voice": "VVVVx", "results": {k: {"kept": v} for k, v in kept.items()}}
    rows = [row("A", "negation", clear=True, phone=False), row("B", "negation", clear=True, phone=True),
            row("C", "number", clear=False, phone=True)]
    s = summarize(rows)
    assert s["phone"]["negation"]["kept"] == 1 and s["phone"]["negation"]["lost_vs_clear"] == ["A/VVVV"]
    assert s["phone"]["number"]["gained_vs_clear"] == ["C/VVVV"]
    assert s["phone"]["all"]["p_vs_clear"] == 1.0 and s["clear"]["all"]["kept"] == 2
