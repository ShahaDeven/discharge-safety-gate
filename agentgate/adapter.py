"""The contract between the harness and the agent under test.

An agent never talks to the simulator directly. The harness passes it what the person
on the line said, runs any harness-owned tools it calls (verify_identity, escalate, ...)
and hands back their results. Outcomes are read from those tool calls, not from prose,
so recall and most compliance checks need no judge.

    agent.start(ctx)                      # once per call, before anyone speaks
    turn = agent.hear("Hello?")           # what the person on the line said
    turn = agent.tool_results([...])      # results of the tools named in the last turn

A turn carries speech and tool calls in the order the agent produced them, because
"said the diagnosis, then verified identity" is a violation and the reverse is not.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Med:
    name: str
    dose: str = ""
    frequency: str = ""
    note: str = ""

    def line(self) -> str:
        parts = [self.name, self.dose, self.frequency]
        text = " ".join(p for p in parts if p)
        return f"{text} ({self.note})" if self.note else text


@dataclass(frozen=True)
class CallContext:
    """What a real deployment would pull from the EHR before dialing."""
    call_id: str
    organization: str
    callback_number: str
    jurisdiction: str
    patient_name: str
    patient_dob: str                 # YYYY-MM-DD
    discharge_date: str
    discharge_diagnosis: str
    discharge_summary: str
    discharge_meds: tuple[Med, ...]
    warning_signs: tuple[str, ...]   # condition-specific symptoms to ask about
    call_date: str = ""              # today, YYYY-MM-DD
    part2_record: bool = False       # record is protected by 42 CFR Part 2
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    ends_call: bool = False


@dataclass(frozen=True)
class Speech:
    text: str


# One agent turn: speech and tool calls interleaved in the order produced.
Step = Speech | ToolCall


@dataclass
class AgentTurn:
    steps: list[Step] = field(default_factory=list)
    error: str | None = None          # "refusal", "max_tokens", "api_error: ..." - ends the call
    served_by: str | None = None      # model that produced the turn, when the adapter knows it
    usage: list[dict] = field(default_factory=list)

    @property
    def speech(self) -> str:
        return " ".join(s.text for s in self.steps if isinstance(s, Speech)).strip()

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [s for s in self.steps if isinstance(s, ToolCall)]


class CallAgent(Protocol):
    """Implement these three methods (in process, or behind adapters/http_agent.py)."""

    def start(self, ctx: CallContext) -> None: ...

    def hear(self, utterance: str) -> AgentTurn: ...

    def tool_results(self, results: list[ToolResult]) -> AgentTurn: ...


@dataclass(frozen=True)
class ToolSpec:
    """A harness-owned tool. The company layer defines the set; the agent sees name,
    description and schema (in Anthropic tool format for the reference agent)."""
    name: str
    description: str
    input_schema: dict
    ends_call: bool = False

    def anthropic(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "strict": True}


class ToolHarness(Protocol):
    """Runs harness-owned tools against the scenario's ground truth (who is really on the line)."""

    specs: tuple[ToolSpec, ...]

    def run(self, call: ToolCall) -> ToolResult: ...
