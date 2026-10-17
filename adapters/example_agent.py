"""Example outside agent: the reference agent (C0's prompt) behind the HTTP contract (HTTP_CONTRACT.md).

    1. Register it on the Agents page:  http://127.0.0.1:8100/agent   with "runs on this machine" ticked.
       Copy the signing secret the page shows.
    2. Start it (PowerShell):
         $env:DSG_AGENT_SECRET = "<secret>"
         D:\\Main_Python\\.venv\\Scripts\\python.exe -m adapters.example_agent --port 8100 --max-usd 0.30
    3. Click "Test connection", then "Run the gate".

Because it is C0 behind a URL, the gate should find nothing worse than C0: a working end-to-end check.
Its model calls are billed to the same API key but not to the console's caps, so it keeps its own cap.
"""

import argparse
import asyncio
import json
import os
import threading
from collections import OrderedDict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agentgate import llm
from agentgate.adapter import AgentTurn, CallContext, Med, Speech, ToolResult, ToolSpec
from agentgate.http_agent import verify
from discharge import config
from discharge.reference_agent.agent import ReferenceAgent

SECRET = os.environ.get("DSG_AGENT_SECRET", "").encode()
PROMPT = (config.PROMPT_DIR / "c0_baseline.md").read_text(encoding="utf-8")
MAX_CALLS = 64                          # calls kept in memory; the oldest is dropped

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
calls: OrderedDict[str, ReferenceAgent] = OrderedDict()
seen: dict[str, float] = {}             # nonces already used (verify() refuses replays)
state = {"spent": 0.0, "max_usd": 0.30}
lock = threading.Lock()


def context(c: dict, call_id: str) -> CallContext:
    meds = tuple(Med(**m) for m in c["discharge_meds"])
    return CallContext(call_id=call_id, **{**c, "discharge_meds": meds, "warning_signs": tuple(c["warning_signs"])})


def reply(turn: AgentTurn) -> dict:
    with lock:
        state["spent"] += llm.cost_usd(turn.usage)
    return {"steps": [{"type": "speech", "text": s.text} if isinstance(s, Speech)
                      else {"type": "tool_call", "id": s.id, "name": s.name, "input": s.input} for s in turn.steps],
            "error": turn.error}


def handle(m: dict) -> dict:
    if m["type"] == "start":
        tools = tuple(ToolSpec(t["name"], t["description"], t["input_schema"]) for t in m["tools"])
        agent = ReferenceAgent(PROMPT, tools, model=config.AGENT_MODEL, effort=config.AGENT_EFFORT)
        agent.start(context(m["context"], m["call_id"]))
        with lock:
            calls[m["call_id"]] = agent
            while len(calls) > MAX_CALLS:
                calls.popitem(last=False)
        return {"ok": True}
    agent = calls[m["call_id"]]
    if m["type"] == "hear":
        return reply(agent.hear(m["utterance"]))
    return reply(agent.tool_results([ToolResult(r["tool_call_id"], r["name"], r["content"]) for r in m["results"]]))


@app.post("/agent")
async def endpoint(request: Request):
    body = await request.body()
    if not verify(SECRET, body, request.headers, seen):
        return JSONResponse({"detail": "bad or reused signature"}, status_code=401)
    m = json.loads(body)
    if m.get("type") == "ping":
        return {"ok": True}
    if state["spent"] >= state["max_usd"]:      # a protocol error, so the gate doesn't cache the call
        return JSONResponse({"detail": f"example agent's cap of ${state['max_usd']:.2f} reached"}, status_code=503)
    if m.get("type") != "start" and m.get("call_id") not in calls:
        return JSONResponse({"detail": "unknown call"}, status_code=404)
    return await asyncio.to_thread(handle, m)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--max-usd", type=float, default=0.30)
    args = ap.parse_args()
    if not SECRET:
        raise SystemExit("set DSG_AGENT_SECRET to the secret the Agents page showed")
    state["max_usd"] = args.max_usd
    print(f"example agent on http://127.0.0.1:{args.port}/agent  (cap ${args.max_usd:.2f})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
