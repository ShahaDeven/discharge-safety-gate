"""HTTP API for the Discharge Safety Gate console.

    python -m web                               # http://localhost:8001 (serves web/frontend/dist)
    uvicorn web.api:app --port 8001             # same, for development with `npm run dev` on :5175

Read-only results (no token needed, nothing spent): /api/session, /api/overview, /api/call/..., /api/prompts/C0,
/api/proposals, /api/jobs/{id}, and replaying a cached call over /api/stream?mode=replay.

Anything that spends money or changes data needs the X-DSG-Token header (printed in the server's terminal)
and passes the spending caps first: a live call (/api/stream?mode=live), saving a proposal, running one.
Security details: web/security.py. The sealed holdout never appears in any response.
"""

import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
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
from evals import proposals
from evals.dashboard import build_data, call_summary, transcript
from evals.gate import load_groups  # noqa: F401  (kept for callers that refresh gate data)
from evals.run import SUITES, evaluate

from .security import READ_ONLY, TOKEN, TOKEN_ID, Budget, Guard, redact, require_token

LIVE_DIR = config.RESULTS_DIR / "live"
DIST = Path(__file__).resolve().parent / "frontend" / "dist"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    mode = "READ-ONLY demo mode" if READ_ONLY else f"access token: {TOKEN}"
    print(f"\n  Discharge Safety Gate console  ->  http://localhost:8001\n  {mode}\n", flush=True)
    yield


app = FastAPI(title="Discharge Safety Gate console", docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)
app.add_middleware(Guard)
SCENARIOS = {s.id: s for s in load_scenarios(SUITES["suite"])}          # visible suite only, never the holdout
BUDGET = Budget()
AUDIT = AuditLog(config.AUDIT_PATH)
_cache: dict[str, tuple[float, dict]] = {}
_overview: dict | None = None
_jobs: dict[str, dict] = {}
_job_lock = threading.Lock()
_create_lock = threading.Lock()


def _cached(cand: str) -> dict:
    path = config.CACHE_DIR / f"{cand}.json"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if cand not in _cache or _cache[cand][0] != mtime:
        _cache[cand] = (mtime, load_cache(path))
    return _cache[cand][1]


def _known(cand: str) -> bool:
    return cand in candidates() or any(p["id"] == cand and p["status"] == "done" for p in proposals.all_proposals())


def _scored(sid: str, entry: dict) -> tuple[dict, list]:
    sc = SCENARIOS[sid]
    s = score(sc, entry["record"], judge.cached(entry["record"], judge.load_cache()))
    return call_summary(entry, s), transcript(entry, s)


def _row(e: dict) -> dict:
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


# ------------------------------------------------------------------ session and results (read-only)

@app.get("/api/session")
def session() -> dict:
    return {"read_only": READ_ONLY, "budget": BUDGET.status(), "token_required": not READ_ONLY}


@app.post("/api/session/check")
def check_token(token_id: str = Depends(require_token)) -> dict:
    return {"ok": True, "token_id": token_id}


def _proposal_view(p: dict, short: bool = False) -> dict:
    keep = ("id", "name", "note", "model", "effort", "scenario_set", "created_at", "status", "result")
    out = {k: p.get(k) for k in keep}
    if not short:
        out["prompt"] = p["prompt"]
    return out


@app.get("/api/overview")
def overview(refresh: bool = False) -> dict:
    """Everything the pages show except transcripts. Finished proposals appear as extra candidates
    (visible scenarios only; their sealed-holdout result is a verdict and reasons, never scenario data)."""
    global _overview
    if _overview is None or refresh:
        _overview = build_data(with_transcripts=False)
    data = {k: v for k, v in _overview.items()}
    data["candidates"] = list(_overview["candidates"])
    data["calls"] = dict(_overview["calls"])
    data["gate"] = dict(_overview["gate"])
    for p in proposals.all_proposals():
        if p["status"] != "done" or not p["result"]["visible"]:          # blocked by the prompt check: no calls
            continue
        k = p["result"]["k"]
        rows = evaluate(p["id"], [s for s in SCENARIOS.values()], k)
        data["calls"][p["id"]] = {}
        for sc, e, s in rows:
            data["calls"][p["id"]].setdefault(sc.id, []).append(call_summary(e, s))
        data["candidates"].append({"id": p["id"], "description": p["name"], "k": k, "scenarios": len(data["calls"][p["id"]]),
                                   "calls": len(rows), "why": p["note"], "proposal": True})
        data["gate"][p["id"]] = {**p["result"]["visible"], "verdict": p["result"]["verdict"], "reasons": p["result"]["reasons"],
                                 "description": p["name"], "sealed": p["result"]["sealed"]}
    data["session"] = session()
    rt = config.RESULTS_DIR / "redteam.json"
    data["redteam"] = json.loads(rt.read_text(encoding="utf-8")) if rt.exists() else None
    return data


@app.get("/api/call/{cand}/{sid}/{r}")
def call(cand: str, sid: str, r: int) -> dict:
    if sid not in SCENARIOS or not _known(cand):                        # holdout ids are never in SCENARIOS
        raise HTTPException(404, "no such call")
    entry = _cached(cand).get(f"{sid}#{r}")
    if not entry:
        raise HTTPException(404, "no such call")
    summary, rows = _scored(sid, entry)
    return {"summary": summary, "transcript": rows}


