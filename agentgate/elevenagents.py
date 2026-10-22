"""An agent built on ElevenLabs Agents, put through the gate as the agent under test.

    agent = ElevenAgent(agent_id, tools, version_id="...")       # or branch_id=...; a version is immutable
    agent.start(ctx)                                            # opens a text-only conversation
    turn = agent.hear("Hello?")
    turn = agent.tool_results([...])
    agent.close()                                               # run_call does this when the call ends

The conversation runs over the ElevenLabs Agents WebSocket in **text-only** mode (no audio, no voice pricing).
The harness tools are **client tools** on the ElevenLabs agent: when it calls one, the bridge hands the call to
the harness like any agent's tool call and sends the harness's answer back as the client-tool result. Outcomes
are read from those tool calls, exactly as for every other agent.

What ElevenLabs receives: the call context as dynamic variables ({{patient_name}}, {{warning_signs}}...), the
person's lines and the tool results. Never the scenario id, the hidden facts or the answer key.

When is the agent's turn over? The protocol has no explicit "your turn" for text, so a turn ends when:
- the agent called a tool that needs an answer, and nothing more arrives for TOOL_SETTLE_S; or
- the server says the response is complete (agent_response_complete); or
- QUIET_S passes with nothing new after the agent spoke.
A reader thread answers the server's pings, so a slow simulated patient never drops the connection.

Failures (no reply, a closed socket, an unknown tool, a server error) raise ElevenAgentError: the call fails and
is never cached, like any outside agent. Only the fixed ElevenLabs host is ever contacted.
"""

import json
import queue
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .adapter import AgentTurn, CallContext, Speech, ToolCall, ToolResult, ToolSpec

API = "https://api.elevenlabs.io/v1"
TURN_TIMEOUT_S = 45.0       # longest wait for the first sign of a reply
QUIET_S = 2.5               # after the agent spoke: this long with nothing new ends its turn
TOOL_SETTLE_S = 0.6         # after a tool call: wait briefly for parallel calls, then hand them to the harness
OPEN_TIMEOUT_S = 15.0
MAX_STEPS = 8
MAX_SPEECH = 2_000
MAX_MESSAGE_BYTES = 256_000
PREFIX = "dsg_"             # tools may be named dsg_end_call etc. if a name clashes with a platform tool
FATAL = {"client_error", "guardrail_triggered"}


class ElevenAgentError(RuntimeError):
    """The conversation failed (not the agent's behavior): never cached, retried next run."""


def dynamic_variables(ctx: CallContext) -> dict:
    """The call context as ElevenLabs dynamic variables (strings and booleans only). No call id, no extras."""
    return {
        "organization": ctx.organization, "callback_number": ctx.callback_number, "state": ctx.jurisdiction,
        "patient_name": ctx.patient_name, "patient_first_name": ctx.patient_name.split()[0],
        "patient_dob": ctx.patient_dob, "discharge_date": ctx.discharge_date,
        "discharge_diagnosis": ctx.discharge_diagnosis, "discharge_summary": ctx.discharge_summary,
        "discharge_meds": "; ".join(m.line() for m in ctx.discharge_meds),
        "warning_signs": "; ".join(ctx.warning_signs), "call_date": ctx.call_date or "",
        "part2_record": bool(ctx.part2_record),
    }


def signed_url(agent_id: str, branch_id: str | None = None, version_id: str | None = None,
               key: Callable[[], str] | None = None, transport: httpx.BaseTransport | None = None) -> str:
    """A one-time WebSocket URL for a private agent (the API key never goes into the URL)."""
    if key is None:
        from .voice.elevenlabs import _key as key                      # the same .env key as the voice work
    params = {"agent_id": agent_id, **({"branch_id": branch_id} if branch_id else {}),
              **({"version_id": version_id} if version_id else {})}
    try:
        with httpx.Client(timeout=OPEN_TIMEOUT_S, follow_redirects=False, trust_env=False, transport=transport) as c:
            r = c.get(f"{API}/convai/conversation/get-signed-url", params=params, headers={"xi-api-key": key()})
    except httpx.HTTPError as exc:
        raise ElevenAgentError(f"could not reach ElevenLabs: {type(exc).__name__}") from None
    if r.status_code != 200:
        raise ElevenAgentError(f"signed URL refused: HTTP {r.status_code} (check the agent id, branch or version, "
                               f"and that the API key may use ElevenAgents)")
    url = r.json().get("signed_url", "")
    host = urlsplit(url).hostname or ""
    if not url.startswith("wss://") or not (host == "elevenlabs.io" or host.endswith(".elevenlabs.io")):
        raise ElevenAgentError("signed URL is not a wss:// address on elevenlabs.io")
    return url


