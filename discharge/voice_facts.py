"""Did a transcript keep the facts that matter? Deterministic checks, no model involved.

A benchmark line lists its critical facts; each is checked against a transcript after normalizing case,
punctuation, contractions and number words (English and Spanish):

    {"kind": "term", "any": ["water pill", "furosemide"], "not_any": ["luvox"]}
        one of `any` appears, and none of `not_any` (a sound-alike drug that would change the meaning)
    {"kind": "negation", "near": ["taken", "taking"], "window": 5}
        a negation (not, n't, never, no, ... / no, nunca, ...) within `window` words before one of `near`
    {"kind": "number", "value": 5, "near": ["pounds"], "window": 4}
        the number 5 (digits or words) within `window` words of one of `near`
    {"kind": "name", "value": "Micaela Rendón"}
        first and last name come through, compared without accents (the identity check's own rule)
    {"kind": "not_name", "value": "Micaela Rendón"}
        the patient's name must NOT appear: a wrong person with a similar name, transcribed with the
        patient's name as a hint, must not be turned into the patient
    {"kind": "dob", "value": "1963-06-12"}
        the day, the month (name or number) and the year all come through
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
_HUNDREDS = {"doscientos": 200, "trescientos": 300, "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600,
             "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_THOUSAND = {"thousand", "mil"}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
             "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
             "twentieth": 20, "thirtieth": 30, "primero": 1}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                           "septiembre", "octubre", "noviembre", "diciembre"], 1)})
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
        if w.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th)", w):
            out.append((i, int(re.match(r"\d+", w).group())))
            i += 1
            continue
        # A spoken year: "nineteen sixty three" is 1963, not 19 + 60 + 3.
        if w in ("nineteen", "twenty") and i + 1 < len(words) and (words[i + 1] in _TENS or words[i + 1] in _UNITS):
            rest = numbers(words[i + 1:i + 3])
            if rest and rest[0][0] == 0 and rest[0][1] < 100 and words[i + 1] not in ("one", "two", "three", "four",
                                                                                      "five", "six", "seven", "eight", "nine"):
                out.append((i, (1900 if w == "nineteen" else 2000) + rest[0][1]))
                i += 1 + (2 if len(words) > i + 2 and words[i + 1] in _TENS and words[i + 2] in _UNITS else 1)
                continue
        start, total, current, seen, last = i, 0, 0, False, ""
        while i < len(words):
            w = words[i]
            # Two plain number words in a row start a new number ("june twelve nineteen sixty" is 12, then a
            # year), and an ordinal ends one ("november fifth, nineteen sixty nine" is 5, then 1969).
            if w in _UNITS and last == "unit":
                break
            if w in _TENS and last in ("unit", "tens"):
                break
            if w in _UNITS:
                current += _UNITS[w]
                last = "unit"
            elif w in _ORDINALS:
                current += _ORDINALS[w]
                seen = True
                i += 1
                break
            elif w in _HUNDREDS:
                current += _HUNDREDS[w]
            elif w in _THOUSAND and (seen or i == start):
                total += max(current, 1) * 1000
                current = 0
            elif w in _TENS:
                current += _TENS[w]
                last = "tens"
            elif w in _SCALES and (seen or i == start or words[i - 1] == "a"):
                current = max(current, 1) * _SCALES[w]
                last = "scale"
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
    if kind in ("name", "not_name"):
        from .tools import name_matches
        found = name_matches(text, fact["value"])
        return found if kind == "name" else not found
    if kind == "dob":
        y, m, d = (int(x) for x in fact["value"].split("-"))
        vals = [v for _, v in numbers(words)]
        month_ok = any(MONTHS.get(w) == m for w in words) or m in vals
        return y in vals and d in vals and month_ok
    raise ValueError(f"unknown fact kind {kind!r}")


def preserved(facts: list[dict], text: str) -> list[bool]:
    return [check(f, text) for f in facts]
