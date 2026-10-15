"""Append-only change ledger: every candidate pushed through the gate, with its verdict.

    ledger.json   machine-readable, one entry per (candidate fingerprint, verdict)
    ledger.md     the same as a table for humans

Entries are never edited. Re-running the gate on an unchanged candidate with the same
verdict adds nothing; a changed prompt or a different verdict adds a new entry, so the
history of what was promoted, held or rejected, and why, stays readable. Each entry is
also written to the hash-chained audit log.
"""

import json
from datetime import date
from pathlib import Path

from .audit import AuditLog, content_hash


class Ledger:
    def __init__(self, path_json: Path, path_md: Path, audit: AuditLog | None = None):
        self.path_json, self.path_md, self.audit = Path(path_json), Path(path_md), audit

    def load(self) -> list[dict]:
        return json.loads(self.path_json.read_text(encoding="utf-8")) if self.path_json.exists() else []

    def record(self, candidate: str, description: str, fingerprint: str, baseline: str, verdict: str,
               reasons: list[str], summary: dict, k: dict, today: str | None = None) -> dict | None:
        """Add an entry unless the same candidate fingerprint already has this verdict. Returns it if added."""
        entries = self.load()
        key = content_hash([candidate, fingerprint, baseline, verdict])
        if any(e["key"] == key for e in entries):
            return None
        entry = {"id": f"L-{len(entries) + 1:03d}", "key": key, "date": today or date.today().isoformat(),
                 "candidate": candidate, "description": description, "fingerprint": fingerprint,
                 "baseline": baseline, "verdict": verdict, "reasons": reasons, "summary": summary, "k": k}
        entries.append(entry)
        self.path_json.parent.mkdir(parents=True, exist_ok=True)
        self.path_json.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")
        self.path_md.write_text(self.render(entries), encoding="utf-8")
        if self.audit:
            self.audit.append("gate", {k_: entry[k_] for k_ in ("id", "candidate", "fingerprint", "baseline",
                                                                "verdict", "reasons")})
        return entry

    @staticmethod
    def render(entries: list[dict]) -> str:
        lines = ["# Change ledger", "", "Append-only. Every change pushed through the gate, newest last.", "",
                 "| id | date | change | vs | verdict | why |", "|---|---|---|---|---|---|"]
        for e in entries:
            why = "<br>".join(e["reasons"]) or "no regression beyond the noise floor"
            lines.append(f"| {e['id']} | {e['date']} | **{e['candidate']}** {e['description']} | {e['baseline']} "
                         f"| **{e['verdict']}** | {why} |")
        return "\n".join(lines) + "\n"
