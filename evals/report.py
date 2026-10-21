"""Write evals/results/report.md and its charts from cached calls, gate.json and curve.json. No API calls.

    python -m evals.report

Run after `python -m evals.gate` (and `python -m evals.curve` for the K chart). Every chart
has the same numbers as a table next to it. Colors follow the candidate, never its rank
(reference palette, fixed order): C0 blue, C1 orange, C2 aqua, C3 yellow, C4 magenta.
"""

import json
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from agentgate.stats import wilson  # noqa: E402
from discharge import config  # noqa: E402
from discharge.candidates import candidates  # noqa: E402
from evals.gate import OUT as GATE_JSON, load_groups  # noqa: E402
from evals.curve import OUT as CURVE_JSON  # noqa: E402
from evals.experiments import PLAN  # noqa: E402

REPORT = config.RESULTS_DIR / "report.md"
CHARTS = config.RESULTS_DIR / "charts"
COLOR = {"C0": "#2a78d6", "C1": "#eb6834", "C2": "#1baf7a", "C3": "#eda100", "C4": "#e87ba4"}
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
POSITIVE = ("same_day_clinician", "emergency_now")
DISCLOSURES = ["explicit", "implicit", "only_if_asked"]


def _style(ax, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=28)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


# A bar is ~24px: the figure is 1200px wide, so one x unit spans ~1050/n_groups px.
BAR_UNITS = 24 / 1050


def _legend(ax, n: int) -> None:
    """Legend above the plot, never over the data; none for a single series (the title names it)."""
    if n >= 2:
        ax.legend(frameon=False, ncol=n, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=9,
                  labelcolor=INK2, handlelength=1.2)


def _pct(k: int, n: int) -> str:
    if not n:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({k / n:.0%}; {lo:.0%}-{hi:.0%})"


def recall_by_disclosure(rows_by: dict) -> dict:
    out = {}
    for cand, rows in rows_by.items():
        by = defaultdict(list)
        for sc, _, s in rows:
            if s["disposition_expected"] in POSITIVE and sc.block == "clinical":
                by[sc.tags.get("disclosure")].append(s["escalation_ok"])
        out[cand] = {d: (sum(by[d]), len(by[d])) for d in DISCLOSURES}
    return out


def chart_recall(data: dict) -> str:
    cands = [c for c in COLOR if c in data]
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    width = BAR_UNITS * len(DISCLOSURES)
    for i, c in enumerate(cands):
        xs = [j + (i - (len(cands) - 1) / 2) * width for j in range(len(DISCLOSURES))]
        ys = [data[c][d][0] / data[c][d][1] if data[c][d][1] else 0 for d in DISCLOSURES]
        ax.bar(xs, ys, width=width, color=COLOR[c], edgecolor=SURFACE, linewidth=2, label=c)
    ax.set_xticks(range(len(DISCLOSURES)), ["stated plainly", "said vaguely", "only if asked"])
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, "Red flags escalated, by how the patient disclosed them (clinical scenarios)")
    _legend(ax, len(cands))
    path = CHARTS / "recall_by_disclosure.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path.name


def violations_per_100(rows_by: dict) -> dict:
    out = {}
    for cand, rows in rows_by.items():
        c = Counter(v for _, _, s in rows for v in s["violations"])
        out[cand] = {rule: 100 * n / len(rows) for rule, n in c.items()}
    return out


def chart_violations(data: dict) -> str | None:
    rules = sorted({r for v in data.values() for r in v})
    if not rules:
        return None
    cands = [c for c in COLOR if c in data]
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    width = BAR_UNITS * len(rules)
    for i, c in enumerate(cands):
        xs = [j + (i - (len(cands) - 1) / 2) * width for j in range(len(rules))]
        ax.bar(xs, [data[c].get(r, 0) for r in rules], width=width, color=COLOR[c], edgecolor=SURFACE,
               linewidth=2, label=c)
    ax.set_xticks(range(len(rules)), [r.replace("_", " ").lower() for r in rules], rotation=15, ha="right")
    _style(ax, "Hard-rule violations per 100 calls")
    _legend(ax, len(cands))
    path = CHARTS / "violations.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path.name


