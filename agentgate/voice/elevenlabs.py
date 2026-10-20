"""ElevenLabs text to speech and speech to text, over plain HTTPS (no SDK). Batch only: no streaming needed to
test what transcription does to an agent's input.

    ELEVENLABS_API_KEY in .env (a restricted key with Text to Speech, Speech to Text, Voices read, User read).

Checked on 2026-10-19 with one live sentence: Flash v2.5 returned 16 kHz PCM and a `character-cost` header
(6 for 32 characters); Scribe v2 returned "I have not taken the water pill".
"""

import os
import time

import httpx

from .base import Audio, Transcript, VoiceError

API = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_flash_v2_5"           # the cheapest, low-latency model
STT_MODEL = "scribe_v2"
TIMEOUT_S = 60.0


RETRIES = 3                                   # attempts for dropped connections, timeouts, 429 and 5xx
_sleep = time.sleep                           # replaced in tests


def _post(transport, url: str, what: str, **kw) -> httpx.Response:
    """POST with retries on transient failures (a dropped connection, a timeout, rate limits, server errors),
    backing off 1 s, then 2 s. Anything else fails at once."""
    for attempt in range(RETRIES):
        try:
            with httpx.Client(timeout=TIMEOUT_S, transport=transport) as c:
                r = c.post(url, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES - 1:
                _sleep(2 ** attempt)
                continue
            return r
        except httpx.TransportError as exc:          # includes RemoteProtocolError, ConnectError, timeouts
            if attempt == RETRIES - 1:
                raise VoiceError(f"ElevenLabs {what} failed after {RETRIES} attempts: {type(exc).__name__}") from None
            _sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set (add it to .env)")
    return key


def _fail(what: str, r: httpx.Response) -> VoiceError:
    try:
        detail = r.json().get("detail")
        msg = detail.get("message") if isinstance(detail, dict) else str(detail)
    except ValueError:
        msg = r.text[:200]
    return VoiceError(f"ElevenLabs {what} failed: HTTP {r.status_code}: {str(msg)[:200]}")


class ElevenLabsTTS:
    provider = "elevenlabs"

    def __init__(self, voice: str, model: str = TTS_MODEL, rate: int = 16000,
                 transport: httpx.BaseTransport | None = None):
        self.voice, self.model, self.rate = voice, model, rate
        self.transport = transport                       # tests only

    def speak(self, text: str) -> Audio:
        t0 = time.monotonic()
        r = _post(self.transport, f"{API}/text-to-speech/{self.voice}", "text to speech",
                  params={"output_format": f"pcm_{self.rate}"}, headers={"xi-api-key": _key()},
                  json={"text": text, "model_id": self.model})
        if r.status_code != 200:
            raise _fail("text to speech", r)
        cost = r.headers.get("character-cost")
        return Audio(r.content, self.rate, {"credits": int(cost) if cost and cost.isdigit() else None,
                                            "request_id": r.headers.get("request-id"),
                                            "latency_s": round(time.monotonic() - t0, 3)})


class ElevenLabsSTT:
    provider = "elevenlabs"

    def __init__(self, model: str = STT_MODEL, language: str | None = None, tag_audio_events: bool | None = None,
                 transport: httpx.BaseTransport | None = None):
        """language: None lets the model detect it (the default); a code such as "en" forces it.
        tag_audio_events: None keeps the API default (tags such as "[laughter]" on); False turns them off."""
        self.model, self.language, self.tag_audio_events = model, language, tag_audio_events
        self.transport = transport

    @property
    def settings(self) -> dict:
        """Settings beyond model and language that change the transcript (part of the cache key)."""
        return {} if self.tag_audio_events is None else {"tag_audio_events": self.tag_audio_events}

    def transcribe(self, audio: Audio) -> Transcript:
        data = {"model_id": self.model}
        if self.language:
            data["language_code"] = self.language
        if self.tag_audio_events is not None:
            data["tag_audio_events"] = "true" if self.tag_audio_events else "false"
        t0 = time.monotonic()
        r = _post(self.transport, f"{API}/speech-to-text", "speech to text", headers={"xi-api-key": _key()},
                  data=data, files={"file": ("audio.wav", audio.wav(), "audio/wav")})
        if r.status_code != 200:
            raise _fail("speech to text", r)
        body = r.json()
        return Transcript(str(body.get("text", "")).strip(),
                          {"detected_language": body.get("language_code"),
                           "language_probability": body.get("language_probability"),
                           "request_id": r.headers.get("request-id"), "latency_s": round(time.monotonic() - t0, 3)})


def subscription() -> dict:
    """Plan and credits used so far (needs the User read permission). Free."""
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{API}/user/subscription", headers={"xi-api-key": _key()})
    if r.status_code != 200:
        raise _fail("subscription", r)
    d = r.json()
    return {"tier": d.get("tier"), "used": d.get("character_count"), "limit": d.get("character_limit")}


def voices() -> list[dict]:
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{API}/voices", headers={"xi-api-key": _key()})
    if r.status_code != 200:
        raise _fail("voices", r)
    return [{"id": v["voice_id"], "name": v["name"], "category": v.get("category"), **(v.get("labels") or {})}
            for v in r.json().get("voices", [])]
