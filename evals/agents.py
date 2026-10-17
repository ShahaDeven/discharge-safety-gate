"""Bring your own agent: register an HTTP endpoint and put it through the same gate as everything else.

    python -m evals.agents list
    python -m evals.agents secret A-001           # print an agent's signing secret again (this machine only)

The console's Agents page calls these functions. An outside agent is untrusted (agentgate/http_agent.py
has the request-side and reply-side limits); on top of that:
- registration checks the URL before saving it (and every call checks it again, then pins the address),
- each agent gets its own signing secret, derived from a master key kept outside the repo
  (DSG_AGENT_MASTER_KEY, or .secrets/agent_master.key, created on first use and gitignored),
- an agent's URL, settings and transcripts are shown only to the session that registered it (its owner);
  everyone else sees its name and verdict,
- the gate is the same: C0 as the baseline, visible + sealed scenarios, same rules. The free prompt check
  can't apply (we never see an outside agent's prompt), so its behaviour on the calls is all that's judged.

What we pay for is the simulated person and the judge (about $0.0015 a call); the agent's own model
costs are its owner's.
"""

import hashlib
import hmac
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from agentgate.audit import AuditLog, content_hash
from agentgate.http_agent import CONTRACT, HttpAgent, check_endpoint
from agentgate.replay import Candidate, load_cache
from discharge import config
from discharge.tools import TOOLS
from evals import proposals
from evals.proposals import SETS, Kind, scenario_sets

DIR = config.RESULTS_DIR / "agents"
KEY_PATH = config.ROOT / ".secrets" / "agent_master.key"
MAX_AGENTS = 20
COST_PER_CALL = 0.0011          # simulated person, measured on C0's calls; the judge is added per call below
JUDGE_PER_CALL = 0.0004


