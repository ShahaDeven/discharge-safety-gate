"""Message Batches transport for multi-turn calls (50% off, results in minutes to hours).

A call is a conversation, so its requests can't all be sent at once: each turn needs
the previous answer. The replayer runs every call in its own thread; each thread's
LLM request (agent or simulator) is parked here instead of being sent. When every
live call is waiting, all parked requests go out as ONE batch (a "round"); when the
batch ends, each thread gets its answer and moves on to its next request. A run is
therefore ~40 rounds, one per conversation step, each a few minutes.

Caching matters more than the discount: in the pilot 86% of input tokens were cache
reads. A round can outlast the default 5-minute cache, which would make batch mode
cost MORE than live, so every cache_control is sent with the 1-hour TTL here (writes
cost 2x instead of 1.25x). Check the cache-read share a batch run prints.
"""

import threading
import time
from collections import Counter

from pydantic import BaseModel

from . import llm


def _jsonable(obj):
    """Request params may hold SDK response blocks (the agent appends response.content); make them plain JSON."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def _ttl_1h(obj):
    if isinstance(obj, dict):
        return {k: ({"type": "ephemeral", "ttl": "1h"} if k == "cache_control" else _ttl_1h(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_ttl_1h(v) for v in obj]
    return obj


class _Slot:
    __slots__ = ("params", "event", "result")

    def __init__(self, params: dict):
        self.params = params
        self.event = threading.Event()
        self.result = None


class BatchTransport:
    def __init__(self, poll_seconds: int = 15, log=print):
        self.poll_seconds = poll_seconds
        self.log = log
        self._cv = threading.Condition()
        self._live = 0
        self._pending: dict[str, _Slot] = {}
        self._seq = 0
        self._thread: threading.Thread | None = None
        self.rounds = 0

    # -- called by the replayer
    def expect(self, n: int) -> None:
        """Register all calls before any starts, so a partial round is never sent early."""
        with self._cv:
            self._live += n

    def leave(self) -> None:
        with self._cv:
            self._live -= 1
            self._cv.notify_all()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._coordinate, name="batch-coordinator", daemon=True)
        self._thread.start()

    def join(self) -> None:
        if self._thread:
            self._thread.join()

    # -- called by llm.create / llm.parse inside a call thread
    def send(self, params: dict):
        slot = _Slot(_ttl_1h(_jsonable(params)))
        with self._cv:
            self._seq += 1
            self._pending[f"q{self._seq}"] = slot
            self._cv.notify_all()
        slot.event.wait()
        if isinstance(slot.result, Exception):
            raise slot.result
        return slot.result

    # -- coordinator
    def _coordinate(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._live <= 0 or (self._pending and len(self._pending) >= self._live))
                if not self._pending:
                    return                       # every call finished
                todo, self._pending = self._pending, {}
            self._round(todo)

    def _round(self, todo: dict[str, _Slot]) -> None:
        self.rounds += 1
        models = Counter(s.params["model"].replace("claude-", "") for s in todo.values())
        started = time.perf_counter()
        client = llm.client()
        try:
            batch_id = client.messages.batches.create(
                requests=[{"custom_id": cid, "params": s.params} for cid, s in todo.items()]).id
            while client.messages.batches.retrieve(batch_id).processing_status != "ended":
                time.sleep(self.poll_seconds)
            seen = set()
            for res in client.messages.batches.results(batch_id):
                slot = todo.get(res.custom_id)
                if slot is None:
                    continue
                seen.add(res.custom_id)
                if res.result.type == "succeeded":
                    slot.result = res.result.message
                else:
                    slot.result = RuntimeError(f"batch request {res.result.type}: {getattr(res.result, 'error', '')}")
                slot.event.set()
            for cid, slot in todo.items():
                if cid not in seen:
                    slot.result = RuntimeError("batch returned no result for this request")
                    slot.event.set()
        except Exception as exc:                 # the whole round failed: fail each call, they rerun next time
            for slot in todo.values():
                if not slot.event.is_set():
                    slot.result = exc
                    slot.event.set()
        self.log(f"  round {self.rounds}: {len(todo)} requests ({', '.join(f'{n} {m}' for m, n in models.items())})"
                 f" in {time.perf_counter() - started:.0f}s", flush=True)
