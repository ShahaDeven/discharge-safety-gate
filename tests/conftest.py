"""Shared fixtures: the pilot scenarios and a scripted agent. Nothing here calls the API."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentgate.adapter import AgentTurn, CallContext, Speech, ToolCall, ToolResult  # noqa: E402
from discharge.scenario import load_scenarios  # noqa: E402


@pytest.fixture(scope="session")
def pilot():
    return {s.id: s for s in load_scenarios(ROOT / "discharge" / "scenarios" / "pilot.yaml")}


class ScriptedAgent:
    """Plays back a fixed list of turns: one per hear() or tool_results() call."""

    def __init__(self, turns: list[list]):
        self.turns = [AgentTurn(steps=t) for t in turns]
        self.heard: list[str] = []
        self.results: list[list[ToolResult]] = []
        self.ctx: CallContext | None = None

    def start(self, ctx):
        self.ctx = ctx

    def _next(self) -> AgentTurn:
        return self.turns.pop(0) if self.turns else AgentTurn(steps=[ToolCall("end", "end_call", {"summary": "x"})])

    def hear(self, utterance):
        self.heard.append(utterance)
        return self._next()

    def tool_results(self, results):
        self.results.append(results)
        return self._next()


def say(text):
    return Speech(text)


def tool(tool_name, /, **inp):
    # positional-only: tool inputs can themselves be called "name" (verify_identity)
    return ToolCall(f"{tool_name}-{len(inp)}", tool_name, inp)


def event_speech(who, text, **extra):
    return {"kind": "speech", "who": who, "text": text, **extra}


def event_call(tool_name, /, **inp):
    return {"kind": "tool_call", "who": "agent", "name": tool_name, "input": inp, "id": tool_name}


def event_result(name, text, ends_call=False):
    return {"kind": "tool_result", "who": "harness", "name": name, "text": text, "id": name, "ends_call": ends_call}
