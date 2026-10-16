"""Scenario schema. Ground truth (what is wrong, who is on the line, what the agent must
do) is set here, in data, never by an LLM, so the answer key is exact.

Pilot scenarios are hand-written in scenarios/pilot.yaml; scenarios/build.py writes
scenarios.jsonl in the same shape.
"""

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from agentgate.adapter import CallContext, Med

from . import config
from .conditions import CONDITIONS, WARNING_SIGNS

Disclosure = Literal["explicit", "implicit", "only_if_asked"]
IssueType = Literal["omission", "duplicate", "stopped_still_taking", "dose_mismatch", "not_filled"]
Disposition = Literal["emergency_now", "same_day_clinician", "routine_followup", "none"]
DISPOSITION_RANK = {"none": 0, "routine_followup": 1, "same_day_clinician": 2, "emergency_now": 3}


class MedSpec(BaseModel):
    name: str
    dose: str = ""
    frequency: str = ""
    note: str = ""
    aliases: list[str] = Field(default_factory=list)   # brand names etc., for health-info term matching

    def to_med(self) -> Med:
        return Med(self.name, self.dose, self.frequency, self.note)


class PatientSpec(BaseModel):
    name: str
    dob: str                    # YYYY-MM-DD
    age: int
    sex: Literal["female", "male"]
    language: Literal["English", "Spanish"] = "English"
    literacy: Literal["standard", "low"] = "standard"
    hearing: Literal["normal", "hard_of_hearing"] = "normal"
    lay_reason: str             # how the patient would describe why they were in hospital


class PartySpec(BaseModel):
    role: Literal["patient", "caregiver_authorized", "caregiver_unauthorized", "wrong_person", "voicemail"]
    name: str = ""
    relationship: str = ""      # "daughter", "roommate", ...
    knows_dob: bool = True


class FactSpec(BaseModel):
    id: str                     # F1, F2, ...
    domain: str                 # one of conditions.DOMAINS
    truth: str                  # what is actually going on (for the simulator only)
    phrasing: str               # how this person would put it
    disclosure: Disclosure
    red_flag: str | None = None  # taxonomy code when the fact is a warning sign


class EventSpec(BaseModel):
    type: Literal["ask_if_ai", "opt_out", "dosing_question", "is_this_normal", "ask_for_human",
                  "asks_about_patient_meds"]
    text: str
    at_reply: int               # the simulator's Nth reply


class MedIssue(BaseModel):
    drug: str
    issue_type: IssueType


class Expected(BaseModel):
    disposition: Disposition
    # Highest level that is still acceptable when there is no red flag. A same-day nurse call for an
    # unfilled maintenance inhaler is reasonable; for a patient who is simply fine it is a false escalation.
    max_ok: Disposition = "routine_followup"
    red_flags: list[str] = Field(default_factory=list)
    med_issues: list[MedIssue] = Field(default_factory=list)
    opt_out: bool = False
    handoff: bool = False


class Scenario(BaseModel):
    id: str
    block: Literal["clinical", "compliance", "psych", "fairness"]
    condition: Literal["HF", "COPD", "PNA", "AMI", "PSY"]
    jurisdiction: Literal["NY", "CA", "TX", "IL"] = "NY"
    patient: PatientSpec
    party: PartySpec
    discharge_date: str
    call_date: str = ""          # defaults to 2 days after discharge (AHRQ RED: call within 2-3 days)
    discharge_summary: str
    discharge_meds: list[MedSpec]
    home_meds: list[MedSpec]    # what is actually in the patient's cabinet, discrepancies included
    facts: list[FactSpec] = Field(default_factory=list)
    events: list[EventSpec] = Field(default_factory=list)
    part2_record: bool = False
    expected: Expected
    notes: str = ""
    tags: dict[str, str] = Field(default_factory=dict)   # grid cell: disclosure, severity, recipe, persona...

    def today(self) -> str:
        return self.call_date or (date.fromisoformat(self.discharge_date) + timedelta(days=2)).isoformat()

    def context(self, call_id: str) -> CallContext:
        return CallContext(
            call_id=call_id,
            organization=config.ORGANIZATION,
            callback_number=config.CALLBACK_NUMBER,
            jurisdiction=self.jurisdiction,
            patient_name=self.patient.name,
            patient_dob=self.patient.dob,
            discharge_date=self.discharge_date,
            discharge_diagnosis=CONDITIONS[self.condition],
            discharge_summary=self.discharge_summary,
            discharge_meds=tuple(m.to_med() for m in self.discharge_meds),
            warning_signs=WARNING_SIGNS[self.condition],
            call_date=self.today(),
            part2_record=self.part2_record,
        )


def load_scenarios(path: Path) -> list[Scenario]:
    path = Path(path)
    if path.suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        raw = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    scenarios = [Scenario.model_validate(r) for r in raw]
    ids = [s.id for s in scenarios]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate scenario ids: {sorted(dupes)}")
    return scenarios
