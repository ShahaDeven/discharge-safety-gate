"""RxNorm normalization via the NLM RxNav REST API (free, no key), cached to disk.

    python -m discharge.rxnorm clopidogrel "sacubitril / valsartan"

Used when building scenarios: each medicine's ingredient is looked up once and its
brand names are added as aliases, so a flag that says "Plavix" matches an expected
clopidogrel issue. Scoring itself never calls the network; it reads the aliases stored
in the scenario. The cache (data/rxnorm_cache.json) is committed, so rebuilding the
suite works offline too.
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "rxnorm_cache.json"
API = "https://rxnav.nlm.nih.gov/REST"

_cache: dict | None = None


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{API}/{path}", timeout=20) as r:
        return json.load(r)


def _load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
    return _cache


def lookup(ingredient: str) -> dict:
    """{"rxcui": str | None, "brands": [...]} for an ingredient name ("sacubitril / valsartan")."""
    cache = _load()
    key = ingredient.lower().strip()
    if key in cache:
        return cache[key]
    ids = _get(f"rxcui.json?name={urllib.parse.quote(key)}").get("idGroup", {}).get("rxnormId") or []
    entry = {"rxcui": ids[0] if ids else None, "brands": []}
    if entry["rxcui"]:
        groups = _get(f"rxcui/{entry['rxcui']}/related.json?tty=BN")["relatedGroup"].get("conceptGroup", [])
        names = [c["name"] for g in groups for c in g.get("conceptProperties", [])]
        entry["brands"] = sorted({n for n in names if " - " not in n})   # drops "Salix - substance" style entries
    cache[key] = entry
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    return entry


def brand_aliases(ingredient: str) -> list[str]:
    try:
        return lookup(ingredient)["brands"]
    except OSError:              # offline and not cached: fall back to the hand-written aliases
        return []


if __name__ == "__main__":
    for name in sys.argv[1:]:
        print(name, lookup(name))
