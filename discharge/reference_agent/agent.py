"""Reference agent: one Claude conversation per call, with the harness tools.

Not a product. It exists so the harness has something realistic to test, and so a
prompt or model change can be pushed through the gate. Any other agent plugs in
through the same three methods (see agentgate/adapter.py).

Caching: tools + the fixed prompt are cached across every call of a candidate; the
per-call context follows; top-level cache_control caches the conversation so each
turn reads the earlier turns from cache.
"""

from agentgate import llm
from agentgate.adapter import AgentTurn, CallContext, Speech, ToolCall, ToolResult, ToolSpec

CONTEXT_VERSION = "ctx-v2"   # bump when render_context changes, so cached replays rerun


def render_context(ctx: CallContext) -> str:
    meds = "\n".join(f"- {m.line()}" for m in ctx.discharge_meds)
    signs = "\n".join(f"- {s}" for s in ctx.warning_signs)
    return f"""CALL CONTEXT (from the EHR; confidential until identity is verified)
Today's date: {ctx.call_date}
Organization: {ctx.organization}
Callback number: {ctx.callback_number}
State: {ctx.jurisdiction}
Patient: {ctx.patient_name}, date of birth {ctx.patient_dob}
Discharged: {ctx.discharge_date}, after: {ctx.discharge_diagnosis}
Discharge summary: {ctx.discharge_summary}
Discharge medication list:
{meds}
Warning signs to ask about for this condition:
{signs}
42 CFR Part 2 record (substance use disorder treatment): {"YES" if ctx.part2_record else "no"}"""


class ReferenceAgent:
    def __init__(self, prompt: str, tools: tuple[ToolSpec, ...], *, model: str, effort: str,
                 fallbacks: bool = False, max_tokens: int = 4000):
        self.prompt = prompt
        self.tools = [t.anthropic() for t in tools]
        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks
        self.max_tokens = max_tokens
        self.system: list = []
        self.messages: list = []

    def start(self, ctx: CallContext) -> None:
        self.system = [
            {"type": "text", "text": self.prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": render_context(ctx)},
        ]
        self.messages = []

    def hear(self, utterance: str) -> AgentTurn:
        self.messages.append({"role": "user", "content": f"[Person on the line]: {utterance}"})
        return self._turn()

    def tool_results(self, results: list[ToolResult]) -> AgentTurn:
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content} for r in results]})
        return self._turn()

    def _turn(self) -> AgentTurn:
        usage: list[dict] = []
        response = llm.create(model=self.model, system=self.system, messages=self.messages, tools=self.tools,
                              effort=self.effort, max_tokens=self.max_tokens, fallbacks=self.fallbacks,
                              role="agent", log=usage)
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            return AgentTurn(error=f"refusal ({category})", usage=usage, served_by=response.model)
        if response.stop_reason == "max_tokens":
            return AgentTurn(error="max_tokens", usage=usage, served_by=response.model)
        # Append the full content (thinking blocks included) so the next turn keeps them.
        self.messages.append({"role": "assistant", "content": response.content})
        steps = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                steps.append(Speech(block.text))
            elif block.type == "tool_use":
                steps.append(ToolCall(block.id, block.name, dict(block.input)))
        return AgentTurn(steps=steps, usage=usage, served_by=response.model)
