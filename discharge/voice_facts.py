"""Did a transcript keep the facts that matter? Deterministic checks, no model involved.

A benchmark line lists its critical facts; each is checked against a transcript after normalizing case,
punctuation, contractions and number words (English and Spanish):

    {"kind": "term", "any": ["water pill", "furosemide"], "not_any": ["luvox"]}
        one of `any` appears, and none of `not_any` (a sound-alike drug that would change the meaning)
    {"kind": "negation", "near": ["taken", "taking"], "window": 5}
        a negation (not, n't, never, no, ... / no, nunca, ...) within `window` words before one of `near`
    {"kind": "number", "value": 5, "near": ["pounds"], "window": 4}
        the number 5 (digits or words) within `window` words of one of `near`
"""

import re

NEGATIONS = {"not", "no", "never", "none", "nothing", "nor", "neither", "nunca", "jamas", "jamás", "ni", "tampoco"}
_UNITS = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
          "cero": 0, "uno": 1, "una": 1, "un": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
          "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
          "quince": 15, "dieciséis": 16, "dieciseis": 16, "veinte": 20}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}
_SCALES = {"hundred": 100, "cien": 100, "ciento": 100}
_JOINERS = {"and", "y"}


def tokens(text: str) -> list[str]:
    """Lowercase words; "haven't" becomes ["have", "not"]; hyphens split; punctuation dropped."""
    t = text.lower().replace("’", "'").replace("-", " ")
    t = re.sub(r"(\w)n't\b", r"\1 not", t)
    t = re.sub(r"\bcan not\b|\bcannot\b", "can not", t)
    t = re.sub(r"[^\w\s']", " ", t).replace("'", " ")
    return t.split()


def numbers(words: list[str]) -> list[tuple[int, int]]:
    """(position, value) for every number in the words: digits ("102") or number words ("a hundred and two")."""
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w.isdigit():
            out.append((i, int(w)))
            i += 1
            continue
        start, total, current, seen = i, 0, 0, False
        while i < len(words):
            w = words[i]
            if w in _UNITS:
                current += _UNITS[w]
            elif w in _TENS:
                current += _TENS[w]
            elif w in _SCALES and (seen or i == start or words[i - 1] == "a"):
                current = max(current, 1) * _SCALES[w]
            elif w in _JOINERS and seen and i + 1 < len(words) and (words[i + 1] in _UNITS or words[i + 1] in _TENS):
                i += 1
                continue
            elif w == "a" and i + 1 < len(words) and words[i + 1] in _SCALES:
                i += 1
                continue
            else:
                break
            seen = True
            i += 1
        if seen:
            out.append((start, total + current))
        else:
            i = start + 1
    return out


def _has_phrase(words: list[str], phrase: str) -> bool:
    p = tokens(phrase)
    return any(words[i:i + len(p)] == p for i in range(len(words) - len(p) + 1))


def _positions(words: list[str], near: list[str]) -> list[int]:
    return [i for i, w in enumerate(words) for n in near if w == n.lower() or (len(n) > 4 and w.startswith(n.lower()))]


def check(fact: dict, text: str) -> bool:
    words = tokens(text)
    kind = fact["kind"]
    if kind == "term":
        return any(_has_phrase(words, p) for p in fact["any"]) and not any(_has_phrase(words, p)
                                                                           for p in fact.get("not_any", []))
    if kind == "negation":
        w = fact.get("window", 5)
        return any(any(words[j] in NEGATIONS for j in range(max(0, i - w), i)) for i in _positions(words, fact["near"]))
    if kind == "number":
        w = fact.get("window", 4)
        nums = numbers(words)
        return any(abs(pos - i) <= w and val == fact["value"] for i in _positions(words, fact["near"]) for pos, val in nums)
    raise ValueError(f"unknown fact kind {kind!r}")


def preserved(facts: list[dict], text: str) -> list[bool]:
    return [check(f, text) for f in facts]
