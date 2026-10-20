"""Provider-neutral voice interfaces, audio as 16-bit mono PCM, and the cache that makes runs reproducible.

    tts.speak(text) -> Audio            # any provider
    phone_line(audio) -> Audio           # phone.py: what a phone call does to speech (deterministic)
    stt.transcribe(audio) -> Transcript  # any provider

Every artifact carries its provider, model and settings in `meta`, and is cached under a key derived from
everything that could change it: audio by (provider, model, voice, rate, text), transcripts by (provider,
model, language, sha256 of the audio bytes). Re-running is free; changing any setting reruns only that step.
"""

import hashlib
import io
import json
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class VoiceError(RuntimeError):
    """A provider failed (status, timeout, bad reply). Never contains the API key."""


@dataclass(frozen=True)
class Audio:
    pcm: bytes                      # 16-bit little-endian mono
    rate: int                       # samples per second
    meta: dict = field(default_factory=dict)

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (2 * self.rate)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pcm + str(self.rate).encode()).hexdigest()

    def wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(self.pcm)
        return buf.getvalue()

    @classmethod
    def from_wav(cls, data: bytes, meta: dict | None = None) -> "Audio":
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError("expected 16-bit mono WAV")
            return cls(w.readframes(w.getnframes()), w.getframerate(), meta or {})


@dataclass(frozen=True)
class Transcript:
    text: str
    meta: dict = field(default_factory=dict)


class TextToSpeech(Protocol):
    provider: str
    model: str
    voice: str
    rate: int

    def speak(self, text: str) -> Audio: ...


class SpeechToText(Protocol):
    provider: str
    model: str
    language: str | None

    def transcribe(self, audio: Audio) -> Transcript: ...


def _key(parts: list) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


class VoiceCache:
    """Audio as WAV plus a JSON sidecar; transcripts as JSON. One file per artifact, so a run is inspectable."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, kind: str, key: str, ext: str) -> Path:
        return self.root / kind / f"{key}.{ext}"

    def has_audio(self, tts: TextToSpeech, text: str) -> bool:
        key = _key([tts.provider, tts.model, tts.voice, tts.rate, text])
        return self._path("audio", key, "wav").exists() and self._path("audio", key, "json").exists()

    def speak(self, tts: TextToSpeech, text: str) -> tuple[Audio, bool]:
        """(audio, came from cache)."""
        key = _key([tts.provider, tts.model, tts.voice, tts.rate, text])
        wav, side = self._path("audio", key, "wav"), self._path("audio", key, "json")
        if wav.exists() and side.exists():
            return Audio.from_wav(wav.read_bytes(), json.loads(side.read_text(encoding="utf-8"))), True
        t0 = time.monotonic()
        audio = tts.speak(text)
        meta = {**audio.meta, "provider": tts.provider, "model": tts.model, "voice": tts.voice, "rate": audio.rate,
                "text": text, "seconds_to_make": round(time.monotonic() - t0, 3), "sha256": audio.sha256}
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(audio.wav())
        side.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return Audio(audio.pcm, audio.rate, meta), False

    def _transcript_path(self, stt: SpeechToText, audio: Audio) -> tuple[Path, dict]:
        settings = getattr(stt, "settings", {}) or {}
        # Default settings keep the original key, so transcripts made before settings existed stay cached.
        key = _key([stt.provider, stt.model, stt.language, audio.sha256] + ([settings] if settings else []))
        return self._path("transcripts", key, "json"), settings

    def has_transcript(self, stt: SpeechToText, audio: Audio) -> bool:
        return self._transcript_path(stt, audio)[0].exists()

    def transcribe(self, stt: SpeechToText, audio: Audio) -> tuple[Transcript, bool]:
        path, settings = self._transcript_path(stt, audio)
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            return Transcript(d["text"], d["meta"]), True
        t0 = time.monotonic()
        tr = stt.transcribe(audio)
        meta = {**tr.meta, "provider": stt.provider, "model": stt.model, "language": stt.language,
                "settings": settings, "audio_sha256": audio.sha256, "audio_seconds": round(audio.seconds, 3),
                "seconds_to_make": round(time.monotonic() - t0, 3)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": tr.text, "meta": meta}, ensure_ascii=False, indent=1), encoding="utf-8")
        return Transcript(tr.text, meta), False
