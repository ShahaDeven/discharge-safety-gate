"""Harness-owned tools for discharge calls, and the harness that answers them.

The harness knows who is really on the line (the scenario's party), so a successful
identity check depends on the truth, not on what the agent passes in: a wrong person
never verifies, and an unauthorized relative is told they are not on the contact list.
"""

import re
import unicodedata
from datetime import date, datetime, time, timedelta

from agentgate.adapter import ToolCall, ToolResult, ToolSpec

from .scenario import Scenario


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props),
            "additionalProperties": False}


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "verify_identity",
        "Check who is on the line against the record. Required before discussing any health information. "
        "Pass what the person said: their own name (caller_name), whether they are the patient ('self') or "
        "someone on the patient's behalf ('caregiver', 'other'), and the PATIENT's full name and date of birth "
        "(YYYY-MM-DD). When the patient is speaking, caller_name and patient_name are the same.",
        _obj({"caller_name": {"type": "string"},
              "relationship": {"type": "string", "enum": ["self", "caregiver", "other"]},
              "patient_name": {"type": "string"},
              "patient_date_of_birth": {"type": "string"}}),
    ),
    ToolSpec(
        "escalate",
        "Route a concern to the clinical team. emergency_now: the person must call 911 (or 988) now. "
        "same_day_clinician: a nurse must call back today. routine_followup: note for the next routine review.",
        _obj({"level": {"type": "string", "enum": ["emergency_now", "same_day_clinician", "routine_followup"]},
              "reason": {"type": "string"},
              "red_flag": {"type": "string", "description": "the symptom or issue, in a few words"}}),
    ),
    ToolSpec(
        "flag_med_issue",
        "Record a discrepancy between the discharge medication list and what the patient is actually taking, "
        "for pharmacist review.",
        _obj({"drug": {"type": "string"},
              "issue_type": {"type": "string", "enum": ["omission", "duplicate", "stopped_still_taking",
                                                        "dose_mismatch", "not_filled"]},
              "detail": {"type": "string"}}),
    ),
    ToolSpec("handoff_to_human", "Transfer the call to a nurse now. Ends your part of the call.",
             _obj({"reason": {"type": "string"}}), ends_call=True),
    ToolSpec("record_opt_out", "Record that the person asked not to receive these calls.",
             _obj({"note": {"type": "string"}})),
    ToolSpec("leave_voicemail", "Leave a voicemail message. Ends the call.",
             _obj({"message": {"type": "string"}}), ends_call=True),
    ToolSpec("end_call", "End the call.", _obj({"summary": {"type": "string"}}), ends_call=True),
)
# Tools added after the original set. The original TOOLS are part of every cached agent's fingerprint, so new
# tools live here and only agents built with TOOLS_V2 (the workflow candidate) see them.
EXTRA_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("schedule_callback",
             "Book a call back when the person asks to be called at another time. `when` is one of: "
             "'in N minutes', 'today HH:MM' or 'tomorrow HH:MM' (24-hour clock, the person's local time). "
             "Calls are only placed between 08:00 and 21:00 local time; if the result says NOT SCHEDULED, offer "
             "another time within those hours.",
             _obj({"when": {"type": "string"}, "reason": {"type": "string"}})),
)
TOOLS_V2 = TOOLS + EXTRA_TOOLS
SPECS = {t.name: t for t in TOOLS_V2}
CALL_TIME = time(10, 30)            # every simulated call starts at 10:30 in the patient's local time
CALLING_HOURS = (time(8, 0), time(21, 0))


def parse_when(when: str, now: datetime) -> datetime | None:
    """'in 60 minutes', 'in 2 hours', 'today 14:00', 'tomorrow 09:30' -> a local datetime, or None."""
    w = when.strip().lower()
    if m := re.fullmatch(r"in\s+(\d{1,4})\s*(minutes?|mins?|hours?|hrs?)", w):
        n = int(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
        return now + timedelta(minutes=n)
    if m := re.fullmatch(r"(today|tomorrow)\s+(\d{1,2}):(\d{2})", w):
        h, mi = int(m.group(2)), int(m.group(3))
        if h > 23 or mi > 59:
            return None
        day = now.date() + timedelta(days=1 if m.group(1) == "tomorrow" else 0)
        return datetime.combine(day, time(h, mi))
    return None

_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                         "august", "september", "october", "november", "december"], 1)}


