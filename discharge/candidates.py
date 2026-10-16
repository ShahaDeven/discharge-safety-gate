"""Candidate agents to push through the gate.

    C0  baseline (RED Tool 5 structure)          Sonnet 5.5
    C1  "keep calls under 3 minutes": skips the warning-sign checklist and the line-by-line medicine review
    C2  the same prompt on Sonnet 5.5 (does a bigger model fix what C0 misses? ~10x the cost per call)
    C3  "greet the patient warmly and say why you're calling": names the stay before checking who answered
    C4  the same prompt at effort high (a change that should be promoted, or at least not hurt)
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
    # effort high spends more output tokens; 1.5x is a guess until measured
    est = config.EST_COST_PER_CALL.get(model, 0.025) * (1.5 if effort == "high" else 1.0)
    return Candidate(cid, description, fp, make, est)


def candidates() -> dict[str, Candidate]:
    return {c.id: c for c in [
        reference("C0", "Baseline: RED Tool 5 call structure", "c0_baseline.md"),
        reference("C1", "Short calls: no warning-sign checklist, quick medicine check", "c1_short_calls.md"),
        reference("C2", "Same prompt on Sonnet 5.5", "c0_baseline.md", model="claude-sonnet-5-5"),
        reference("C3", "Warm personal greeting that names the hospital stay", "c3_personal_greeting.md"),
        reference("C4", "Same prompt at effort high", "c0_baseline.md", effort="high"),
    ]}
