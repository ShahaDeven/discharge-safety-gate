import { CONTROL, RULES, SHORT, go, pct, type Gate, type Metric, type Overview as Data, type Rule } from "../api";

type Bar = { label: string; base: number | null; cand: number | null; worse: boolean };
const byRate = (a: Rule, b: Rule) => b.cand_calls / b.cand_n - a.cand_calls / a.cand_n;
const ruleBar = (r: Rule): Bar => ({ label: RULES[r.rule] || r.rule, base: r.base_calls / r.base_n, cand: r.cand_calls / r.cand_n, worse: true });
const metricBar = (m: Metric): Bar => ({ label: m.label, base: m.base, cand: m.cand, worse: m.status === "worse" || m.status === "new_miss" });

/** The change's biggest problem: a new kind of violation, then a safety drop, then a rule broken more often. */
export function headline(g: Gate): Bar[] {
  const kinds = g.rules.filter((r) => r.status === "new_kind").sort(byRate);
  if (kinds.length) return kinds.slice(0, 2).map(ruleBar);
  const drops = g.metrics.filter((m) => m.tier === "safety" && (m.status === "worse" || m.status === "new_miss"));
  if (drops.length) return drops.slice(0, 2).map(metricBar);
  const more = g.rules.filter((r) => r.status === "more_often").sort(byRate);
  if (more.length) return more.slice(0, 2).map(ruleBar);
  return g.metrics.filter((m) => ["red_flag_recall", "emergency_recall"].includes(m.name)).map(metricBar);
}

export function plainWhy(id: string, g: Gate): string {
  if (id === CONTROL) return g.verdict === "PROMOTE" ? "Passes against itself: run-to-run wobble alone doesn't trip the gate." : "The gate reacts to noise.";
  const kind = g.rules.filter((r) => r.status === "new_kind").sort(byRate)[0];
  if (kind) return `Broke a rule the current agent never breaks: ${(RULES[kind.rule] || kind.rule).toLowerCase()}, on ${kind.cand_calls} of ${kind.cand_n} calls.`;
  const m = g.metrics.find((x) => x.tier === "safety" && (x.status === "worse" || x.status === "new_miss"));
  if (m) return `Caught fewer red flags: ${m.label.toLowerCase()} fell from ${pct(m.base)} to ${pct(m.cand)}, with new misses on scenarios the current agent always handles.`;
  if (g.verdict === "HOLD") return "Something dipped, but not enough to tell from noise yet. Needs more replays.";
  return "Nothing got worse beyond normal run-to-run variation.";
}

export function order(d: Data): string[] {
  return [CONTROL, ...d.candidates.map((c) => c.id).filter((c) => c !== "C0")].filter((id) => d.gate[id]);
}

export default function Overview({ d }: { d: Data }) {
  const calls = d.candidates.reduce((a, c) => a + c.calls, 0);
  const cost = Object.values(d.cost).reduce((a, b) => a + b, 0);
  const cand = Object.fromEntries(d.candidates.map((c) => [c.id, c]));
  return (
    <>
      <section className="hero">
        <span className="eyebrow">Release gate for a post-discharge call agent</span>
        <h1>Every change to a patient-calling agent, tested before it ships</h1>
        <p>Each version of the agent calls the same 102 synthetic patients, several times. The gate compares it with the current agent, call by call, and decides: <b>promote</b>, <b>hold</b> for more evidence, or <b>reject</b>.</p>
        <div className="facts">
          <span><b>{calls.toLocaleString()}</b> calls</span><span><b>{Object.keys(d.scenarios).length}</b> scenarios</span>
          <span><b>{d.candidates.length}</b> agent versions</span><span><b>${cost.toFixed(2)}</b> of API time</span>
        </div>
        <span className="synthetic">Synthetic patients and a reference agent only. No real patient data, no company's real agent.</span>
      </section>

      <section>
        <div className="sect-head"><span className="eyebrow">How it works</span><h2>One call, start to finish</h2></div>
        <div className="steps">
          <div className="step"><b>A scenario with a hidden answer key</b><span>A patient, a hidden warning sign and how readily they mention it, a planted medicine mistake, and who picks up the phone.</span></div>
          <div className="step"><b>A simulated patient talks to the agent</b><span>The agent runs the whole call: checks identity, asks about symptoms, reviews medicines, escalates.</span></div>
          <div className="step"><b>The call is scored against the key</b><span>Code checks escalation, medicines and privacy rules. A model judge checks only medical advice.</span></div>
          <div className="step"><b>The gate compares versions</b><span>Same scenarios, several replays each. Rules broken by the change, or real drops, block it.</span></div>
        </div>
        <div><button type="button" className="btn primary" onClick={() => go({ page: "live", cand: "C3", sid: "CMP-05" })}>Watch a call happen</button></div>
      </section>

      <section>
        <div className="sect-head"><span className="eyebrow">Verdicts</span><h2>Four changes, one control</h2>
          <p className="muted">The control replays the current agent against itself. It has to pass, and how much it wobbles sets the bar every real change must clear.</p></div>
        <div className="board">
          {order(d).map((id) => {
            const g = d.gate[id];
            return (
              <div key={id} className={`vrow${id === CONTROL ? " control" : ""}`}>
                <div className="vname">
                  <b>{id === CONTROL ? "Current agent vs itself" : `${id} · ${SHORT[id]}`}</b>
                  <span className="muted">{id === CONTROL ? "Control: replays 1-2 against 3-4" : cand[id].description}</span>
                </div>
                <span className={`chip v-${g.verdict}`}>{g.verdict}</span>
                <p className="why">{plainWhy(id, g)}</p>
                <div className="bars">
                  {headline(g).map((h) => (
                    <div key={h.label} style={{ display: "grid", gap: 4 }}>
                      <span className="lab">{h.label}</span>
                      {[["current", h.base, ""], [id === CONTROL ? "again" : "change", h.cand, h.worse ? "cand worse" : "cand"]].map(([lab, v, c]) => (
                        <div className="barrow" key={String(lab)}>
                          <span>{lab}</span>
                          <div className="track"><div className={`fill ${c}`} style={{ width: `${Math.max(0, Math.min(1, Number(v) || 0)) * 100}%` }} /></div>
                          <span className="val">{pct(v as number)}</span>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
                <button type="button" className="btn" onClick={() => go({ page: "changes", cand: id })}>Details</button>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <div className="sect-head"><span className="eyebrow">Findings</span><h2>What the calls showed</h2></div>
        <div className="findings">
          {d.findings.map((f) => (
            <article key={f.cand + f.sid} className="finding">
              <span className="tag">{f.cand} · {SHORT[f.cand]} · {f.sid}</span>
              <h3>{f.title}</h3>
              <p>{f.body}</p>
              <div className="actions">
                <button type="button" className="btn primary" onClick={() => go({ page: "calls", cand: f.cand, sid: f.sid, r: f.r, compare: f.compare })}>
                  {f.compare ? "Compare the calls" : "Read the call"}
                </button>
                <button type="button" className="btn" onClick={() => go({ page: "live", cand: f.cand, sid: f.sid, r: f.r })}>Watch it play</button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