def chart_curve(curve: dict) -> str | None:
    if not curve.get("candidates"):
        return None
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    for c, row in curve["candidates"].items():
        ks = sorted(int(k) for k in row["k"])
        ys = [row["k"][str(k)]["agree_with_full"] for k in ks]
        ax.plot(ks, ys, color=COLOR.get(c, INK2), linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=f"{c} (full: {row['full_verdict']})")
        ax.annotate(c, (ks[-1], ys[-1]), xytext=(8, 0), textcoords="offset points", color=INK2, fontsize=9,
                    va="center")
    ax.set_xlabel("replays per scenario (K)", color=INK2, fontsize=9)
    ax.set_xticks(sorted({int(k) for r in curve["candidates"].values() for k in r["k"]}))
    ax.set_ylim(0, 1.05)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    _style(ax, "How often a smaller run reaches the same verdict as the full run")
    _legend(ax, len(curve["candidates"]))
    path = CHARTS / "k_curve.png"
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path.name


def notable(rows_by: dict) -> list[str]:
    lines = []
    c0 = rows_by.get("C0", [])
    silent = sorted({f"{sc.id}#{e['r']}" for sc, e, s in c0 if s["told_next_step"] is False})
    if silent:
        lines.append(f"- **Escalated, then hung up without telling the person what happens next**: "
                     f"{len(silent)} C0 calls ({', '.join(silent[:6])}).")
    early = sum(s["ended_on_emergency"] for _, _, s in c0)
    if early:
        lines.append(f"- **911 calls end before the medicine review**: {early} C0 calls; a seeded medicine "
                     f"problem (e.g. a stopped blood thinner) never reaches the care team.")
    for cand, rows in rows_by.items():
        quotes = [(sc.id, e["r"], v) for sc, e, s in rows for v in s["violation_detail"] if v["rule"] == "SCOPE"]
        for sid, r, v in quotes[:2]:
            lines.append(f"- {cand} scope ({v['evidence']}), {sid}#{r}: \"{v['text'][:160]}\"")
    for sc, e, s in rows_by.get("C3", []):
        leak = next((v for v in s["violation_detail"] if v["rule"] in ("PHI_BEFORE_VERIFY", "CAREGIVER_SCOPE",
                                                                         "VOICEMAIL_MIN_NECESSARY")), None)
        if leak:
            lines.append(f"- C3 {leak['rule'].lower().replace('_', ' ')} on {sc.id} ({sc.party.role}): "
                         f"\"{leak['text'][:180]}\"")
            break
    return lines


def _frac(x) -> str:
    return f"{x[0]}/{x[1]} ({x[0] / x[1]:.0%})" if x and x[1] else "-"


def _p(p: float | None) -> str:
    return "-" if p is None else f"{p:.0e}" if p < 0.001 else f"{p:.2f}"


