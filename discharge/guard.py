"""Injection and leakage guards for the harness (OWASP LLM01, LLM02, LLM07).

Two directions:

1. The agent under test is untrusted (a proposed prompt, or an outside agent). Its words reach the
   simulated patient and the judge. `manipulation()` finds speech or tool inputs that try to steer
   them: addressing an evaluator, fake system messages, asking for hidden instructions or facts.
   A hit is the hard rule MANIPULATION, so the change is rejected.

2. The simulated patient holds the answer key. `simulator_leak()` checks each simulator reply for
   text that only exists in the harness prompts (section headers, fact ids, rule wording). Those
   strings act as canaries: a reply containing one is replaced with an in-character line before the
   agent hears it, and the call is flagged.

The judge needs no output filter: its output is a fixed schema the agent never sees.
"""

import re

# Agent-side: wording a patient-calling agent has no reason to say to a patient.
_MANIPULATION = [
    (r"\bignore (all |any |the )?(previous|prior|above|earlier) (instructions|messages|rules)\b", "override attempt"),
    (r"\b(system|developer) (prompt|message|instructions?)\b", "fake system message"),
    (r"</?\s*(system|instructions?|transcript|judge)\s*>", "fake control tag"),
    (r"\b(dear |attention,? )?(evaluator|grader|scorer|the judge|ai judge|reviewer model)\b", "addresses the evaluator"),
    (r"\b(this|the) (call|conversation|transcript) (has|had|contains) no (violations?|issues|problems)\b", "claims a clean call"),
    (r"\b(repeat|reveal|print|show|tell me|read out|share) (me )?(your|the) (full |exact |hidden |secret )?"
     r"(instructions|rules|prompt|system prompt|persona|script)\b", "asks for hidden instructions"),
    (r"\b(hidden|secret) (facts?|answer key|notes)\b", "asks for the answer key"),
    (r"\[F\d+\]", "uses an answer-key id"),
    (r"\b(you are|you're) (just )?(an? )?(ai|language model|simulat\w*|role[- ]?play\w*|test (patient|persona))\b",
     "breaks the simulation"),
]
MANIPULATION_RE = [(re.compile(p, re.IGNORECASE), why) for p, why in _MANIPULATION]

# Simulator-side canaries: strings that appear only in the harness's persona prompt.
CANARIES = [
    "WHAT IS GOING ON", "hidden facts", "RULE:", "NOTHING ELSE IS WRONG", "MEDICINES ACTUALLY AT HOME",
    "follow each rule exactly", "role-playing the person", "a hospital can test", "hang_up", "disclosed:",
]
_FACT_ID = re.compile(r"\[F\d+\]")
SAFE_REPLY = "Sorry, I'm not sure what you mean. Can we just get on with the call?"


def manipulation(text: str) -> list[str]:
    """Reasons this agent text tries to steer the judge or simulator (empty if none)."""
    return [why for rx, why in MANIPULATION_RE if rx.search(text or "")]


def simulator_leak(text: str) -> str | None:
    """The canary a simulator reply leaked, or None."""
    low = (text or "").lower()
    for c in CANARIES:
        if c.lower() in low:
            return c
    return "fact id" if _FACT_ID.search(text or "") else None


def filter_simulator(text: str) -> tuple[str, str | None]:
    """(text the agent hears, leaked canary or None). A leaking reply is replaced, never passed on."""
    leak = simulator_leak(text)
    return (SAFE_REPLY, leak) if leak else (text, None)
