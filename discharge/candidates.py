"""Candidate agents to push through the gate. Day 1: the baseline only.

    C0  baseline (RED Tool 5 structure)          Sonnet 5.5
    C2  the same prompt on Sonnet 5.5 (does a bigger model fix what C0 misses? ~10x the cost per call)
    Day 2 adds C1 (short calls) and C3 (personal greeting).
"""

from functools import partial

from agentgate.audit import content_hash
from agentgate.replay import Candidate

from . import config
from .reference_agent.agent import CONTEXT_VERSION, ReferenceAgent
from .tools import TOOLS


def reference(cid: str, description: str, prompt_file: str, model: str = config.AGENT_MODEL,
              effort: str = config.AGENT_EFFORT) -> Candidate:
    prompt = (config.PROMPT_DIR / prompt_file).read_text(encoding="utf-8")
    fp = content_hash([prompt, model, effort, config.FALLBACKS, [t.anthropic() for t in TOOLS], CONTEXT_VERSION])
    make = partial(ReferenceAgent, prompt, TOOLS, model=model, effort=effort, fallbacks=config.FALLBACKS)
    return Candidate(cid, description, fp, make)


def candidates() -> dict[str, Candidate]:
    return {c.id: c for c in [
        reference("C0", "Baseline: RED Tool 5 call structure", "c0_baseline.md"),
        reference("C2", "Same prompt on Sonnet 5.5", "c0_baseline.md", model="claude-sonnet-5-5"),
    ]}
