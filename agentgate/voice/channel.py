"""An input channel for run_call: what the person says -> speech -> a line -> transcription -> what the agent hears.

    channel = VoiceChannel(tts, stt, cache, line=lambda a, seed: phone_line(a, snr_db=15, loss=0.03, seed=seed))
    run_call(agent, sim, harness, ctx, channel=channel)

Each step goes through the VoiceCache, so an identical line is voiced and transcribed once. The line's noise
seed comes from the text itself, so the same sentence always gets the same noise (reproducible), while
different sentences get different noise. Stage directions in brackets ("[beep]") are not spoken.
"""

import hashlib
import re
from typing import Callable

from .base import Audio, SpeechToText, TextToSpeech, VoiceCache

STAGE = re.compile(r"\[[^\]]*\]")
STT_CREDITS_PER_MINUTE = 21          # measured on Scribe v2 (2026-10-19); ElevenLabs doesn't report it per request


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


class VoiceChannel:
    def __init__(self, tts: TextToSpeech, stt: SpeechToText, cache: VoiceCache,
                 line: Callable[[Audio, int], Audio] | None = None, name: str = "voice"):
        self.tts, self.stt, self.cache, self.line, self.name = tts, stt, cache, line, name

    def __call__(self, text: str) -> tuple[str, dict]:
        spoken = STAGE.sub(" ", text).strip()
        if not spoken:
            return text, {"channel": self.name, "skipped": "nothing to say aloud"}
        audio, tts_hit = self.cache.speak(self.tts, spoken)
        sent = self.line(audio, _seed(spoken)) if self.line else audio
        tr, stt_hit = self.cache.transcribe(self.stt, sent)
        credits = (0 if tts_hit else (audio.meta.get("credits") or 0)) + \
                  (0 if stt_hit else round(sent.seconds / 60 * STT_CREDITS_PER_MINUTE, 1))
        return tr.text, {"channel": self.name, "said": spoken, "credits": credits,
                         "tts_cached": tts_hit, "stt_cached": stt_hit,
                         "tts_s": None if tts_hit else audio.meta.get("seconds_to_make"),
                         "stt_s": None if stt_hit else tr.meta.get("seconds_to_make"),
                         "audio_s": round(audio.seconds, 2)}
