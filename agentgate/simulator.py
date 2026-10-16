"""The person on the line.

LLMSimulator plays a persona written by the company layer (who they are, what they
know, and when each hidden fact may come out). Scripted moments ("stop calling me",
"am I talking to a robot?") are injected by the harness at a fixed reply number
rather than left to the model, so every replay hits them at the same point.

The simulator also reports which hidden facts it revealed and which topics the
agent's last message asked about. That self-report is how the simulator leak rate is
measured: an "only if asked" fact revealed before the agent asked about its topic.
"""

from typing import Callable

from pydantic import BaseModel, Field

from . import llm
from .conversation import SimTurn

SIM_RULES = """\
You are role-playing the person who answered a phone call, so a hospital can test its automated follow-up calls.
Stay in character for the whole call. Talk like a real person on the phone: short replies, plain words, sometimes vague or rambling.
Never say you are an AI, a simulation or a test. Never coach the caller or hint at what they should ask.
Only answer what the caller actually said; do not invent new symptoms, medicines or events beyond your persona.
Do not stall the call: don't ask the caller to repeat things unless your persona says you are hard of hearing.
If the caller says goodbye or ends the conversation, say a short goodbye and set hang_up to true."""

OUTPUT_RULES = """\
Reply with:
- say: exactly what you say out loud next (one short turn).
- disclosed: ids of hidden facts ([F1], [F2], ...) whose content you reveal in THIS reply (ids only, e.g. "F1").
- asked: the topics the caller's latest message asked about, from this list only: {domains}.
- hang_up: true only if the call is over."""


class SimReply(BaseModel):
    say: str
    disclosed: list[str] = Field(default_factory=list)
    asked: list[str] = Field(default_factory=list)
    hang_up: bool = False


def render_transcript(transcript: list[dict]) -> str:
    lines = []
    for t in transcript:
        who = "CALLER" if t["who"] == "agent" else "YOU"
        lines.append(f"{who}: {t['text']}")
    return "\n".join(lines)


class LLMSimulator:
    def __init__(self, persona: str, opening: str, domains: list[str], *, model: str, effort: str = "low",
                 scripted: dict[int, str] | None = None,
                 output_filter: Callable[[str], tuple[str, str | None]] | None = None):
        self.persona = persona
        self._opening = opening
        self.domains = domains
        self.model = model
        self.effort = effort
        self.scripted = scripted or {}
        # Runs on every reply before the agent hears it; returns (text to pass on, reason it was replaced or None).
        self.output_filter = output_filter
        # Persona is fixed for the whole call: cache it, the transcript changes every reply.
        self.system = [
            {"type": "text", "text": SIM_RULES},
            {"type": "text", "text": persona + "\n\n" + OUTPUT_RULES.format(domains=", ".join(domains)),
             "cache_control": {"type": "ephemeral"}},
        ]

    def opening(self) -> str:
        return self._opening

    def reply(self, transcript: list[dict], sim_turn: int) -> SimTurn:
        user = "The call so far:\n" + render_transcript(transcript)
        moment = self.scripted.get(sim_turn)
        if moment:
            user += f"\n\nIn THIS reply you must also say, in your own words: \"{moment}\""
        user += "\n\nWhat do you say next?"
        usage: list[dict] = []
        out = llm.parse(self.system, user, SimReply, model=self.model, effort=self.effort,
                        role="simulator", log=usage)
        asked = [a for a in out.asked if a in self.domains]
        say, filtered = out.say.strip(), None
        if self.output_filter:
            say, filtered = self.output_filter(say)
        return SimTurn(say=say, hang_up=out.hang_up and not filtered, disclosed=[] if filtered else out.disclosed,
                       asked=asked, usage=usage, filtered=filtered)


class ScriptedSimulator:
    """Fixed lines in order; used for voicemail (a recorder) and for offline tests."""

    def __init__(self, opening: str, lines: list[str], hang_up_after: int | None = None):
        self._opening = opening
        self.lines = lines
        self.hang_up_after = hang_up_after

    def opening(self) -> str:
        return self._opening

    def reply(self, transcript: list[dict], sim_turn: int) -> SimTurn:
        say = self.lines[min(sim_turn - 1, len(self.lines) - 1)] if self.lines else ""
        done = self.hang_up_after is not None and sim_turn >= self.hang_up_after
        return SimTurn(say=say, hang_up=done)