class AgentIn(BaseModel):
    """What the Agents page may send. Anything else is rejected."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=60, pattern=r"^[A-Za-z0-9 .,'()\-:/]+$")
    url: str = Field(min_length=10, max_length=300)
    version: str = Field(default="1", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._\-]+$")
    note: str = Field(default="", max_length=500)
    local: bool = False
    scenario_set: str = Field(default="quick", pattern=r"^(quick|full)$")


# ---------------------------------------------------------------- storage

def _path(aid: str) -> Path:
    if not (aid.startswith("A-") and aid[2:].isdigit() and len(aid) == 5):
        raise ValueError("bad agent id")
    return DIR / f"{aid}.json"


def load(aid: str) -> dict:
    return json.loads(_path(aid).read_text(encoding="utf-8"))


def save(rec: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    _path(rec["id"]).write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")


def all_agents() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(DIR.glob("A-*.json"))] if DIR.exists() else []


def _master_key() -> bytes:
    env = os.getenv("DSG_AGENT_MASTER_KEY", "")
    if env:
        return env.encode()
    if not KEY_PATH.exists():
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEY_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(KEY_PATH, 0o600)
        except OSError:
            pass
    return KEY_PATH.read_text(encoding="utf-8").strip().encode()


def secret(aid: str) -> str:
    """The agent's signing secret. Derived, never stored next to the agent's record."""
    return hmac.new(_master_key(), f"agent:{aid}".encode(), hashlib.sha256).hexdigest()


def endpoint_label(url: str) -> str:
    """Scheme, host and path only, for logs (a query string could hold someone's key)."""
    u = urlsplit(url)
    return f"{u.scheme}://{u.hostname}{f':{u.port}' if u.port else ''}{u.path}"


# ---------------------------------------------------------------- register, check, run

def create(a: AgentIn, owner: str) -> dict:
    """Check the URL (refused before saving if it could reach a private address) and save the agent."""
    check_endpoint(a.url, a.local)                       # raises EndpointRefused
    existing = all_agents()
    if len(existing) >= MAX_AGENTS:
        raise ValueError(f"at most {MAX_AGENTS} agents can be registered")
    aid = f"A-{len(existing) + 1:03d}"
    rec = {"id": aid, "name": a.name, "url": a.url, "version": a.version, "note": a.note, "local": a.local,
           "scenario_set": a.scenario_set, "owner": owner, "status": "saved", "result": None,
           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rec["fingerprint"] = candidate(rec).fingerprint
    save(rec)
    AuditLog(config.AUDIT_PATH).append("agent_registered", {"id": aid, "name": a.name, "endpoint": endpoint_label(a.url),
                                                            "version": a.version, "local": a.local, "owner": owner})
    return rec


def http_agent(rec: dict, **kw) -> HttpAgent:
    return HttpAgent(rec["url"], secret(rec["id"]).encode(), TOOLS, local=rec["local"], **kw)


def candidate(rec: dict) -> Candidate:
    # We can't see inside an outside agent: its owner bumps the version when it changes, and that reruns it.
    fp = content_hash(["http-agent", rec["url"], rec["version"], rec["local"], CONTRACT,
                       [t.anthropic() for t in TOOLS]])
    return Candidate(rec["id"], rec["name"], fp, partial(http_agent, rec), COST_PER_CALL)


def check(rec: dict) -> dict:
    """Signed ping. Free."""
    try:
        return {"ok": True, "seconds": round(http_agent(rec).ping(), 3), "error": None}
    except Exception as exc:                            # EndpointRefused / AgentProtocolError: say why
        return {"ok": False, "seconds": None, "error": str(exc)[:300]}


def estimate(rec: dict) -> dict:
    """What we pay: the simulated person and the judge on every call still to run, plus C0 on the sealed set
    if that baseline is missing. The agent's own costs are not ours to count."""
    visible, holdout = scenario_sets(rec["scenario_set"])
    k = SETS[rec["scenario_set"]]["k"]
    cand = candidate(rec)
    jobs = proposals._replayer(visible + holdout, 99).pending(cand, [s.model_dump() for s in visible + holdout], k,
                                                             load_cache(config.CACHE_DIR / f"{cand.id}.json"))
    base = proposals._baseline_candidate()
    base_jobs = proposals._replayer(holdout, 99).pending(base, [s.model_dump() for s in holdout],
                                                         proposals.BASELINE_HOLDOUT_K, load_cache(config.CACHE_DIR / "C0.json"))
    usd = len(jobs) * (COST_PER_CALL + JUDGE_PER_CALL) + len(base_jobs) * (base.est_cost_per_call + JUDGE_PER_CALL)
    return {"calls": len(jobs), "baseline_calls": len(base_jobs), "visible": len(visible), "holdout": len(holdout),
            "k": k, "usd": round(usd, 3), "blocked": []}


def run(aid: str, max_usd: float, progress=lambda *a: None) -> dict:
    rec = load(aid)
    exposed = bool(rec.get("sealed_seen"))
    result = proposals.gate_candidate(AGENT, rec, candidate(rec), max_usd, progress)
    rec = load(aid)
    rec["sealed_seen"] = True
    if exposed:
        # An outside agent hears the sealed scenarios during its calls, so after its first run the seal is weaker.
        rec["result"]["reasons"].append("This agent has heard the sealed scenarios in an earlier run, so the sealed "
                                        "vote is weaker than on its first run.")
        result = rec["result"]
    save(rec)
    return result


AGENT = Kind("Outside agent", save, "agent_verdict")


# ---------------------------------------------------------------- what each viewer may see

def view(rec: dict, viewer: str | None) -> dict:
    """The owner sees everything but the secret; everyone else sees the name and verdict."""
    public = {"id": rec["id"], "name": rec["name"], "status": rec["status"], "created_at": rec["created_at"],
              "owned": viewer == rec["owner"],
              "result": {k: rec["result"][k] for k in ("verdict", "reasons", "spent_usd", "k", "blocked", "sealed")
                         if k in rec["result"]} if rec.get("result") else None}
    if viewer != rec["owner"]:
        return public
    return {**public, "url": rec["url"], "version": rec["version"], "note": rec["note"], "local": rec["local"],
            "scenario_set": rec["scenario_set"], "endpoint": endpoint_label(rec["url"])}


def main() -> None:
    cmd = sys.argv[1:] or ["list"]
    if cmd[0] == "list":
        for a in all_agents():
            print(a["id"], a["status"], a["name"], endpoint_label(a["url"]), (a.get("result") or {}).get("verdict", ""))
    elif cmd[0] == "secret" and len(cmd) > 1:
        load(cmd[1])
        print(secret(cmd[1]))


if __name__ == "__main__":
    main()