def _connect(url: str):
    from websockets.sync.client import connect
    return connect(url, open_timeout=OPEN_TIMEOUT_S, max_size=MAX_MESSAGE_BYTES, close_timeout=2)


class ElevenAgent:
    def __init__(self, agent_id: str, tools: tuple[ToolSpec, ...], *, branch_id: str | None = None,
                 version_id: str | None = None, url_for: Callable[[], str] | None = None,
                 connect: Callable[[str], object] = _connect, turn_timeout: float = TURN_TIMEOUT_S,
                 quiet: float = QUIET_S, settle: float = TOOL_SETTLE_S):
        self.agent_id, self.branch_id, self.version_id = agent_id, branch_id, version_id
        self.tool_names = {t.name for t in tools}
        self.url_for = url_for or (lambda: signed_url(agent_id, branch_id, version_id))
        self.connect = connect
        self.turn_timeout, self.quiet, self.settle = turn_timeout, quiet, settle
        self.ws = None
        self.events: queue.Queue = queue.Queue()
        self.reader: threading.Thread | None = None
        self.conversation_id: str | None = None
        self.awaiting: set[str] = set()            # tool calls the server is waiting on
        self._early: list[dict] = []               # events that arrived while the conversation was opening
        self._send_lock = threading.Lock()

    # -- the three methods
    def start(self, ctx: CallContext) -> None:
        try:
            self.ws = self.connect(self.url_for())
        except ElevenAgentError:
            raise
        except Exception as exc:
            raise ElevenAgentError(f"could not open the conversation: {type(exc).__name__}") from None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self._send({"type": "conversation_initiation_client_data", "dynamic_variables": dynamic_variables(ctx),
                    "conversation_config_override": {"conversation": {"text_only": True}}})
        deadline = time.monotonic() + OPEN_TIMEOUT_S
        while self.conversation_id is None:
            ev = self._next(deadline - time.monotonic())
            if ev is None:
                raise ElevenAgentError("the conversation did not start in time")
            if ev.get("type") == "conversation_initiation_metadata":
                meta = ev.get("conversation_initiation_metadata_event") or {}
                self.conversation_id = meta.get("conversation_id") or "unknown"
            else:
                self._check(ev)
                self._early.append(ev)               # e.g. a first message, if the agent has one

    def hear(self, utterance: str) -> AgentTurn:
        self._send({"type": "user_message", "text": utterance})
        return self._collect()

    def tool_results(self, results: list[ToolResult]) -> AgentTurn:
        for r in results:
            if r.tool_call_id in self.awaiting:
                self.awaiting.discard(r.tool_call_id)
                self._send({"type": "client_tool_result", "tool_call_id": r.tool_call_id, "result": r.content,
                            "is_error": r.content.startswith("ERROR")})
        return self._collect()

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        if self.reader is not None:
            self.reader.join(timeout=3)

    # -- plumbing
    def _send(self, msg: dict) -> None:
        try:
            with self._send_lock:
                self.ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception as exc:
            raise ElevenAgentError(f"the conversation was closed ({type(exc).__name__})") from None

    def _read(self) -> None:
        """Reader thread: pings answered here; everything else queued for the turn being collected."""
        try:
            for raw in self.ws:
                try:
                    ev = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "ping":
                    eid = (ev.get("ping_event") or {}).get("event_id")
                    try:
                        self._send({"type": "pong", "event_id": eid})
                    except ElevenAgentError:
                        break
                    continue
                self.events.put(ev)
            self.events.put({"type": "_closed", "reason": "the server closed the conversation"})
        except Exception as exc:
            self.events.put({"type": "_closed", "reason": f"connection lost ({type(exc).__name__})"})

    def _next(self, timeout: float) -> dict | None:
        try:
            return self.events.get(timeout=max(0.01, timeout))
        except queue.Empty:
            return None

    @staticmethod
    def _check(ev: dict) -> None:
        kind = ev.get("type")
        if kind in FATAL:
            detail = json.dumps({k: v for k, v in ev.items() if k != "type"})[:200]
            raise ElevenAgentError(f"ElevenLabs reported {kind}: {detail}")
        if kind == "queue_status" and (ev.get("queue_status_event") or ev).get("status") == "timed_out":
            raise ElevenAgentError("ElevenLabs queue timed out (too many conversations at once)")

    def _collect(self) -> AgentTurn:
        steps: list = []
        by_response: dict[str, int] = {}            # response id -> index of its Speech step
        parts: dict[str, list[str]] = {}
        spoke = tools = False
        ended = None
        deadline = time.monotonic() + self.turn_timeout
        early, self._early = self._early, []
        while True:
            if early:
                ev = early.pop(0)
            else:
                wait = self.settle if tools else self.quiet if spoke else deadline - time.monotonic()
                ev = self._next(min(wait, deadline - time.monotonic()))
            if ev is None:
                if spoke or tools:
                    break
                raise ElevenAgentError(f"no reply within {self.turn_timeout:g} s")
            kind = ev.get("type")
            self._check(ev)
            if kind == "_closed":
                ended = ev["reason"]
                break
            if kind == "agent_response":
                e = ev.get("agent_response_event") or {}
                text, rid = (e.get("agent_response") or "").strip(), e.get("response_id") or e.get("event_id")
                if text:
                    if rid in by_response:                           # already built from streamed parts
                        steps[by_response[rid]] = Speech(text[:MAX_SPEECH])
                    else:
                        by_response[rid] = len(steps)
                        steps.append(Speech(text[:MAX_SPEECH]))
                    spoke = True
            elif kind == "agent_chat_response_part":
                p = ev.get("text_response_part") or {}
                rid = p.get("response_id")
                parts.setdefault(rid, []).append(p.get("text") or "")
                if p.get("type") == "stop" and rid not in by_response:
                    text = "".join(parts.pop(rid, [])).strip()
                    if text:
                        by_response[rid] = len(steps)
                        steps.append(Speech(text[:MAX_SPEECH]))
                        spoke = True
            elif kind == "agent_response_correction":
                e = ev.get("agent_response_correction_event") or {}
                for i, s in enumerate(steps):
                    if isinstance(s, Speech) and s.text == (e.get("original_agent_response") or "").strip():
                        steps[i] = Speech((e.get("corrected_agent_response") or "").strip()[:MAX_SPEECH])
            elif kind == "client_tool_call":
                c = ev.get("client_tool_call") or {}
                name = c.get("tool_name") or ""
                name = name[len(PREFIX):] if name.startswith(PREFIX) else name
                if name not in self.tool_names:
                    raise ElevenAgentError(f"the agent called an unknown tool {name[:40]!r}")
                cid = str(c.get("tool_call_id") or "")
                if not cid:
                    raise ElevenAgentError("a tool call without an id")
                if c.get("expects_response", True):
                    self.awaiting.add(cid)
                steps.append(ToolCall(cid, name, c.get("parameters") or {}))
                tools = True
            elif kind == "agent_response_complete" and (spoke or tools):
                break
            if len(steps) > MAX_STEPS:
                raise ElevenAgentError(f"more than {MAX_STEPS} steps in one turn")
            if time.monotonic() > deadline and (spoke or tools):
                break
        if ended and not any(isinstance(s, ToolCall) for s in steps):
            # The platform ended the conversation itself (its own end-call tool, a time limit): the agent's
            # behavior, recorded as an error turn so the call ends and is scored as it stands.
            return AgentTurn(steps=steps, error=f"conversation ended by ElevenLabs: {ended}",
                             served_by=f"elevenlabs:{self.conversation_id}")
        return AgentTurn(steps=steps, served_by=f"elevenlabs:{self.conversation_id}")
