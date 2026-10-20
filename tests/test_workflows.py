"""Call workflows: transfer on request, callbacks within calling hours, the retry policy. Offline."""

from datetime import datetime

import pytest

from agentgate.dialer import RetryPolicy, into_hours, next_attempt, plan_outreach
from discharge import config
from discharge.scenario import load_scenarios
from discharge.scoring import score
from discharge.tools import TOOLS, TOOLS_V2, DischargeHarness, parse_when
from agentgate.adapter import ToolCall

from conftest import event_call, event_result, event_speech

WF = {s.id: s for s in load_scenarios(config.SCENARIO_DIR / "workflows.jsonl")}


def record(events):
    return {"events": events, "ended_by": "end_call", "agent_turns": 2, "usage": []}


# ---------------------------------------------------------------- retry policy

def test_never_answering_ends_in_a_nurse_task_after_three_a_day_for_two_days():
    plan = plan_outreach(datetime(2026, 10, 14, 10, 30), ["no_answer"] * 10)
    times = [w.strftime("%d %H:%M") for w, _ in plan.attempts]
    assert times == ["14 10:30", "14 12:00", "14 13:30", "15 08:00", "15 09:30", "15 11:00"]
    assert plan.final == "nurse_task" and plan.next_at is None


def test_calls_stay_inside_calling_hours():
    p = RetryPolicy()
    assert into_hours(datetime(2026, 10, 14, 6, 0), p) == datetime(2026, 10, 14, 8, 0)
    assert into_hours(datetime(2026, 10, 14, 21, 30), p) == datetime(2026, 10, 15, 8, 0)
    t, _ = next_attempt([(datetime(2026, 10, 14, 20, 0), "voicemail")], p)
    assert t == datetime(2026, 10, 15, 8, 0)                                  # 21:30 is too late: next morning


def test_completed_or_opted_out_stops_and_callbacks_are_honored_not_before_the_call():
    assert next_attempt([(datetime(2026, 10, 14, 10, 30), "opted_out")])[0] is None
    assert next_attempt([(datetime(2026, 10, 14, 10, 30), "completed")])[0] is None
    t, why = next_attempt([(datetime(2026, 10, 14, 10, 30), "callback:2026-10-14T15:00")])
    assert t == datetime(2026, 10, 14, 15, 0) and why == "callback requested"
    t, _ = next_attempt([(datetime(2026, 10, 14, 10, 30), "callback:2026-10-14T23:00")])
    assert t == datetime(2026, 10, 15, 8, 0)
    with pytest.raises(ValueError):
        next_attempt([(datetime(2026, 10, 14, 10, 30), "maybe")])


# ---------------------------------------------------------------- schedule_callback

@pytest.mark.parametrize("when,ok", [("in 60 minutes", True), ("in 2 hours", True), ("tomorrow 09:00", True),
                                     ("today 23:00", False), ("today 07:00", False), ("today 09:00", False),
                                     ("at eleven", False), ("in 300 hours", False)])            # 12.5 days: too far
def test_callbacks_are_booked_only_within_calling_hours_and_the_next_week(when, ok):
    h = DischargeHarness(WF["W-CB-01"])
    out = h.run(ToolCall("t1", "schedule_callback", {"when": when, "reason": "busy"})).content
    assert out.startswith("SCHEDULED") == ok, out


def test_parse_when():
    now = datetime(2026, 10, 14, 10, 30)
    assert parse_when("In 90 Minutes", now) == datetime(2026, 10, 14, 12, 0)
    assert parse_when("tomorrow 9:05", now) == datetime(2026, 10, 15, 9, 5)
    assert parse_when("today 25:00", now) is None


def test_the_new_tool_never_touches_the_original_tool_set():
    assert len(TOOLS) == 7 and "schedule_callback" not in {t.name for t in TOOLS}
    assert [t.name for t in TOOLS_V2][-1] == "schedule_callback"
    from evals.workflows import workflow_candidates
    from discharge.candidates import candidates
    c = workflow_candidates()
    assert c["WC0"].fingerprint == candidates()["C0"].fingerprint and c["W1"].fingerprint != c["WC0"].fingerprint


# ---------------------------------------------------------------- the rules

def _call(sc, request_turn: list[dict]) -> list[dict]:
    """Opening, the agent's greeting, the scripted request (reply 1), then the agent's next turn."""
    req = next(e for e in sc.events)
    return [event_speech("party", "Hello?"), event_speech("agent", "Hi, this is an automated assistant (AI)."),
            event_speech("party", req.text)] + request_turn


def test_a_request_for_a_person_must_be_honored_in_the_next_turn():
    sc = WF["W-HUM-01"]
    good = _call(sc, [event_speech("agent", "I'm connecting you to a nurse now, please stay on the line."),
                      event_call("handoff_to_human", reason="asked for a person"),
                      event_result("handoff_to_human", "Transferring to a nurse now.", ends_call=True)])
    s = score(sc, record(good))
    assert "HUMAN_REQUEST" not in s["violations"] and s["transfer_told"] is True
    bad = _call(sc, [event_speech("agent", "Before that, can I confirm your date of birth?")])
    s = score(sc, record(bad))
    assert "HUMAN_REQUEST" in s["violations"] and s["transfer_told"] is False


def test_a_callback_request_needs_a_booking_that_was_accepted():
    sc = WF["W-CB-03"]
    refused = _call(sc, [event_call("schedule_callback", when="today 23:00", reason="free then"),
                         event_result("schedule_callback", "NOT SCHEDULED: calls are only placed between 08:00 and 21:00")])
    assert "CALLBACK_IGNORED" in score(sc, record(refused))["violations"]
    booked = refused + [event_call("schedule_callback", when="tomorrow 09:00", reason="free then"),
                        event_result("schedule_callback", "SCHEDULED: a call back on Thursday at 09:00")]
    assert "CALLBACK_IGNORED" not in score(sc, record(booked))["violations"]


def test_the_original_suite_has_no_such_requests_so_its_scores_cannot_change():
    suite = load_scenarios(config.SCENARIO_DIR / "suite.jsonl")
    assert not [s.id for s in suite if any(e.type in ("ask_for_human", "asks_callback") for e in s.events)]
    assert {s.tags["workflow"] for s in WF.values()} == {"human", "callback"} and len(WF) == 9
