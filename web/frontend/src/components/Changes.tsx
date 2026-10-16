import { CONTROL, RULES, SHORT, STATUS, go, pct, type Overview } from "../api";
import { order, plainWhy } from "./Overview";

export default function Changes({ d, id }: { d: Overview; id?: string }) {
  const ids = order(d);
  const cur = id && d.gate[id] ? id : ids.find((x) => x === "C3") || ids[0];
  const g = d.gate[cur];
  const cand = Object.fromEntries(d.candidates.map((c) => [c.id, c]));
  const bad = new Map<string, string>();
  for (const [sid, rules] of Object.entries(g.new_violations || {})) bad.set(sid, rules.map((r) => RULES[r] || r).join(", "));
  for (const m of g.metrics) for (const sid of m.new_misses || []) if (!bad.has(sid)) bad.set(sid, "missed a red flag the current agent always catches");
  const k = cur === CONTROL ? "2 vs 2 replays" : `current agent x${cand.C0.k} vs change x${cand[cur].k} replays`;

  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Changes</span><h2>Why the gate decided what it did</h2></div>
      <div className="tabs" role="tablist">
        {ids.map((x) => (
          <button key={x} type="button" role="tab" className="tab" aria-selected={x === cur} onClick={() => go({ page: "changes", cand: x })}>
            {x === CONTROL ? "Control" : `${x} · ${SHORT[x]}`}
          </button>
        ))}
      </div>
      <div className="panel">
        <div className="panel-head">
          <h3>{cur === CONTROL ? "Current agent vs itself" : `${cur}: ${cand[cur].description}`}</h3>
          <span className={`chip v-${g.verdict}`}>{g.verdict}</span>
        </div>
        <p className="muted">{plainWhy(cur, g)} Compared on {g.scenarios} scenarios, {k}.</p>

        <div className="scroll">
          <table>
            <thead><tr><th>What was measured</th><th>Kind</th><th>Current</th><th>{cur === CONTROL ? "Again" : "Change"}</th><th>Difference (CI*)</th><th>Result</th></tr></thead>
            <tbody>
              {g.metrics.map((m) => {
                const [lab, cls] = STATUS[m.status] || [m.status, "st-same"];
                return (
                  <tr key={m.name}>
                    <td>{m.label}</td><td className="tier">{m.tier}</td><td className="num">{pct(m.base)}</td><td className="num">{pct(m.cand)}</td>
                    <td className="num">{(m.diff * 100).toFixed(1)} pts ({(m.lo * 100).toFixed(1)} to {(m.hi * 100).toFixed(1)})</td>
                    <td className={`st ${cls}`}>{lab}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {g.rules.length > 0 && (
          <div className="scroll">
            <table>
              <thead><tr><th>Rule</th><th>Current agent</th><th>{cur === CONTROL ? "Again" : "Change"}</th><th>Result</th></tr></thead>
              <tbody>
                {g.rules.map((r) => {
                  const [lab, cls] = STATUS[r.status] || [r.status, "st-same"];
                  return (
                    <tr key={r.rule}>
                      <td>{RULES[r.rule] || r.rule}</td><td className="num">{r.base_calls}/{r.base_n} calls</td>
                      <td className="num">{r.cand_calls}/{r.cand_n} calls</td><td className={`st ${cls}`}>{lab}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {bad.size > 0 && cur !== CONTROL && (
          <>
            <h3>Where it went wrong ({bad.size} scenarios)</h3>
            <div className="wrong">
              {[...bad].map(([sid, why]) => (
                <button key={sid} type="button" className="sid" title={why} onClick={() => go({ page: "calls", cand: cur, sid, compare: true })}>{sid}</button>
              ))}
            </div>
          </>
        )}

        <details>
          <summary>The gate's exact reasoning</summary>
          <ul>{(g.reasons.length ? g.reasons : ["No reason to block: nothing moved beyond the noise floor."]).map((r) => <li key={r}>{r}</li>)}</ul>
          <p className="muted" style={{ marginTop: 8 }}>* Confidence intervals are corrected for testing several measures at once (Bonferroni).</p>
        </details>
      </div>
    </section>
  );
}
