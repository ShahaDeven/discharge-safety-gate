"""Append-only, hash-chained audit log (JSONL).

Every entry carries the hash of the one before it, so editing or deleting any past
entry breaks the chain from that point on. `verify` finds the first broken entry.
This is the kind of record the HIPAA Security Rule's audit-controls standard
(45 CFR 164.312(b)) asks for: what ran, when, against which inputs, with what result.

    python -m agentgate.audit evals/results/audit.jsonl      # verify a log
"""

import hashlib
import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _digest(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def content_hash(obj) -> str:
    """Stable hash of any JSON-serialisable payload (used to reference transcripts)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._last: tuple[int, str] | None = None   # (seq, hash) once read; one writer per run

    def _tail(self) -> tuple[int, str]:
        if self._last is not None:
            return self._last
        if not self.path.exists():
            return 0, GENESIS
        last = None
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return 0, GENESIS
        e = json.loads(last)
        return e["seq"], e["hash"]

    def append(self, kind: str, payload: dict) -> dict:
        with self._lock:
            seq, prev = self._tail()
            entry = {"seq": seq + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "kind": kind, "payload": payload, "prev_hash": prev}
            entry["hash"] = _digest(entry)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._last = (entry["seq"], entry["hash"])
            return entry


def verify(path: Path) -> tuple[bool, int, str]:
    """(ok, entries checked, message). Stops at the first broken link."""
    prev = GENESIS
    n = 0
    path = Path(path)
    if not path.exists():
        return True, 0, "no log yet"
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            e = json.loads(line)
            n += 1
            if e.get("prev_hash") != prev:
                return False, n, f"line {lineno} (seq {e.get('seq')}): prev_hash does not match the entry before it"
            if _digest(e) != e.get("hash"):
                return False, n, f"line {lineno} (seq {e.get('seq')}): contents changed after it was written"
            prev = e["hash"]
    return True, n, "chain intact"


if __name__ == "__main__":
    ok, n, msg = verify(Path(sys.argv[1] if len(sys.argv) > 1 else "evals/results/audit.jsonl"))
    print(f"{'OK' if ok else 'BROKEN'}: {n} entries, {msg}")
    sys.exit(0 if ok else 1)
