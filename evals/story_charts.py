"""Pictures for README.md (the short version): the story in six images, drawn from saved results. No API calls.

    python -m evals.story_charts            # writes evals/results/charts/story_*.png

Same look as the report (evals/report.py): reference palette, light chart surface, recessive grid, thin marks.
Status colors (good / warning / critical) only ever appear next to their word, never alone.
"""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from discharge import config  # noqa: E402
from evals.report import CHARTS, GRID, INK, INK2, SURFACE  # noqa: E402
from evals.voice_summary import summary as voice_summary  # noqa: E402

BLUE, CONTEXT = "#2a78d6", "#b4b3ad"                       # the one series; "before" as quiet context
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"         # status: always paired with a word
VERDICT = {"PROMOTE": ("Safe", GOOD), "REJECT": ("Not safe", CRIT), "HOLD": ("Needs checking", WARN)}


def _fig(w: float, h: float):
    fig = plt.figure(figsize=(w, h), dpi=150)
    fig.set_facecolor(SURFACE)
    return fig


def _axes(ax, title: str, sub: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=24 if sub else 12, fontweight="bold")
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, color=INK2, fontsize=9.5, va="bottom")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9.5, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def _verdict(ax, x: float, y: float, word: str, color: str, transform=None, ha: str = "center") -> None:
    """A status dot with its word: the color never carries the meaning alone."""
    t = transform or ax.transData
    ax.annotate(word, (x, y), xycoords=t, ha=ha, va="center", fontsize=9.5, color=INK, fontweight="bold",
                xytext=(7 if ha == "left" else 0, 0), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.35", fc=SURFACE, ec=color, lw=1.6))


def _save(fig, name: str) -> str:
    path = CHARTS / name
    fig.savefig(path, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return path.name


# ---------------------------------------------------------------------------------------------- 1. how it works

def how_it_works() -> str:
    fig = _fig(10, 2.5)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 26)
    ax.axis("off")
    steps = [("1  Practice patients", "102 made-up patients,\neach with a hidden\nwarning sign"),
             ("2  Practice calls", "The AI calls each one\nseveral times, in text\nor with ElevenLabs voices"),
             ("3  Grading", "Warning sign caught?\nPrivacy kept?\nNo medical advice?"),
             ("4  Verdict", "")]
    w, gap, y0, h = 21, 4.6, 1, 23
    for i, (title, body) in enumerate(steps):
        x = 1 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, y0), w, h, boxstyle="round,pad=0,rounding_size=1.6",
                                    fc="#ffffff", ec=GRID, lw=1.4))
        ax.text(x + 1.6, y0 + h - 3.2, title, fontsize=11, fontweight="bold", color=INK, va="top")
        if body:
            ax.text(x + 1.6, y0 + h - 8.4, body, fontsize=9.5, color=INK2, va="top", linespacing=1.5)
        if i < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + w + 0.6, y0 + h / 2), (x + w + gap - 0.6, y0 + h / 2),
                                         arrowstyle="-|>", mutation_scale=14, color=INK2, lw=1.4))
    x = 1 + 3 * (w + gap)
    for j, (word, color) in enumerate([("Safe", GOOD), ("Needs checking", WARN), ("Not safe", CRIT),
                                       ("Not finished", INK2)]):
        _verdict(ax, x + 1.6, y0 + h - 8.0 - j * 3.9, word, color, ha="left")
    return _save(fig, "story_how_it_works.png")


# ---------------------------------------------------------------------------------------------- 2. what it found

