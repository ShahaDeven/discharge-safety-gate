"""ElevenLabs Agents as the agent under test: the WebSocket bridge against a fake ElevenLabs server on localhost,
and the evals module's free parts (client tools, test export, candidates). No network, no API key, no credits."""

import json
import os
import threading
import time

os.environ.setdefault("DSG_TOKEN", "test-token-0123456789")

import httpx
import pytest
from websockets.sync.server import serve

from agentgate.adapter import Speech, ToolCall, ToolResult
from agentgate.conversation import run_call
from agentgate.elevenagents import ElevenAgent, ElevenAgentError, dynamic_variables, signed_url
from agentgate.simulator import ScriptedSimulator
from discharge.tools import TOOLS, DischargeHarness


def said(text, rid="r1"):
    return {"type": "agent_response", "agent_response_event": {"agent_response": text, "event_id": 1, "response_id": rid}}


def tool(name, cid, **params):
    return {"type": "client_tool_call", "client_tool_call": {"tool_name": name, "tool_call_id": cid, "parameters": params,
                                                             "event_id": 2, "expects_response": True}}


DONE = {"type": "agent_response_complete"}


class FakeElevenLabs:
    """A scripted ElevenLabs Agents server: answers the opening, pings once, then replies via `respond(msg)`."""

    def __init__(self, respond, close_after=None):
        self.got, self.respond, self.close_after, self.closed = [], respond, close_after, threading.Event()
        self.server = serve(self.handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handler(self, ws):
        try:
            for raw in ws:
                msg = json.loads(raw)
                self.got.append(msg)
                if msg["type"] == "conversation_initiation_client_data":
                    ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 5}}))
                    ws.send(json.dumps({"type": "conversation_initiation_metadata",
                                        "conversation_initiation_metadata_event": {"conversation_id": "conv_1"}}))
                    continue
                for out in self.respond(msg):
                    ws.send(json.dumps(out))
                if self.close_after and msg.get("text") == self.close_after:
                    ws.close()
                    return
        finally:
            self.closed.set()

    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    def agent(self, **kw):
        return ElevenAgent("agent_test", TOOLS, url_for=self.url, quiet=0.3, settle=0.2, turn_timeout=kw.pop("turn_timeout", 3), **kw)

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def ctx(pilot):
    sc = pilot["P01"]
    return sc, sc.context("P01#scenario-id-must-not-leak")


