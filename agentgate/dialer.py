"""When to call again: a deterministic retry policy for outbound follow-up calls.

    plan = plan_outreach(first_call, ["no_answer", "voicemail", "busy", ...], RetryPolicy())

An attempt ends in one of: "completed", "opted_out", "no_answer", "busy", "voicemail", or "callback:<ISO time>"
(the person asked to be called at that time). The policy:
- retry RETRY_AFTER after no answer, busy or voicemail;
- at most MAX_PER_DAY attempts a day, for MAX_DAYS days from the first attempt;
- only within calling hours (local time; 08:00-21:00 by default, the TCPA convention for solicitations);
- never again after "completed" or "opted_out";
- a requested callback is honored (moved into calling hours if needed) and doesn't count against the daily limit;
- when attempts run out without reaching anyone: a nurse task, "patient unreachable", because a high-risk patient
  nobody can reach needs a person, not silence.

All times are the patient's local time. No telephony here: this decides WHEN; a dialer places the call.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

DONE = {"completed", "opted_out"}
RETRY = {"no_answer", "busy", "voicemail"}


@dataclass(frozen=True)
class RetryPolicy:
    retry_after: timedelta = timedelta(minutes=90)
    max_per_day: int = 3
    max_days: int = 2
    start: time = time(8, 0)
    end: time = time(21, 0)


@dataclass
class Plan:
    attempts: list[tuple[datetime, str]] = field(default_factory=list)   # (when, outcome)
    next_at: datetime | None = None
    final: str = ""                                                        # completed | opted_out | nurse_task | pending


def into_hours(t: datetime, p: RetryPolicy) -> datetime:
    """The earliest moment at or after t inside calling hours."""
    if t.time() < p.start:
        return datetime.combine(t.date(), p.start)
    if t.time() > p.end:
        return datetime.combine(t.date() + timedelta(days=1), p.start)
    return t


def next_attempt(attempts: list[tuple[datetime, str]], p: RetryPolicy = RetryPolicy()) -> tuple[datetime | None, str]:
    """(when to call next, why). None when done: completed, opted out, or out of attempts (nurse task)."""
    if not attempts:
        raise ValueError("no attempt yet")
    when, outcome = attempts[-1]
    if outcome in DONE:
        return None, outcome
    first_day = attempts[0][0].date()
    last_day = first_day + timedelta(days=p.max_days - 1)
    if outcome.startswith("callback:"):
        # Never before the call that asked for it; moved into calling hours if it falls outside them.
        asked = datetime.fromisoformat(outcome.split(":", 1)[1])
        return into_hours(max(asked, when + timedelta(minutes=1)), p), "callback requested"
    if outcome not in RETRY:
        raise ValueError(f"unknown outcome {outcome!r}")
    t = into_hours(when + p.retry_after, p)
    # Requested callbacks don't use up the day's tries.
    tries = lambda d: sum(1 for w, o in attempts if w.date() == d and not o.startswith("callback:"))
    while tries(t.date()) >= p.max_per_day:
        t = datetime.combine(t.date() + timedelta(days=1), p.start)
    if t.date() > last_day:
        return None, "nurse_task"
    return t, f"retry after {outcome.replace('_', ' ')}"


def plan_outreach(first: datetime, outcomes: list[str], p: RetryPolicy = RetryPolicy()) -> Plan:
    """Play a sequence of outcomes through the policy: when each attempt happens, and how it ends."""
    plan = Plan()
    t: datetime | None = into_hours(first, p)
    for outcome in outcomes:
        if t is None:
            break
        plan.attempts.append((t, outcome))
        t, why = next_attempt(plan.attempts, p)
        if t is None:
            plan.final = why
    plan.next_at = t
    if t is not None:
        plan.final = "pending"
    return plan