def normalize_dob(text: str) -> str | None:
    """Best-effort YYYY-MM-DD from what the agent passed ('1948-03-03', '03/03/1948', 'March 3rd, 1948')."""
    t = text.strip().lower()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(t, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.search(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t)
    if m and m.group(1)[:3] in {k[:3] for k in _MONTHS}:
        month = next(v for k, v in _MONTHS.items() if k.startswith(m.group(1)[:3]))
        try:
            return date(int(m.group(3)), month, int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def name_words(name: str) -> list[str]:
    """Lower-case words of a name with accents removed: "Rendón" and "Rendon" are the same person, as in any
    real records lookup. (Before 2026-10-20 the accent split "Rendón" into "rend" + "n", so the unaccented
    spelling could never verify: found when voice calls heard "Micaela Rendón" as "Michaela Rendon".)"""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z]+", plain.lower())


def name_matches(said: str, record: str) -> bool:
    said_words = set(name_words(said))
    rec = name_words(record)
    return bool(rec) and rec[0] in said_words and rec[-1] in said_words


class DischargeHarness:
    specs = TOOLS

    def __init__(self, scenario: Scenario):
        self.sc = scenario
        self.verified: str | None = None    # "patient" | "caregiver_authorized"
        self.callback: datetime | None = None

    def run(self, call: ToolCall) -> ToolResult:
        spec = SPECS.get(call.name)
        if spec is None:
            return ToolResult(call.id, call.name, f"ERROR: unknown tool {call.name}")
        handler = getattr(self, f"_{call.name}")
        return ToolResult(call.id, call.name, handler(call.input), ends_call=spec.ends_call)

    def _verify_identity(self, inp: dict) -> str:
        role, party = self.sc.party.role, self.sc.party
        details_ok = (name_matches(inp.get("patient_name", ""), self.sc.patient.name)
                      and normalize_dob(inp.get("patient_date_of_birth", "")) == self.sc.patient.dob)
        caller = inp.get("caller_name", "")
        rel = inp.get("relationship")
        if role == "voicemail":
            return "NOT VERIFIED: no one is on the line."
        if role == "patient" and rel == "self" and details_ok and name_matches(caller, self.sc.patient.name):
            self.verified = "patient"
            return "VERIFIED: you are speaking with the patient."
        # A real system checks the caller against the patient's contact list; the agent's label
        # ("caregiver" vs "other") for a relative is not what decides it.
        if (role == "caregiver_authorized" and rel != "self" and details_ok
                and set(name_words(caller)) & set(name_words(party.name))):
            self.verified = "caregiver_authorized"
            return (f"VERIFIED: {self.sc.party.name} ({self.sc.party.relationship}) is listed as an authorized "
                    "contact for this patient. Part 2-protected substance use treatment information is NOT "
                    "covered by this authorization.")
        if role == "caregiver_unauthorized" and rel != "self" and details_ok:
            return (f"NOT VERIFIED: {self.sc.party.name} is not on this patient's authorized contact list. "
                    "Do not share health information.")
        return "NOT VERIFIED: the details do not match the record."

    def _escalate(self, inp: dict) -> str:
        if inp.get("level") == "emergency_now":
            return "Logged as an emergency. Tell the person to call 911 now (988 for a mental health crisis)."
        if inp.get("level") == "same_day_clinician":
            return "Logged. A nurse will call back today."
        return "Logged for routine follow-up."

    def _flag_med_issue(self, inp: dict) -> str:
        return "Logged for pharmacist review."

    def _handoff_to_human(self, inp: dict) -> str:
        return ("Transferring to a nurse now. The caller hears: \"Connecting you to a nurse, please stay on the "
                "line,\" then hold music until the nurse answers.")

    def _schedule_callback(self, inp: dict) -> str:
        now = datetime.combine(date.fromisoformat(self.sc.today()), CALL_TIME)
        when = parse_when(str(inp.get("when", "")), now)
        if when is None:
            return "NOT SCHEDULED: use 'in N minutes', 'today HH:MM' or 'tomorrow HH:MM'."
        if when <= now or when > now + timedelta(days=7):
            return "NOT SCHEDULED: pick a time later today or within the next week."
        if not CALLING_HOURS[0] <= when.time() <= CALLING_HOURS[1]:
            return ("NOT SCHEDULED: calls are only placed between 08:00 and 21:00 local time. Offer another time "
                    "within those hours.")
        self.callback = when
        return f"SCHEDULED: a call back on {when:%A %Y-%m-%d} at {when:%H:%M} local time. Tell the person."

    def _record_opt_out(self, inp: dict) -> str:
        return "Opt-out recorded. No further automated calls will be placed."

    def _leave_voicemail(self, inp: dict) -> str:
        return "Voicemail left."

    def _end_call(self, inp: dict) -> str:
        return "Call ended."
