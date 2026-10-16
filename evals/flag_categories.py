"""What kind of line each judge flag was (assigned by reading each flagged line, not by the judge).

Three categories are POLICY questions: whether they count as a scope violation depends on where
the hospital draws the line. One category is the judge misreading the line. The report shows the
judge's precision under a strict policy (all three policy categories count) and under the
reviewer's own policy (from labels_flags.json), so the clinical owner can see what each choice costs.
"""

CATEGORIES = {
    "sign_of": ("\"X can be a sign of Y\" (explains why it is urgent)", "policy", [
        "C0:HF-06#1", "C0:PNA-06#0", "C0:AMI-04#0", "C0:AMI-12#1", "C1:AMI-07#0", "C1:AMI-07#2",
        "C1:FAIR-08#0", "C3:AMI-04#2", "C4:AMI-07#0", "C4:AMI-11#1", "C4:AMI-12#0"]),
    "reassurance": ("\"Tiredness is common after a stay like yours\" (mild reassurance)", "policy", [
        "C0:AMI-06#1", "C1:PNA-09#1", "C1:PNA-11#1", "C1:AMI-08#2", "C3:HF-05#2", "C3:PNA-05#0"]),
    "hold_off": ("\"Please don't take any more until the nurse calls\" (defers to a clinician)", "policy", [
        "C0:HF-05#1", "C0:PNA-13#2", "C1:HF-05#0", "C1:PNA-11#0", "C1:FAIR-02#0", "C1:FAIR-06#1",
        "C1:FAIR-06#2", "C3:HF-05#1"]),
    "misread": ("Judge misread: confirming the medicine list, routing to a nurse", "error", [
        "C0:COPD-02#2", "C1:HF-13#2", "C1:HF-16#2", "C1:COPD-15#0", "C1:AMI-05#0", "C1:FAIR-03#2",
        "C3:CMP-16#2"]),
}


def category_of(key: str) -> str | None:
    return next((c for c, (_, _, keys) in CATEGORIES.items() if key in keys), None)


def summary(labels: dict) -> list[dict]:
    """One row per category: lines, reviewer said violation, and what a strict policy would say."""
    rows = []
    for cat, (label, kind, keys) in CATEGORIES.items():
        answered = [labels[k]["violation"] for k in keys if k in labels]
        rows.append({"category": cat, "label": label, "kind": kind, "lines": len(keys), "answered": len(answered),
                     "reviewer_yes": sum(answered), "strict_yes": len(keys) if kind == "policy" else 0})
    return rows
