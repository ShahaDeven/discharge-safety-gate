"""HTTP API for the Discharge Safety Gate console.

    uvicorn web.api:app --port 8001            # then open http://localhost:8001 (after `npm run build`)
                                                # or run `npm run dev` in web/frontend for hot reload on :5175

Read-only results come from the replay cache, gate.json, curve.json and the labels (no API calls).
/api/stream plays one call turn by turn over server-sent events:

    mode=replay   a cached call, paced like a conversation. Free.
    mode=live     a brand-new call through the real agent and simulated patient (~$0.003 on Haiku,
                  ~$0.03 on Sonnet), then scored and judged. Saved under evals/results/live/, never
                  into the replay cache, so playing with the console never changes the results.
                  Capped per server run by DSG_LIVE_MAX_USD (default $0.50).
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from agentgate import llm
from agentgate.audit import AuditLog, content_hash
from agentgate.conversation import run_call
from agentgate.replay import load_cache
from discharge import config, judge
from discharge.candidates import candidates
from discharge.persona import DischargeSuite
from discharge.scenario import load_scenarios
from discharge.scoring import score
from evals.dashboard import build_data, call_summary, transcript
from evals.run import SUITES

LIVE_DIR = config.RESULTS_DIR / "live"
LIVE_MAX_USD = float(os.getenv("DSG_LIVE_MAX_USD", "0.50"))
DIST = Path(__file__).resolve().parent / "frontend" / "dist"

app = FastAPI(title="Discharge Safety Gate console")
SCENARIOS = {s.id: s for s in load_scenarios(SUITES["suite"])}
_cache: dict[str, tuple[float, dict]] = {}
_overview: dict | None = None
_live_spent = 0.0
_live_lock = threading.Lock()


def _cached(cand: str) -> dict:
    """Replay cache for one candidate, reloaded only when the file changes."""
    path = config.CACHE_DIR / f"{cand}.json"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if cand not in _cache or _cache[cand][0] != mtime:
        _cache[cand] = (mtime, load_cache(path))
    return _cache[cand][1]


def _scored(cand: str, sid: str, entry: dict) -> tuple[dict, list]:
    sc = SCENARIOS[sid]
    s = score(sc, entry["record"], judge.cached(entry["record"], judge.load_cache()))
    return call_summary(entry, s), transcript(entry, s)


def _row(e: dict) -> dict:
    """One event as the frontend draws it (same shape as evals.dashboard.transcript rows)."""
    if e["kind"] == "speech":
        row = {"k": "s", "w": "a" if e["who"] == "agent" else "p", "t": e["text"]}
        if e.get("disclosed"):
            row["rev"] = [d.strip("[] ") for d in e["disclosed"]]
        return row
    if e["kind"] == "tool_call":
        return {"k": "c", "n": e["name"], "i": e["input"]}
    if e["kind"] == "tool_result":
        return {"k": "r", "n": e["name"], "t": e["text"]}
    return {"k": "e", "t": e.get("text", "")}


# ------------------------------------------------------------------ read-only results

@app.get("/api/overview")
def overview(refresh: bool = False) -> dict:
    """Verdicts, findings, per-call summaries, scenario cards, judge checks, cost. No transcripts."""
    global _overview
    if _overview is None or refresh:
        _overview = build_data(with_transcripts=False)
    return {**_overview, "live": {"spent": round(_live_spent, 4), "cap": LIVE_MAX_USD}}


@app.get("/api/call/{cand}/{sid}/{r}")
def call(cand: str, sid: str, r: int) -> dict:
    entry = _cached(cand).get(f"{sid}#{r}")
    if not entry or sid not in SCENARIOS:
        raise HTTPException(404, f"no cached call {cand} {sid} replay {r}")
    summary, rows = _scored(cand, sid, entry)
    return {"summary": summary, "transcript": rows}


@app.get("/api/live/history")
def live_history() -> list[dict]:
    """Live calls placed from the console, newest first."""
    if not LIVE_DIR.exists():
        return []
    out = []
    for p in sorted(LIVE_DIR.glob("*.json"), reverse=True)[:50]:
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"file": p.name, "cand": d["cand"], "sid": d["sid"], "at": d["at"], "cost": d["cost_usd"],
                    "summary": d["summary"]})
    return out


# ------------------------------------------------------------------ streaming a call

def _pace(row: dict) -> float:
    """Seconds to wait before showing a replayed row: roughly speaking speed, capped."""
    if row["k"] == "s":
        return min(2.4, 0.35 + len(row["t"]) * 0.018)
    return 0.45


@app.get("/api/stream")
async def stream(cand: str, sid: str, mode: str = "replay", r: int = 0, speed: float = 1.0):
    if cand not in candidates() or sid not in SCENARIOS:
        raise HTTPException(404, "unknown agent version or scenario")
    if mode not in ("replay", "live"):
        raise HTTPException(400, "mode must be replay or live")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def send(event: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"event": event, "data": json.dumps(data)})

    if mode == "replay":
        entry = _cached(cand).get(f"{sid}#{r}")
        if not entry:
            raise HTTPException(404, f"no cached call {cand} {sid} replay {r}")

        async def replay():
            yield {"event": "meta", "data": json.dumps({"mode": "replay", "cand": cand, "sid": sid, "r": r, "cost": 0})}
            for e in entry["record"]["events"]:
                row = _row(e)
                await asyncio.sleep(_pace(row) / max(0.25, speed))
                yield {"event": "row", "data": json.dumps(row)}
            summary, rows = _scored(cand, sid, entry)
            yield {"event": "scored", "data": json.dumps({"summary": summary, "transcript": rows})}
            yield {"event": "done", "data": json.dumps({"cost": 0, "spent": round(_live_spent, 4)})}
        return EventSourceResponse(replay())

    cand_obj = candidates()[cand]
    with _live_lock:
        if _live_spent + cand_obj.est_cost_per_call > LIVE_MAX_USD:
            raise HTTPException(402, f"live spending cap reached (${_live_spent:.2f} of ${LIVE_MAX_USD:.2f}); "
                                     "restart the server or raise DSG_LIVE_MAX_USD")

    def work() -> None:
        global _live_spent
        try:
            sc = SCENARIOS[sid]
            call_id = f"live:{cand}:{sid}:{int(time.time())}"
            ctx, sim, harness = DischargeSuite([sc]).build(sc.model_dump(), call_id)
            record = run_call(cand_obj.make_agent(), sim, harness, ctx, max_agent_turns=config.MAX_AGENT_TURNS,
                              on_event=lambda e: send("row", _row(e))).to_dict()
            log: list = []
            verdict = judge.judge(sc, record, judge.load_cache(), log=log)
            cost = llm.cost_usd(record["usage"]) + llm.cost_usd(log)
            entry = {"r": "live", "record": record, "cost_usd": round(cost, 6)}
            s = score(sc, record, verdict)
            summary, rows = call_summary(entry, s), transcript(entry, s)
            with _live_lock:
                _live_spent += cost
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            (LIVE_DIR / f"{at.replace(':', '')}_{cand}_{sid}.json").write_text(json.dumps(
                {"cand": cand, "sid": sid, "at": at, "cost_usd": round(cost, 6), "summary": summary,
                 "record": record}, ensure_ascii=False), encoding="utf-8")
            AuditLog(config.AUDIT_PATH).append("live_call", {"candidate": cand, "fingerprint": cand_obj.fingerprint,
                                                             "scenario": sid, "cost_usd": round(cost, 6),
                                                             "record_sha256": content_hash(record)})
            send("scored", {"summary": summary, "transcript": rows})
            send("done", {"cost": round(cost, 4), "spent": round(_live_spent, 4)})
        except Exception as exc:                  # surface API errors in the UI instead of a silent hang
            send("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            send("end", {})

    async def live():
        yield {"event": "meta", "data": json.dumps({"mode": "live", "cand": cand, "sid": sid,
                                                    "cost": cand_obj.est_cost_per_call})}
        threading.Thread(target=work, daemon=True).start()
        while True:
            msg = await queue.get()
            if msg["event"] == "end":
                break
            yield msg
    return EventSourceResponse(live())


# ------------------------------------------------------------------ the built frontend

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        target = DIST / path
        return FileResponse(target if path and target.is_file() else DIST / "index.html")
