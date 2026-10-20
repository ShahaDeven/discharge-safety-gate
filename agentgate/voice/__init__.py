"""Optional voice layer: text to speech, a phone-line simulation, speech to text, and caches for each step.

The harness never needs it: every text test runs without it. Provider SDK calls live only in their adapter
(elevenlabs.py); everything else sees the small interfaces in base.py.
"""