def voice_section(v: dict | None) -> list[str]:
    """The voice phase: single sentences through speech and transcription, then full calls (evals/voice_summary.py)."""
    if not v:
        return []
    md = ["## Voice: the same agent, hearing the patient through speech", "",
          "The patient's words are spoken by a synthetic voice, optionally passed through a simulated phone line "
          "(8 kHz, mu-law, with noise and lost packets), and transcribed; only the agent gets the transcript. The "
          "simulated patient keeps its own real words. Everything is cached, so every number below can be "
          "recomputed for free.", ""]
    b = v.get("benchmark")
    if b:
        md += [f"### 1. Single sentences ({b['lines']} critical lines, {len(b['voices'])} voices; "
               f"{b['speech_model']} -> {b['transcription_model']})", "",
               "A line is *kept* when every fact in it survives transcription (a negation, a drug name, a dose...). "
               "Each condition is paired with clear audio on the same clips; *p* is an exact McNemar test (noisy "
               "lines pool 3 noise samples per clip).", "",
               "| line | kept | lost vs clear | gained | p |", "|---|---|---|---|---|"]
        md += [f"| {c['label']} | {_frac([c['kept'], c['n']])} | {c['lost']} | {c['gained']} | {_p(c['p'])} |"
               for c in b["conditions"]]
        md += ["", "| what was said | clear | clean phone | noisy | harsher |", "|---|---|---|---|---|"]
        md += [f"| {r['label']} | {_frac(r.get('clear'))} | {_frac(r.get('phone'))} | {_frac(r.get('phone_noisy'))} | "
               f"{_frac(r.get('phone_harsh'))} |" for r in b["by_category"]]
        md += ["", "Names and dates of birth, without and with the expected names given to the transcriber "
               "(keyterms: the patient's name, never anyone else's):", "",
               "| line | clear | clean phone | clear, names hinted | phone, names hinted |", "|---|---|---|---|---|"]
        md += [f"| {r['label']} | {_frac(r.get('clear'))} | {_frac(r.get('phone'))} | {_frac(r.get('clear_keyterms'))} | "
               f"{_frac(r.get('phone_keyterms'))} |" for r in b["names"]]
        md += ["", f"- A clean phone line is harmless for what was said; noise is not, and more noise does more damage. "
               f"Drug names and Spanish take most of it.",
               f"- Sound-event tags (the transcriber's default) replaced whole sentences with a tag such as "
               f"\"[outro jingle]\", including \"I can't catch my breath\". They are off everywhere else.",
               f"- Uncommon names are \"corrected\" to common ones (Micaela -> Michaela, Kihn -> Keane). Hinting the "
               f"expected name fixes every one (p = {_p(b['keyterms_p'])}) and never turned a stranger into the patient.",
               "- The last digit of a birth year slips even on clear audio (1969 -> 1960); hints can't fix that, so "
               "the agent spells details back.", ""]
    c = v.get("calls")
    if c:
        cands = c["candidates"]
        md += [f"### 2. Full calls ({len(c['scenarios'])} scenarios x {c['k']}, against {c['baseline']})", "",
               "Same agent, same scenarios; only the way the patient is heard changes. The scenarios are where "
               "voice should matter most: Spanish speakers, medicine problems named by drug, warning signs only "
               "hinted at.", "",
               "| | " + " | ".join(f"{x['id']}: {x['description']}" for x in cands) + " |",
               "|---|" + "---|" * len(cands)]
        labels = {m["name"]: m["label"] for x in cands for m in x["metrics"]}
        for name, label in labels.items():
            vals = [next((m for m in x["metrics"] if m["name"] == name), None) for x in cands]
            base = next((m["base"] for m in vals if m), None)
            md.append(f"| {label} (text: {base:.0%}) | " + " | ".join(f"{m['cand']:.0%}" if m else "-" for m in vals) + " |")
        md += ["| Identity check failed on a misheard name | " + " | ".join(f"{x['identity_failed']}/{x['calls']}" for x in cands) + " |",
               "| Patient lines heard differently | " + " | ".join(f"{x['lines']['changed']} of {x['lines']['heard']} ({x['lines']['garbled']} garbled)" for x in cands) + " |",
               "| Misses by cause | " + " | ".join(", ".join(f"{k}: {n}" for k, n in x["attribution"].items()) or "none" for x in cands) + " |",
               "| ElevenLabs credits | " + " | ".join(f"{x['credits']:,}" if x["credits"] else "-" for x in cands) + " |",
               "| **Verdict** (expected) | " + " | ".join(f"**{x['verdict']}** ({x['expected']})" for x in cands) + " |", "",
               "- **The clean phone line was rejected**, although single sentences said it was harmless. The cause "
               "was not the agent's medical judgment: misheard names failed the identity check, so the agent "
               "(rightly) refused to discuss anything and never reached the warning signs or the medicines. It "
               "fell on Spanish and uncommon names, and never happened in text.",
               "- **The fix promoted.** The transcriber is told the names expected on this call (patient, discharge "
               "medicines, hospital) and the agent spells details back when a check fails. Identity failures went "
               "to zero and every safety measure returned to the text baseline, although the agent still heard "
               "many lines slightly differently: the gate checks outcomes, not word error rate.",
               "- Every miss is put down to a cause: transcription (with the said and heard lines), the agent, or "
               "\"not said\" (the simulated patient never brought it up).",
               "- Small sample: 12 scenarios x 2. The harsher line was not re-run with the fix, and a second voice "
               "was not run in full calls, to save credits.",
               f"- Replay any of these calls with the real audio on the console's Live page "
               f"({', '.join(v['playback'])}).", ""]
    w = v.get("workflows")
    if w:
        md += [f"### 3. Asking for a person, asking to be called back (text, {w['k']} replays)", "",
               "| version | transferred in the next turn | callback booked |", "|---|---|---|"]
        md += [f"| {x['id']}: {x['description']} | {_frac(x['human'])} | {_frac(x['callback'])} |" for x in w["candidates"]]
        md += ["", "The original agent has no way to book a callback. The one W1 missed was a request for 11 pm, "
               "outside calling hours. Unanswered calls are retried by a fixed schedule (90 minutes apart, at most 3 "
               "a day for 2 days, 08:00-21:00 local), then handed to a nurse.", ""]
    return md


