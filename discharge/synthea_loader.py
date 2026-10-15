"""Turn the Synthea sample CSVs into a small patient pool for scenario building.

    python -m discharge.synthea_loader            # reads data/synthea_raw/, writes data/synthea/patients.json

Synthea (MITRE, Apache-2.0) is an open-source generator of synthetic patients; the raw
download stays out of git (data/synthea_raw/ is gitignored), only this pool is kept.

Used: name (Synthea's numeric suffixes stripped), birth date, sex, ethnicity, and up to
two background medicines from a safe whitelist. Background medicines are continued
unchanged on both the discharge and home lists, so they add realistic review work for
the agent without touching the answer key. Condition medicines and the seeded
discrepancies come from build.py, not from Synthea.
"""

import csv
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "synthea_raw"
OUT = ROOT / "data" / "synthea" / "patients.json"
AS_OF = date(2026, 10, 13)

# Synthea description prefix -> the medicine as it would appear on a discharge list.
BACKGROUND = {
    "24 HR Metformin": {"name": "metformin ER", "dose": "500 mg", "frequency": "once daily", "aliases": ["Glucophage"]},
    "Levothyroxine": {"name": "levothyroxine", "dose": "50 mcg", "frequency": "once daily", "aliases": ["Synthroid"]},
    "Alendronic acid": {"name": "alendronate", "dose": "70 mg", "frequency": "once weekly", "aliases": ["Fosamax"]},
    "cetirizine": {"name": "cetirizine", "dose": "10 mg", "frequency": "once daily", "aliases": ["Zyrtec"]},
    "Loratadine": {"name": "loratadine", "dose": "10 mg", "frequency": "once daily", "aliases": ["Claritin"]},
    "ferrous sulfate": {"name": "ferrous sulfate", "dose": "325 mg", "frequency": "once daily", "aliases": ["iron pill"]},
    "amLODIPine": {"name": "amlodipine", "dose": "5 mg", "frequency": "once daily", "aliases": ["Norvasc"]},
}


def _clean(name: str) -> str:
    return re.sub(r"\d+", "", name).strip()


def _age(dob: str) -> int:
    b = date.fromisoformat(dob)
    return AS_OF.year - b.year - ((AS_OF.month, AS_OF.day) < (b.month, b.day))


def load(raw: Path = RAW) -> list[dict]:
    patients = {}
    with (raw / "patients.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["DEATHDATE"]:
                continue
            age = _age(r["BIRTHDATE"])
            if not 25 <= age <= 92:
                continue
            patients[r["Id"]] = {
                "name": f"{_clean(r['FIRST'])} {_clean(r['LAST'])}",
                "dob": r["BIRTHDATE"], "age": age,
                "sex": "female" if r["GENDER"] == "F" else "male",
                "hispanic": r["ETHNICITY"] == "hispanic",
                "background_meds": [],
            }
    with (raw / "medications.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = patients.get(r["PATIENT"])
            if p is None or r["STOP"]:
                continue
            for prefix, med in BACKGROUND.items():
                if r["DESCRIPTION"].startswith(prefix) and med not in p["background_meds"] \
                        and len(p["background_meds"]) < 2:
                    p["background_meds"].append(med)
    return sorted(patients.values(), key=lambda p: p["name"])


def main() -> None:
    pool = load()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(pool, indent=1, ensure_ascii=False), encoding="utf-8")
    with_meds = sum(bool(p["background_meds"]) for p in pool)
    print(f"{len(pool)} living adults (25-92) -> {OUT.relative_to(ROOT)}; {with_meds} with background medicines; "
          f"ages {min(p['age'] for p in pool)}-{max(p['age'] for p in pool)}; "
          f"{sum(p['age'] >= 50 for p in pool)} aged 50+")


if __name__ == "__main__":
    main()
