"""What a phone call does to speech, simulated deterministically.

A narrowband phone channel (the G.711 path most calls still cross somewhere):
1. band-limited to about 300-3400 Hz,
2. sampled at 8 kHz,
3. optionally line noise at a given signal-to-noise ratio, and lost 20 ms packets (silence, as on a VoIP or
   mobile call that drops packets), both at a fixed seed,
4. mu-law companded to 8 bits per sample and expanded back.

The same input and settings always give the same output, so a phone-line run is reproducible from cached
audio and doesn't depend on any provider's telephone setting.
"""

import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt

from .base import Audio

PHONE_RATE = 8000
LOW_HZ, HIGH_HZ = 300.0, 3400.0
MU = 255.0


def mu_law(x: np.ndarray) -> np.ndarray:
    """Encode floats in [-1, 1] to 8-bit mu-law codes and decode them back (the quantization is the point)."""
    y = np.sign(x) * np.log1p(MU * np.abs(x)) / np.log1p(MU)
    q = np.round((y + 1) / 2 * 255) / 255 * 2 - 1                 # 256 levels
    return np.sign(q) * np.expm1(np.abs(q) * np.log1p(MU)) / MU


PACKET_S = 0.020


def phone_line(audio: Audio, snr_db: float | None = None, loss: float = 0.0, seed: int = 0) -> Audio:
    """Pass audio through a simulated phone line.
    snr_db: line noise at this signal-to-noise ratio (for example 15), or None for none.
    loss: share of 20 ms packets dropped (for example 0.03)."""
    x = np.frombuffer(audio.pcm, dtype="<i2").astype(np.float64) / 32768.0
    meta = {**audio.meta, "channel": "phone", "band_hz": [LOW_HZ, HIGH_HZ], "rate": PHONE_RATE,
            "codec": "mu-law 8-bit", "snr_db": snr_db, "loss": loss, "seed": seed}
    if x.size == 0:
        return Audio(b"", PHONE_RATE, meta)
    sos = butter(6, [LOW_HZ, HIGH_HZ], btype="bandpass", fs=audio.rate, output="sos")
    x = sosfiltfilt(sos, x) if x.size > 60 else x
    g = np.gcd(PHONE_RATE, audio.rate)
    x = resample_poly(x, PHONE_RATE // g, audio.rate // g)
    rng = np.random.default_rng(seed)
    if snr_db is not None:
        speech = x[np.abs(x) > 0.01 * np.max(np.abs(x))] if np.any(x) else x      # loudness of the speech itself
        rms = float(np.sqrt(np.mean(speech ** 2))) if speech.size else 0.0
        x = x + rng.normal(0.0, rms / 10 ** (snr_db / 20), x.size)
    if loss > 0:
        n = int(PACKET_S * PHONE_RATE)
        for start in range(0, x.size, n):
            if rng.random() < loss:
                x[start:start + n] = 0.0
    x = mu_law(np.clip(x, -1.0, 1.0))
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    return Audio(pcm, PHONE_RATE, meta)
