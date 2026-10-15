"""Replay a candidate agent over a suite of scenarios, K times each, with a cache.

Results are cached per candidate in <cache_dir>/<candidate>.json, keyed by
"<scenario>#<replay>" with a fingerprint of the agent (prompt, model, effort, tools),
the simulator and the scenario. Rerunning is free, raising K runs only the new
replays, and editing a prompt or a scenario reruns only what it touches. Candidates
never share samples, so baseline and candidate replays are independent draws.

Only the raw call record is cached; scoring is recomputed from it, so a scoring fix
never needs a rerun. Every completed call is also appended to the hash-chained audit log.
"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import llm
from .adapter import CallAgent, CallContext, ToolHarness
from .audit import AuditLog, content_hash
from .conversation import Simulator, run_call


@dataclass(frozen=True)
class Candidate:
    id: str
    description: str
    fingerprint: str                       # changes whenever the agent's behavior could change
    make_agent: Callable[[], CallAgent]    # a fresh agent per call (agents hold call state)
    est_cost_per_call: float = 0.0         # for the cost estimate printed before a run


class Suite(Protocol):
    """What a company layer provides for each scenario."""

    sim_fingerprint: str

    def build(self, scenario: dict, call_id: str) -> tuple[CallContext, Simulator, ToolHarness]: ...


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def fingerprint(cand: Candidate, suite: Suite, scenario: dict) -> str:
    return _hash(cand.fingerprint, suite.sim_fingerprint, json.dumps(scenario, sort_keys=True))


def load_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class Replayer:
    def __init__(self, suite: Suite, cache_dir: Path, audit: AuditLog, max_agent_turns: int = 12,
                 max_usd: float | None = None):
        self.suite = suite
        self.max_usd = max_usd       # spending cap for this replayer; calls not yet started are skipped
        self.spent = 0.0
        self.cache_dir = Path(cache_dir)
        self.audit = audit
        self.max_agent_turns = max_agent_turns
        self._lock = threading.Lock()

    def cache_path(self, cand_id: str) -> Path:
        return self.cache_dir / f"{cand_id}.json"

    def pending(self, cand: Candidate, scenarios: list[dict], k: int, cache: dict) -> list[tuple[dict, int, str]]:
        jobs = []
        for sc in scenarios:
            fp = fingerprint(cand, self.suite, sc)
            for r in range(k):
                hit = cache.get(f"{sc['id']}#{r}")
                if not hit or hit.get("fingerprint") != fp:
                    jobs.append((sc, r, fp))
        return jobs

    def replay_once(self, cand: Candidate, scenario: dict, r: int, fp: str) -> dict:
        call_id = f"{cand.id}:{scenario['id']}#{r}"
        ctx, sim, harness = self.suite.build(scenario, call_id)
        agent = cand.make_agent()
        try:
            rec = run_call(agent, sim, harness, ctx, max_agent_turns=self.max_agent_turns).to_dict()
        except Exception as exc:                     # API failure mid-call: not cached, retried next run
            return {"scenario_id": scenario["id"], "r": r, "fingerprint": fp,
                    "error": f"{type(exc).__name__}: {exc}"}
        return {"scenario_id": scenario["id"], "r": r, "fingerprint": fp, "record": rec, "error": None,
                "cost_usd": round(llm.cost_usd(rec["usage"]), 6)}

    def run(self, cand: Candidate, scenarios: list[dict], k: int, workers: int = 4,
            on_done: Callable[[dict], None] | None = None, transport=None) -> dict:
        """Live by default. With a BatchTransport every call runs at once in its own thread and
        their requests go out together as Message Batches rounds (see agentgate/batch.py)."""
        path = self.cache_path(cand.id)
        cache = load_cache(path)
        jobs = self.pending(cand, scenarios, k, cache)
        if not jobs:
            return cache

        def job(sc: dict, r: int, fp: str) -> dict:
            try:
                if self.max_usd is not None and self.spent >= self.max_usd:
                    return {"scenario_id": sc["id"], "r": r, "fingerprint": fp,
                            "error": f"skipped: spending cap ${self.max_usd:.2f} reached"}
                return self.replay_once(cand, sc, r, fp)
            finally:
                if transport is not None:
                    transport.leave()

        if transport is not None:
            workers = len(jobs)
            transport.expect(len(jobs))
            llm.use_transport(transport)
            transport.start()
        try:
            self._collect(cand, path, cache, jobs, job, workers, on_done, transport)
        finally:
            if transport is not None:
                transport.join()
                llm.use_transport(None)
        return cache

    def _collect(self, cand, path, cache, jobs, job, workers, on_done, transport) -> None:
        mode = "batch" if transport is not None else "live"
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(job, sc, r, fp) for sc, r, fp in jobs]
            for fut in as_completed(futures):
                entry = fut.result()
                if on_done:
                    on_done(entry)
                if entry.get("error"):
                    continue
                self.spent += entry["cost_usd"]
                entry["mode"] = mode
                with self._lock:
                    cache[f"{entry['scenario_id']}#{entry['r']}"] = entry
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                self.audit.append("call", {
                    "candidate": cand.id, "candidate_fingerprint": cand.fingerprint,
                    "scenario": entry["scenario_id"], "replay": entry["r"], "fingerprint": entry["fingerprint"],
                    "ended_by": entry["record"]["ended_by"], "cost_usd": entry["cost_usd"],
                    "record_sha256": content_hash(entry["record"]), "mode": mode,
                })
