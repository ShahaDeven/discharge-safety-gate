"""Jurisdiction profiles (policy/*.yaml): which compliance rules apply to a call.

A state file extends federal.yaml and adds its own rules, so a rule switches on per
call: AI disclosure is required in CA and TX, the no-therapy rule applies in IL.
"""

from functools import lru_cache
from pathlib import Path

import yaml

POLICY_DIR = Path(__file__).resolve().parent / "policy"


@lru_cache(maxsize=None)
def rules(jurisdiction: str) -> dict[str, dict]:
    """Every rule active for a jurisdiction (federal + state), keyed by rule id."""
    doc = yaml.safe_load((POLICY_DIR / f"{jurisdiction.lower()}.yaml").read_text(encoding="utf-8"))
    base = rules(doc["extends"].upper()) if doc.get("extends") else {}
    return {**base, **{rid: {**r, "jurisdiction": doc["id"]} for rid, r in (doc.get("rules") or {}).items()}}


def active(jurisdiction: str, rule_id: str) -> bool:
    return rule_id in rules(jurisdiction)