@app.get("/api/prompts/C0")
def baseline_prompt() -> dict:
    return {"prompt": proposals.baseline_prompt()}


# ------------------------------------------------------------------ proposals

@app.get("/api/proposals")
def list_proposals() -> list[dict]:
    return [_proposal_view(p, short=True) for p in proposals.all_proposals()][::-1]


@app.get("/api/proposals/{pid}")
def get_proposal(pid: str) -> dict:
    try:
        return {**_proposal_view(proposals.load(pid)), "estimate": proposals.estimate(proposals.load(pid))}
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "no such proposal")


@app.post("/api/proposals")
def create_proposal(body: proposals.ProposalIn, token_id: str = Depends(require_token)) -> dict:
    with _create_lock:
        rec = proposals.create(body, token_id)
    return {**_proposal_view(rec), "estimate": proposals.estimate(rec)}


@app.post("/api/proposals/{pid}/run")
def run_proposal(pid: str, token_id: str = Depends(require_token)) -> dict:
    try:
        rec = proposals.load(pid)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "no such proposal")
    est = proposals.estimate(rec)
    BUDGET.check(est["usd"])                                           # refuses (402) past any cap
    with _job_lock:
        if any(j["state"] == "running" for j in _jobs.values()):
            raise HTTPException(409, "another gate run is in progress; one at a time")
        job = {"id": f"J-{int(time.time())}", "pid": pid, "state": "running", "phase": "starting", "done": 0, "total": 0,
               "spent": 0.0, "result": None, "error": None, "estimate": est}
        _jobs[job["id"]] = job
    AUDIT.append("proposal_run", {"id": pid, "token_id": token_id, "estimate_usd": est["usd"]})
    cap = min(BUDGET.run_max, BUDGET.session_max - BUDGET.session, BUDGET.day_max - BUDGET.today())

    def work():
        global _overview
        def progress(phase, done, total, spent):
            job.update(phase=phase, done=done, total=total, spent=round(spent, 4))
        try:
            job["result"] = proposals.run(pid, max_usd=cap, progress=progress)
            job["state"] = "done"
            _overview = None                                           # the new candidate shows up everywhere
        except Exception as exc:
            job.update(state="error", error=redact(f"{type(exc).__name__}: {exc}"))
            rec2 = proposals.load(pid)
            rec2["status"] = "error"
            proposals.save(rec2)
        finally:
            BUDGET.add(job["spent"])
    threading.Thread(target=work, daemon=True).start()
    return job


@app.get("/api/jobs/{jid}")
def get_job(jid: str) -> dict:
    if jid not in _jobs:
        raise HTTPException(404, "no such job")
    return _jobs[jid]


# ------------------------------------------------------------------ streaming a call

def _pace(row: dict) -> float:
    if row["k"] == "s":
        return min(2.4, 0.35 + len(row["t"]) * 0.018)
    return 0.45


@app.get("/api/stream")
async def stream(cand: str, sid: str, mode: str = "replay", r: int = 0, speed: float = 1.0,
                 x_dsg_token: str | None = Header(default=None)):
    if sid not in SCENARIOS or not _known(cand):
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
            raise HTTPException(404, "no such call")

        async def replay():
            yield {"event": "meta", "data": json.dumps({"mode": "replay", "cand": cand, "sid": sid, "r": r, "cost": 0})}
            for e in entry["record"]["events"]:
                row = _row(e)
                await asyncio.sleep(_pace(row) / max(0.25, min(speed, 8.0)))
                yield {"event": "row", "data": json.dumps(row)}
            summary, rows = _scored(sid, entry)
            yield {"event": "scored", "data": json.dumps({"summary": summary, "transcript": rows})}
            yield {"event": "done", "data": json.dumps({"cost": 0, "budget": BUDGET.status()})}
        return EventSourceResponse(replay())

    token_id = require_token(x_dsg_token)
    if cand in candidates():
        cand_obj = candidates()[cand]
    else:
        cand_obj = proposals.candidate(proposals.load(cand))
    BUDGET.check(cand_obj.est_cost_per_call)

    def work() -> None:
        cost = 0.0
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
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            LIVE_DIR.mkdir(parents=True, exist_ok=True)
            (LIVE_DIR / f"{at.replace(':', '')}_{cand}_{sid}.json").write_text(json.dumps(
                {"cand": cand, "sid": sid, "at": at, "cost_usd": round(cost, 6), "summary": summary, "record": record},
                ensure_ascii=False), encoding="utf-8")
            AUDIT.append("live_call", {"candidate": cand, "fingerprint": cand_obj.fingerprint, "scenario": sid,
                                       "cost_usd": round(cost, 6), "record_sha256": content_hash(record), "token_id": token_id})
            send("scored", {"summary": summary, "transcript": rows})
        except Exception as exc:                  # surface the error in the UI, without secrets
            send("error", {"message": redact(f"{type(exc).__name__}: {exc}")})
        finally:
            BUDGET.add(cost)
            send("done", {"cost": round(cost, 4), "budget": BUDGET.status()})
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
        target = (DIST / path).resolve()
        inside = DIST.resolve() in target.parents
        return FileResponse(target if path and inside and target.is_file() else DIST / "index.html")