def _wait(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        time.sleep(0.02)
    return pred()


def test_conversation_opens_text_only_with_the_call_context_and_answers_pings(ctx):
    sc, c = ctx
    fake = FakeElevenLabs(lambda m: [])
    agent = fake.agent()
    try:
        agent.start(c)
        init = fake.got[0]
        assert init["conversation_config_override"] == {"conversation": {"text_only": True}}
        assert init["dynamic_variables"]["patient_name"] == sc.patient.name and agent.conversation_id == "conv_1"
        assert "scenario-id-must-not-leak" not in json.dumps(init)               # never the call id or scenario
        assert all(isinstance(v, (str, bool)) for v in init["dynamic_variables"].values())
        assert _wait(lambda: {"type": "pong", "event_id": 7} in fake.got)        # the reader thread answers pings
    finally:
        agent.close()
        fake.stop()


def test_speech_and_tool_calls_come_back_in_order_and_results_go_back(ctx):
    _, c = ctx

    def respond(m):
        if m["type"] == "user_message":
            return [said("Thank you, one moment."), tool("verify_identity", "tc1", caller_name="Rosa", relationship="self",
                                                         patient_name="Rosa Diaz", patient_date_of_birth="1950-01-02")]
        if m["type"] == "client_tool_result":
            return [said("You're verified. How are you feeling?", "r2"), DONE]
        return []
    fake = FakeElevenLabs(respond)
    agent = fake.agent()
    try:
        agent.start(c)
        turn = agent.hear("This is Rosa Diaz, born January second 1950.")
        assert [type(s) for s in turn.steps] == [Speech, ToolCall] and turn.steps[1].name == "verify_identity"
        assert turn.served_by == "elevenlabs:conv_1"
        turn = agent.tool_results([ToolResult("tc1", "verify_identity", "VERIFIED: you are speaking with the patient.")])
        sent = next(m for m in fake.got if m["type"] == "client_tool_result")
        assert sent == {"type": "client_tool_result", "tool_call_id": "tc1", "is_error": False,
                        "result": "VERIFIED: you are speaking with the patient."}
        assert turn.speech == "You're verified. How are you feeling?"
    finally:
        agent.close()
        fake.stop()


def test_streamed_parts_become_one_line_and_a_late_full_reply_does_not_repeat_it(ctx):
    _, c = ctx
    parts = [{"type": "agent_chat_response_part", "text_response_part": {"text": t, "type": k, "response_id": "r9"}}
             for t, k in (("", "start"), ("Hello, ", "delta"), ("this is the hospital.", "delta"), ("", "stop"))]
    fake = FakeElevenLabs(lambda m: parts + [said("Hello, this is the hospital.", "r9")] if m["type"] == "user_message" else [])
    agent = fake.agent()
    try:
        agent.start(c)
        assert [s.text for s in agent.hear("Hello?").steps] == ["Hello, this is the hospital."]
    finally:
        agent.close()
        fake.stop()


def test_prefixed_tool_names_map_to_harness_tools_and_unknown_tools_fail_the_call(ctx):
    _, c = ctx
    replies = {"bye": [said("Goodbye."), tool("dsg_end_call", "e1", summary="done")],
               "hack": [tool("delete_records", "x1")]}
    fake = FakeElevenLabs(lambda m: replies.get(m.get("text"), []))
    agent = fake.agent()
    try:
        agent.start(c)
        assert agent.hear("bye").tool_calls[0].name == "end_call"
        with pytest.raises(ElevenAgentError, match="unknown tool"):
            agent.hear("hack")
    finally:
        agent.close()
        fake.stop()


def test_no_reply_fails_the_call_instead_of_hanging(ctx):
    _, c = ctx
    fake = FakeElevenLabs(lambda m: [])
    agent = fake.agent(turn_timeout=0.5)
    try:
        agent.start(c)
        t0 = time.monotonic()
        with pytest.raises(ElevenAgentError, match="no reply"):
            agent.hear("Hello?")
        assert time.monotonic() - t0 < 2
    finally:
        agent.close()
        fake.stop()


def test_the_platform_ending_the_conversation_ends_the_call_as_an_agent_error(ctx):
    _, c = ctx
    fake = FakeElevenLabs(lambda m: [said("Okay, goodbye.")], close_after="stop calling me")
    agent = fake.agent()
    try:
        agent.start(c)
        turn = agent.hear("stop calling me")
        assert turn.speech == "Okay, goodbye." and turn.error.startswith("conversation ended by ElevenLabs")
    finally:
        agent.close()
        fake.stop()


def test_a_full_call_through_run_call_scores_tool_calls_and_closes_the_conversation(pilot):
    sc = pilot["P01"]

    def respond(m):
        if m["type"] == "user_message":
            return [said(f"Hello, this is an automated assistant. Is this {sc.patient.name.split()[0]}?"), DONE] \
                if m["text"] == "Hello?" else [said("Thank you for your time."), tool("dsg_end_call", "e1", summary="ok")]
        return []
    fake = FakeElevenLabs(respond)
    try:
        rec = run_call(fake.agent(), ScriptedSimulator("Hello?", ["No thanks, bye."]), DischargeHarness(sc),
                       sc.context("t"), max_agent_turns=4)
        assert rec.ended_by == "end_call"
        assert [e["name"] for e in rec.events if e["kind"] == "tool_call"] == ["end_call"]
        assert fake.closed.wait(3)                                              # run_call closed the conversation
    finally:
        fake.stop()


def test_signed_url_sends_the_key_in_a_header_and_refuses_anything_but_wss():
    seen = {}

    def handler(request):
        seen["key"], seen["url"] = request.headers.get("xi-api-key"), str(request.url)
        return httpx.Response(200, json={"signed_url": seen.get("reply", "wss://api.elevenlabs.io/v1/convai/x?token=t")})
    url = signed_url("agent_1", version_id="v_9", key=lambda: "sk_secret", transport=httpx.MockTransport(handler))
    assert url.startswith("wss://") and seen["key"] == "sk_secret" and "sk_secret" not in seen["url"]
    assert "agent_id=agent_1" in seen["url"] and "version_id=v_9" in seen["url"]
    for bad in ("ws://api.elevenlabs.io/x", "wss://evil.example/x", "wss://elevenlabs.io.evil.example/x"):
        seen["reply"] = bad
        with pytest.raises(ElevenAgentError, match="elevenlabs.io"):
            signed_url("agent_1", key=lambda: "k", transport=httpx.MockTransport(handler))


def test_dynamic_variables_carry_the_call_context_only(pilot):
    sc = pilot["P01"]
    v = dynamic_variables(sc.context("secret-call-id"))
    assert v["patient_first_name"] == sc.patient.name.split()[0] and "secret-call-id" not in json.dumps(v)
    assert set(v) >= {"organization", "callback_number", "patient_dob", "discharge_meds", "warning_signs", "part2_record"}


# ---------------------------------------------------------------- evals/elevenagents.py (free parts)

def test_client_tools_match_the_harness_tools_with_every_parameter_described():
    from evals.elevenagents import client_tools
    tools = client_tools()
    assert [t["name"] for t in tools] == [t.name for t in TOOLS]
    assert all(t["type"] == "client" and t["expects_response"] for t in tools)
    assert all(p.get("description") for t in tools for p in t["parameters"]["properties"].values())


def test_exported_agent_tests_cover_three_types_and_never_the_sealed_set():
    from evals.elevenagents import TEST_CASES, test_definitions
    defs = test_definitions()
    assert {d["kind"] for d in defs} == {"simulation", "llm", "tool"} and len(defs) == len(TEST_CASES)
    assert not any(d["scenario"].startswith("H-") for d in defs)
    sim = next(d["body"] for d in defs if d["kind"] == "simulation")
    assert sim["tool_mock_config"]["mocking_strategy"] == "all" and "<id of verify_identity>" in sim["tool_mock_overrides"]
    ids = test_definitions({t.name: f"tool_{t.name}" for t in TOOLS})
    assert not any("<id of" in json.dumps(d["body"]) for d in ids)


def test_candidates_are_pinned_by_version_and_differ_by_branch(monkeypatch):
    from evals import elevenagents as ea
    for name in ("ELEVENAGENT_EA_BRANCH", "ELEVENAGENT_EA_R_BRANCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ELEVENAGENT_ID", "agent_1")
    monkeypatch.setenv("ELEVENAGENT_EA_VERSION", "v_main")
    monkeypatch.setenv("ELEVENAGENT_EA_R_VERSION", "v_reg")
    assert ea.configured("EA") is None and ea.configured("EA-R") is None
    a, b = ea.ea_candidate("EA"), ea.ea_candidate("EA-R")
    assert a.fingerprint != b.fingerprint
    monkeypatch.setenv("ELEVENAGENT_EA_VERSION", "v_main_2")
    assert ea.ea_candidate("EA").fingerprint != a.fingerprint                  # a new version reruns its calls
    monkeypatch.delenv("ELEVENAGENT_EA_R_VERSION")
    assert "ELEVENAGENT_EA_R_VERSION" in ea.configured("EA-R")
    assert ea.SCENARIOS and not any(s.startswith("H-") for s in ea.SCENARIOS)
