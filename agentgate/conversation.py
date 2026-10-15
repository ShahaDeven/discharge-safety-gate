"""Drive one call: the person on the line <-> the agent under test, with harness tools.

    record = run_call(agent, simulator, harness, ctx, max_agent_turns=12)

The simulator speaks first ("Hello?" or a voicemail greeting). Each agent turn may
call tools; the harness answers them at once and the agent continues until it speaks
without calling a tool. A call ends when a tool ends it (end_call, handoff, voicemail),
the person hangs up, the agent errors, or the turn cap is hit.
"""

from dataclasses import asdict, dataclass, field
from typing import Protocol

from .adapter import AgentTurn, CallAgent, CallContext, Speech, ToolCall, ToolHarness, ToolResult

MAX_TOOL_ROUNDS = 5    # tool round-trips allowed inside one agent turn


@dataclass
class SimTurn:
    say: str
    hang_up: bool = False
    disclosed: list[str] = field(default_factory=list)   # hidden-fact ids revealed in this reply
    asked: list[str] = field(default_factory=list)       # domains the agent's last message asked about
    usage: list[dict] = field(default_factory=list)


class Simulator(Protocol):
    def opening(self) -> str: ...

    def reply(self, transcript: list[dict], sim_turn: int) -> SimTurn: ...


@dataclass
class CallRecord:
    call_id: str
    events: list[dict] = field(default_factory=list)   # ordered: speech / tool_call / tool_result / error
    ended_by: str = ""
    agent_turns: int = 0
    usage: list[dict] = field(default_factory=list)
    served_by: list[str] = field(default_factory=list)

    def transcript(self) -> list[dict]:
        """Spoken lines only, in order: {"who": "agent"|"party", "text": ...}."""
        return [{"who": e["who"], "text": e["text"]} for e in self.events if e["kind"] == "speech"]

    def to_dict(self) -> dict:
        return asdict(self)


def _log_turn(record: CallRecord, turn: AgentTurn, harness: ToolHarness) -> tuple[list[ToolResult], bool]:
    """Append an agent turn's steps in order and run its tool calls. Returns (results, call_ended)."""
    record.usage.extend(turn.usage)
    if turn.served_by:
        record.served_by.append(turn.served_by)
    results: list[ToolResult] = []
    ended = False
    for step in turn.steps:
        if isinstance(step, Speech):
            if step.text.strip():
                record.events.append({"kind": "speech", "who": "agent", "text": step.text.strip()})
        elif isinstance(step, ToolCall):
            record.events.append({"kind": "tool_call", "who": "agent", "name": step.name,
                                  "input": step.input, "id": step.id})
            res = harness.run(step)
            record.events.append({"kind": "tool_result", "who": "harness", "name": res.name,
                                  "text": res.content, "id": res.tool_call_id, "ends_call": res.ends_call})
            results.append(res)
            ended = ended or res.ends_call
    if turn.error:
        record.events.append({"kind": "error", "who": "agent", "text": turn.error})
    return results, ended


def run_call(agent: CallAgent, simulator: Simulator, harness: ToolHarness, ctx: CallContext,
             max_agent_turns: int = 12) -> CallRecord:
    record = CallRecord(call_id=ctx.call_id)
    agent.start(ctx)
    opening = simulator.opening()
    record.events.append({"kind": "speech", "who": "party", "text": opening})
    heard = opening
    sim_turn = 0

    while True:
        if record.agent_turns >= max_agent_turns:
            record.ended_by = "turn_cap"
            return record
        record.agent_turns += 1
        turn = agent.hear(heard)
        results, ended = _log_turn(record, turn, harness)
        rounds = 0
        while results and not ended and not turn.error and rounds < MAX_TOOL_ROUNDS:
            turn = agent.tool_results(results)
            results, ended = _log_turn(record, turn, harness)
            rounds += 1
        if turn.error:
            record.ended_by = "agent_error"
            return record
        if ended:
            record.ended_by = next(e["name"] for e in reversed(record.events)
                                   if e["kind"] == "tool_result" and e["ends_call"])
            return record
        if results:                      # still calling tools after MAX_TOOL_ROUNDS
            record.ended_by = "tool_loop"
            return record

        sim_turn += 1
        reply = simulator.reply(record.transcript(), sim_turn)
        record.usage.extend(reply.usage)
        record.events.append({"kind": "speech", "who": "party", "text": reply.say,
                              "disclosed": reply.disclosed, "asked": reply.asked})
        if reply.hang_up:
            record.ended_by = "party_hung_up"
            return record
        heard = reply.say