def what_it_found() -> str:
    gate = json.loads((config.RESULTS_DIR / "gate.json").read_text(encoding="utf-8"))
    metric = lambda c, n: next(m for m in gate[c]["metrics"] if m["name"] == n)
    rule = lambda c, n: next(r for r in gate[c]["rules"] if r["rule"] == n)
    phi, scope = rule("C3", "PHI_BEFORE_VERIFY"), rule("C2", "SCOPE")
    c4 = metric("C4", "red_flag_recall")
    rows = [  # change, what was measured, before, after, verdict word, color
        ("Keep calls short", "Hidden warning signs caught (patient mentions it only if asked)", 63 / 64, 9 / 48,
         "Not safe", CRIT),
        ("Friendlier greeting", "Calls that revealed the hospital stay before checking who answered",
         phi["base_calls"] / phi["base_n"], phi["cand_calls"] / phi["cand_n"], "Not safe", CRIT),
        ("Let the AI think harder", "Warning signs acted on", c4["base"], c4["cand"], "Safe", GOOD),
        ("Bigger, newer model", "Calls with a line that may count as medical advice",
         scope["base_calls"] / scope["base_n"], scope["cand_calls"] / scope["cand_n"], "Clinicians decide", WARN),
    ]
    fig = _fig(10, 4.6)
    ax = fig.add_axes([0.04, 0.08, 0.74, 0.74])
    _axes(ax, "Four changes a team might make, and what the gate saw",
          "Gray dot: the AI in use today.  Blue dot: after the change.  Same calls, same patients.")
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    n = len(rows)
    for i, (name, what, before, after, word, color) in enumerate(rows):
        y = n - 1 - i
        ax.plot([before, after], [y, y], color=GRID, lw=3, solid_capstyle="round", zorder=1)
        ax.scatter([before], [y], s=90, color=CONTEXT, edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.scatter([after], [y], s=90, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=3)
        # Values sit beside their dot, on the outside of the pair, so they never meet the row labels or each other.
        if abs(after - before) < 0.06:
            ax.annotate(f"{before:.0%} -> {after:.0%}", (min(before, after), y), xytext=(-12, 0),
                        textcoords="offset points", ha="right", va="center", fontsize=9, color=INK2)
        else:
            lo, hi = sorted((before, after))
            ax.annotate(f"{lo:.0%}", (lo, y), xytext=(0, -15), textcoords="offset points", ha="center",
                        va="center", fontsize=9, color=INK2)
            ax.annotate(f"{hi:.0%}", (hi, y), xytext=(0, -15), textcoords="offset points", ha="center",
                        va="center", fontsize=9, color=INK2)
        ax.text(-0.05, y + 0.17, name, transform=ax.get_yaxis_transform(), ha="right", fontsize=10.5,
                color=INK, fontweight="bold")
        ax.text(-0.05, y - 0.2, what, transform=ax.get_yaxis_transform(), ha="right", fontsize=8.5, color=INK2)
        _verdict(ax, 1.04, y, word, color, transform=ax.get_yaxis_transform(), ha="left")
    ax.set_yticks([])
    ax.set_ylim(-0.6, n - 0.4)
    ax.set_xlim(-0.03, 1.03)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    fig.subplots_adjust(left=0.4)
    ax.set_position([0.42, 0.1, 0.42, 0.72])
    return _save(fig, "story_what_it_found.png")


# ---------------------------------------------------------------------------------------------- 3. the voice story

def voice_story(v: dict) -> str | None:
    calls = {c["id"]: c for c in (v.get("calls") or {}).get("candidates", [])}
    if not {"VC", "VK"} <= calls.keys():
        return None
    rf = lambda c: next(m for m in c["metrics"] if m["name"] == "red_flag_recall")
    stages = ["Typed text", "ElevenLabs voice,\nclean phone line", "ElevenLabs voice\n+ the name fix"]
    acted = [rf(calls["VC"])["base"], rf(calls["VC"])["cand"], rf(calls["VK"])["cand"]]
    failed = [0.0, calls["VC"]["identity_failed"] / calls["VC"]["calls"], calls["VK"]["identity_failed"] / calls["VK"]["calls"]]
    failed_lbl = ["0 of 48", f"{calls['VC']['identity_failed']} of {calls['VC']['calls']}",
                  f"{calls['VK']['identity_failed']} of {calls['VK']['calls']}"]
    verdicts = [None, VERDICT[calls["VC"]["verdict"]], VERDICT[calls["VK"]["verdict"]]]
    fig = _fig(10, 5.0)
    fig.suptitle("Same AI, same patients. Only how the AI hears them changes.", x=0.06, ha="left", y=1.0,
                 fontsize=12, fontweight="bold", color=INK)
    fig.text(0.06, 0.935, "ElevenLabs speaks each patient line (text to speech), a simulated phone line carries it,\n"
             "and ElevenLabs speech recognition turns it back into the text the AI hears.",
             ha="left", va="top", fontsize=9.5, color=INK2, linespacing=1.4)
    panels = [("Warning signs acted on", acted, [f"{a:.0%}" for a in acted]),
              ("Calls where a misheard name\nfailed the identity check", failed, failed_lbl)]
    for k, (title, ys, labels) in enumerate(panels):
        ax = fig.add_axes([0.06 + k * 0.49, 0.2, 0.42, 0.52])
        _axes(ax, title)
        ax.bar(range(3), ys, width=0.42, color=BLUE, edgecolor=SURFACE, linewidth=2)
        for x, (y, lbl) in enumerate(zip(ys, labels)):
            ax.annotate(lbl, (x, y), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=10,
                        color=INK, fontweight="bold")
        ax.set_xticks(range(3), stages)
        ax.set_ylim(0, 1.12)
        ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
        for x, vd in enumerate(verdicts):
            if vd:
                _verdict(ax, x, -0.3, vd[0], vd[1], transform=ax.get_xaxis_transform())
            else:
                ax.text(x, -0.3, "the baseline", transform=ax.get_xaxis_transform(), ha="center", va="center",
                        fontsize=9, color=INK2)
    return _save(fig, "story_voice.png")


def names(v: dict) -> str | None:
    b = v.get("benchmark")
    row = next((r for r in (b or {}).get("names", []) if r["category"] == "name"), None)
    if not row or "phone_keyterms" not in row:
        return None
    before, after = row["phone"][0] / row["phone"][1], row["phone_keyterms"][0] / row["phone_keyterms"][1]
    fig = _fig(10, 2.9)
    ax = fig.add_axes([0.25, 0.2, 0.4, 0.55])
    _axes(ax, "Names heard correctly by ElevenLabs speech recognition")
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.barh([1, 0], [before, after], height=0.42, color=[CONTEXT, BLUE], edgecolor=SURFACE, linewidth=2)
    for y, val, n in ((1, before, row["phone"]), (0, after, row["phone_keyterms"])):
        ax.annotate(f"{val:.0%}  ({n[0]} of {n[1]})", (val, y), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=10, color=INK, fontweight="bold")
    ax.set_yticks([1, 0], ["ElevenLabs Scribe\non its own", "Scribe told the names\nto expect (keyterms)"])
    ax.tick_params(axis="y", labelsize=10, labelcolor=INK)
    ax.set_xlim(0, 1.3)
    ax.set_xticks([0, 0.5, 1])
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.spines["bottom"].set_visible(False)
    fig.text(0.72, 0.72, "What it heard on its own", fontsize=10, fontweight="bold", color=INK, va="top")
    fig.text(0.72, 0.6, "Micaela  ->  Michaela\nKihn  ->  Keane\nBebe  ->  BB\nJesica  ->  Jessica",
             fontsize=10, color=INK2, va="top", linespacing=1.6, family="monospace")
    decoy = next((r for r in b["names"] if r["category"] == "name_decoy"), None)
    if decoy and "phone_keyterms" in decoy:
        fig.text(0.25, 0.02, f"On a simulated phone line. A stranger with a similar name was never heard as the patient "
                 f"({decoy['phone_keyterms'][0]} of {decoy['phone_keyterms'][1]}).", fontsize=9, color=INK2)
    return _save(fig, "story_names.png")


# ---------------------------------------------------------------------------------------------- 4. everyday requests

def requests(v: dict) -> str | None:
    w = {c["id"]: c for c in (v.get("workflows") or {}).get("candidates", [])}
    if not {"WC0", "W1"} <= w.keys():
        return None
    groups = [("Transferred to a person\nwhen asked", "human"), ("Callback booked\nwhen asked", "callback")]
    fig = _fig(10, 3.6)
    ax = fig.add_axes([0.08, 0.16, 0.6, 0.62])
    _axes(ax, "Two everyday requests, before and after")
    width = 0.3
    for k, (cid, label, color) in enumerate((("WC0", "Before", CONTEXT), ("W1", "After", BLUE))):
        xs = [i + (k - 0.5) * width for i in range(len(groups))]
        ys = [w[cid][key][0] / w[cid][key][1] for _, key in groups]
        ax.bar(xs, ys, width=width, color=color, edgecolor=SURFACE, linewidth=2, label=label)
        for x, y, (_, key) in zip(xs, ys, groups):
            ax.annotate(f"{w[cid][key][0]} of {w[cid][key][1]}", (x, y), xytext=(0, 5), textcoords="offset points",
                        ha="center", fontsize=9.5, color=INK)
    ax.set_xticks(range(len(groups)), [g for g, _ in groups])
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(frameon=False, ncol=2, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=9.5, labelcolor=INK2)
    ax.set_title("Two everyday requests, before and after", loc="left", color=INK, fontsize=12, pad=26,
                 fontweight="bold")
    fig.text(0.72, 0.7, "After: a short hold tune\nwhile it connects, and\ncallbacks only inside\ncalling hours.\n\n"
             "The one missed callback\nwas a request for 11 pm.", fontsize=9.5, color=INK2, va="top", linespacing=1.5)
    return _save(fig, "story_requests.png")


# ---------------------------------------------------------------------------------------------- 5. ElevenLabs agent

def elevenlabs_journey() -> str | None:
    """Four versions of an ElevenLabs workflow agent through the gate: what it found each time, and the verdict."""
    path = config.RESULTS_DIR / "elevenagents.json"
    cands = json.loads(path.read_text(encoding="utf-8")).get("candidates", {}) if path.exists() else {}
    steps = [("EA-R", "A branch with the\nidentity check late", "Health details before\nchecking who answered\n(4 of 8 calls)"),
             ("EA", "Version 1", "Confusion sent to a nurse,\nnot 911; skipped the\nmedicine review once"),
             ("EA2", "Version 2: those fixed", "Crisis handoff in silence\n(no 988); voicemail without\n\"I'm an AI\""),
             ("EA3", "Version 3: those fixed", "All 18 scenarios as safe\nas the current agent")]
    if not all(c in cands for c, _, _ in steps):
        return None
    fig = _fig(10, 2.9)
    ax = fig.add_axes([0, 0, 1, 0.8])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 26)
    ax.axis("off")
    fig.text(0.01, 0.99, "An agent built on ElevenLabs, through the same gate", fontsize=12, fontweight="bold",
             color=INK, va="top")
    fig.text(0.01, 0.905, "Built with ElevenLabs Agents (workflow builder), run over its live connection. ElevenLabs' "
             "own agent tests also caught the broken branch; only the gate found the missed emergency.",
             fontsize=9, color=INK2, va="top")
    w, gap, y0, h = 21, 4.6, 1, 23
    for i, (cid, title, found) in enumerate(steps):
        x = 1 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, y0), w, h, boxstyle="round,pad=0,rounding_size=1.6",
                                    fc="#ffffff", ec=GRID, lw=1.4))
        ax.text(x + 1.6, y0 + h - 2.6, title, fontsize=10.5, fontweight="bold", color=INK, va="top", linespacing=1.3)
        word, color = VERDICT[cands[cid]["verdict"]]
        _verdict(ax, x + 1.6, y0 + h - 10.2, word, color, ha="left")
        ax.text(x + 1.6, y0 + h - 13.2, found, fontsize=9, color=INK2, va="top", linespacing=1.45)
        if i < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + w + 0.6, y0 + h / 2), (x + w + gap - 0.6, y0 + h / 2),
                                         arrowstyle="-|>", mutation_scale=14, color=INK2, lw=1.4))
    return _save(fig, "story_elevenlabs.png")


def main() -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    v = voice_summary() or {}
    for name in (how_it_works(), what_it_found(), voice_story(v), names(v), requests(v), elevenlabs_journey()):
        if name:
            print(f"chart -> {CHARTS / name}")


if __name__ == "__main__":
    main()