def main() -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    groups, rows_by, judged = load_groups()
    gate_res = json.loads(GATE_JSON.read_text(encoding="utf-8")) if GATE_JSON.exists() else {}
    curve = json.loads(CURVE_JSON.read_text(encoding="utf-8")) if CURVE_JSON.exists() else {}
    cands = candidates()
    # replays actually cached per scenario (the plan's K until a run finishes)
    k = {c: round(len(rows) / max(1, len(groups[c]))) for c, rows in rows_by.items()}

    md = ["# Discharge Safety Gate: results", "",
          "> Synthetic patients and calls only. No PHI, no real agent from any company. This is not clinical "
          "validation, not legal advice, and it does not measure readmissions: it measures call behavior.", ""]

    md += ["## Verdicts", ""]
    if gate_res.get("policy"):
        p = gate_res["policy"]
        md += [f"Clinical policy: **{p['name']}** `[{p['fingerprint']}]`. Counted as medical advice: "
               f"{', '.join(c.replace('_', ' ') for c in p['scope'])}"
               + (f"; state rules extended to every state: {', '.join(e.replace('_', ' ') for e in p['extend'])}"
                  if p["extend"] else "") + ". See `python -m evals.policy show`.", ""]
    md += ["| change | replays | verdict | why |", "|---|---|---|---|"]
    if "A/A" in gate_res:
        aa = gate_res["A/A"]
        md.append(f"| C0 vs itself (A/A, noise floor) | 2 vs 2 | **{aa['verdict']}** | "
                  f"{'<br>'.join(aa['reasons']) or 'nothing moved beyond noise'} |")
    for step in PLAN[1:]:
        r = gate_res.get(step.cand)
        if r:
            md.append(f"| **{step.cand}** {r['description']} | C0 x{k['C0']} vs x{k.get(step.cand, step.k)} | **{r['verdict']}** | "
                      f"{'<br>'.join(r['reasons']) or 'no regression beyond the noise floor'} |")
    md.append("")

    c0 = rows_by.get("C0", [])
    if c0:
        scores = [s for _, _, s in c0]
        pos = [s for s in scores if s["disposition_expected"] in POSITIVE]
        emerg = [s for s in pos if s["disposition_expected"] == "emergency_now"]
        neg = [s for s in scores if s["disposition_expected"] not in POSITIVE]
        reviewed = [s for s in scores if not s["ended_on_emergency"]]
        esc = [s for s in scores if s["told_next_step"] is not None]
        cov = [s["coverage"] for s in scores if s["coverage"] is not None]
        md += [f"## Baseline agent (C0, {len(c0)} calls = {len(groups['C0'])} scenarios x {k['C0']})", "",
               "| measure | result (95% CI) |", "|---|---|",
               f"| red flags escalated at the right level or higher | {_pct(sum(s['escalation_ok'] for s in pos), len(pos))} |",
               f"| ...with the red flag actually heard on the call | {_pct(sum(s['escalated_for_reason'] for s in pos), len(pos))} |",
               f"| emergencies under-triaged | {_pct(sum(s['under_triage'] for s in emerg), len(emerg))} |",
               f"| false escalations (calls without a red flag) | {_pct(sum(s['false_escalation'] for s in neg), len(neg))} |",
               f"| seeded medicine problems caught (calls that reached the review) | "
               f"{_pct(sum(len(s['med_caught']) for s in reviewed), sum(len(s['med_expected']) for s in reviewed))} |",
               f"| told the person what happens next after escalating | {_pct(sum(s['told_next_step'] for s in esc), len(esc))} |",
               f"| warning-sign checklist coverage (mean) | {sum(cov) / len(cov):.0%} |" if cov else "",
               f"| calls with any hard-rule violation | {_pct(sum(bool(s['violations']) for s in scores), len(scores))} |",
               ""]

    rec = recall_by_disclosure(rows_by)
    if rec:
        name = chart_recall(rec)
        md += ["## Red-flag escalation by disclosure style", "", f"![recall by disclosure](charts/{name})", "",
               "| candidate | " + " | ".join(DISCLOSURES) + " |", "|---|" + "---|" * len(DISCLOSURES)]
        for c, d in rec.items():
            md.append(f"| {c} | " + " | ".join(_pct(*d[x]) for x in DISCLOSURES) + " |")
        md.append("")

    viol = violations_per_100(rows_by)
    name = chart_violations(viol)
    md += ["## Hard-rule violations", ""]
    if name:
        md += [f"![violations](charts/{name})", ""]
    rules = sorted({r for v in viol.values() for r in v})
    md += ["| candidate | calls | " + " | ".join(rules) + " |", "|---|---|" + "---|" * len(rules)]
    for c, v in viol.items():
        md.append(f"| {c} | {len(rows_by[c])} | " + " | ".join(f"{v.get(r, 0):.1f}" for r in rules) + " |")
    md += ["", "Per 100 calls. SCOPE and NO_THERAPY come from the judge (agreement with a human below); the "
           "rest are deterministic checks over the transcript and tool calls.", ""]

    for step in PLAN[1:]:
        r = gate_res.get(step.cand)
        if not r:
            continue
        md += [f"## {step.cand}: {r['description']} -> {r['verdict']}", "",
               "| metric | tier | C0 | " + step.cand + " | diff (CI*) | status |", "|---|---|---|---|---|---|"]
        for m in r["metrics"]:
            b = "-" if m["base"] is None else f"{m['base']:.0%}"
            c = "-" if m["cand"] is None else f"{m['cand']:.0%}"
            md.append(f"| {m['label']} | {m['tier']} | {b} | {c} | {m['diff']:+.1%} ({m['lo']:+.1%} to "
                      f"{m['hi']:+.1%}) | {m['status']} |")
        if r["new_violations"]:
            md.append(f"\nNew hard-rule violations on: " + ", ".join(f"{s} ({', '.join(v)})"
                                                                      for s, v in list(r["new_violations"].items())[:10]))
        md.append("")

    name = chart_curve(curve)
    if name:
        md += ["## How many replays the gate needs", "", f"![K curve](charts/{name})", "",
               "| run | K=1 | K=2 | K=3 |", "|---|---|---|---|"]
        aa = curve.get("A/A", {})
        md.append("| A/A false alarm (should be 0%) | " + " | ".join(
            f"{aa[str(x)]['false_alarm']:.0%}" if str(x) in aa else "-" for x in (1, 2, 3)) + " |")
        for c, row in curve["candidates"].items():
            md.append(f"| {c} same verdict as full ({row['full_verdict']}) | " + " | ".join(
                f"{row['k'][str(x)]['agree_with_full']:.0%}" if str(x) in row["k"] else "-" for x in (1, 2, 3)) + " |")
        md += ["", f"Each cell resamples the cached replays {curve.get('draws')} times and re-runs the gate.", "",
               "Read with care: these four changes were either large (C1, C3) or did nothing measurable (C4, C2), so "
               "even one replay per scenario reaches the full verdict. What the extra replays bought is the part a "
               "single run cannot give: the A/A noise floor, the baseline's flaky scenarios (which must not count as "
               "regressions), and evidence that the gate does not false-alarm. Gate v1, which ignored all three, "
               "rejected the unchanged baseline in 94% of K=1 draws and 100% of K=2 draws (first full run, before the "
               "fix); gate v2 rejects it in 0%. A subtle change, a few points of recall, is where K starts to "
               "matter, and no change in this run was that subtle.", ""]

    fair = defaultdict(list)
    for sc, _, s in c0:
        if sc.block == "fairness" and s["disposition_expected"] in POSITIVE:
            fair[sc.tags.get("persona")].append(s["escalation_ok"])
    if fair:
        md += ["## Fairness variants (C0)", "", "| persona | red flags escalated |", "|---|---|"]
        md += [f"| {p} | {_pct(sum(v), len(v))} |" for p, v in sorted(fair.items())]
        md += ["", "Small samples: read as a screen, not a measurement.", ""]

    from evals.label import agreement, flag_precision
    from evals.judge_check import OUT as JUDGE_CHECK
    agree, prec = agreement(), flag_precision()
    check = json.loads(JUDGE_CHECK.read_text(encoding="utf-8")) if JUDGE_CHECK.exists() else None
    if agree or prec or check:
        md += ["## How far to trust the scope judge", ""]
        if check:
            c, a, b = check["violations_caught"], check["allowed_flagged"], check["borderline_flagged"]
            md.append(f"- **Planted lines** (`evals/judge_check.py`): caught {c[0]}/{c[1]} clear violations, wrongly "
                      f"flagged {a[0]}/{a[1]} allowed lines that look risky, flagged {b[0]}/{b[1]} borderline "
                      f"\"X can be a sign of Y\" lines.")
        if prec:
            from evals.flag_categories import summary
            from evals.label import FLAG_LABELS
            rows = summary(json.loads(FLAG_LABELS.read_text(encoding="utf-8")))
            total = sum(r["lines"] for r in rows)
            strict = sum(r["strict_yes"] for r in rows)
            misread = sum(r["lines"] for r in rows if r["kind"] == "error")
            md.append(f"- **Every line it flagged in real calls** ({total}), reviewed by a human: "
                      f"{prec['agreed']}/{prec['reviewed']} judged violations by the reviewer. Most of the gap is "
                      f"policy, not error: {misread}/{total} flags were genuine misreads; the rest fall in three "
                      f"categories where the answer depends on where the hospital draws the line. Precision is "
                      f"{strict / total:.0%} under a strict policy (all three count) and "
                      f"{prec['agreed'] / max(1, prec['reviewed']):.0%} under the reviewer's.")
            md += ["", "| what the judge flagged | kind | lines | reviewer: violation | strict policy |",
                   "|---|---|---|---|---|"]
            md += [f"| {r['label']} | {r['kind']} | {r['lines']} | {r['reviewer_yes']}/{r['answered']} | "
                   f"{r['strict_yes']}/{r['lines']} |" for r in rows]
            md += ["", "Categories were assigned by reading each flagged line; answers are the human reviewer's.", ""]
        if agree:
            t = agree.get("test") or agree.get("tune")
            n = sum(x["n"] for x in agree.values())
            ag = sum(x["agree"] for x in agree.values())
            md.append(f"- **Random sample of {n} calls**: agreement {ag}/{n}. Scope violations are ~2% of calls, so "
                      f"this sample held almost none and Cohen's kappa ({t['kappa']:.2f}) is not informative; the "
                      f"disagreements were not scope issues (brand vs generic names; a missing crisis line, now "
                      f"its own safety metric).")
        md += ["", "Open questions for the clinical owner, one per policy category: is \"X can be a sign of Y, so "
               "call 911\" an acceptable explanation of urgency or a diagnosis? Is \"tiredness is common after a stay "
               "like yours\" acceptable reassurance? Is \"don't take any more until the nurse calls\" a safe hold or "
               "medication advice? The judge currently counts all three as violations; each answer is one line of policy.", ""]

    from evals.voice_summary import summary as voice_summary
    md += voice_section(voice_summary())

    lines = notable(rows_by)
    if lines:
        md += ["## Worth a look", "", *lines, "", "Full transcripts: `evals/results/transcripts/<candidate>/`.", ""]

    md += ["## Cost", "", "| candidate | calls | $ |", "|---|---|---|"]
    total = 0.0
    for c, rows in rows_by.items():
        spent = sum(e["cost_usd"] for _, e, _ in rows)
        total += spent
        md.append(f"| {c} ({cands[c].description}) | {len(rows)} | {spent:.2f} |")
    md += [f"| **total (excluding the judge)** | {sum(len(r) for r in rows_by.values())} | **{total:.2f}** |", "",
           "## Limitations", "",
           "- Simulated patients are not real people; the simulator's own leaks and invented details are measured "
           "and reported, not assumed away.",
           "- Most calls are text. The voice section uses synthetic voices and a simulated phone line, on a "
           "subset of scenarios: no real recordings, accents, background noise of real homes, or interruptions.",
           "- Clinical content follows public patient-education sources but was not reviewed by a clinician.",
           "- The legal mapping is compliance-informed, not legal advice; citations need checking against "
           "primary text.",
           "- No prevalence or outcome data: rates here describe this suite, not a patient population.", ""]
    REPORT.write_text("\n".join(x for x in md if x is not None), encoding="utf-8")
    print(f"report -> {REPORT}\ncharts -> {CHARTS}")


if __name__ == "__main__":
    main()
