"""Batch transport: lockstep rounds, error propagation, 1-hour cache TTL. Fake Batches API, offline."""

import threading
from types import SimpleNamespace

import pytest

from agentgate import llm
from agentgate.batch import BatchTransport, _ttl_1h


class FakeBatches:
    def __init__(self, fail_ids=()):
        self.sizes: list[int] = []
        self.fail_ids = set(fail_ids)
        self._store: dict[str, list] = {}

    def create(self, requests):
        bid = f"b{len(self.sizes)}"
        self.sizes.append(len(requests))
        self._store[bid] = requests
        return SimpleNamespace(id=bid)

    def retrieve(self, bid):
        return SimpleNamespace(id=bid, processing_status="ended")

    def results(self, bid):
        for req in self._store[bid]:
            tag = req["params"]["tag"]
            if tag in self.fail_ids:
                result = SimpleNamespace(type="errored", error="boom")
            else:
                result = SimpleNamespace(type="succeeded", message=f"answer to {tag}")
            yield SimpleNamespace(custom_id=req["custom_id"], result=result)


@pytest.fixture
def fake(monkeypatch):
    batches = FakeBatches(fail_ids={"t2-1"})
    monkeypatch.setattr(llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    return batches


def test_rounds_wait_for_every_live_call(fake):
    """Three calls needing 1, 2 and 3 steps: rounds of 3, 2 and 1 requests, never a partial round."""
    transport = BatchTransport(poll_seconds=0, log=lambda *a, **k: None)
    transport.expect(3)
    transport.start()
    answers: dict[int, list] = {}

    def call(i):
        try:
            got = []
            for step in range(i + 1):
                try:
                    got.append(transport.send({"model": "claude-x", "tag": f"t{i}-{step}"}))
                except RuntimeError as exc:
                    got.append(f"error: {exc}")
            answers[i] = got
        finally:
            transport.leave()

    threads = [threading.Thread(target=call, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    transport.join()

    assert fake.sizes == [3, 2, 1]
    assert transport.rounds == 3
    assert answers[0] == ["answer to t0-0"]
    assert answers[2][0] == "answer to t2-0" and answers[2][2] == "answer to t2-2"
    assert answers[2][1].startswith("error: batch request errored")     # one failed request fails only its call


def test_every_cache_breakpoint_gets_the_one_hour_ttl():
    params = {"cache_control": {"type": "ephemeral"},
              "system": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": "hi"}]}
    out = _ttl_1h(params)
    assert out["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert out["system"][0]["cache_control"]["ttl"] == "1h"
    assert out["messages"] == params["messages"]


def test_batch_cost_is_half_and_prices_one_hour_writes_at_2x():
    live = {"model": "claude-sonnet-5-5", "mode": "live", "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_write": 1_000_000, "cache_write_1h": 0}
    batch = {**live, "mode": "batch", "cache_write_1h": 1_000_000}
    assert llm.cost_usd([live]) == pytest.approx(2.50)        # 1.25x of $2
    assert llm.cost_usd([batch]) == pytest.approx(2.00)       # 2x of $2, then 50% off
