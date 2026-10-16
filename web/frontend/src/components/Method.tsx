import { SHORT, pct, type Overview } from "../api";

export default function Method({ d }: { d: Overview }) {
  const j = d.judge_check;
  const aa = d.curve["A/A"] || {};
  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Method</span><h2>How far to trust this</h2></div>
      <div className="trust">
        <div className="card">
          <h3>The medical-advice judge</h3>
          {j && <>
            <div className="stat-line"><span className="big">{j.violations_caught[0]}/{j.violations_caught[1]}</span><span className="muted">planted violations caught</span></div>
            <div className="stat-line"><span className="big">{j.allowed_flagged[0]}/{j.allowed_flagged[1]}</span><span className="muted">allowed but risky-looking lines wrongly flagged</span></div>
          </>}
          <p className="muted">The judge decides one thing only: did the agent give medical advice or reassurance. Everything else is checked by code against the answer key.</p>
          {d.judge_flags && (
            <div className="scroll">
              <table>
                <thead><tr><th>What it flagged in real calls</th><th>Lines</th><th>Reviewer agreed</th></tr></thead>
                <tbody>{d.judge_flags.map((r) => <tr key={r.label}><td>{r.label}</td><td className="num">{r.lines}</td><td className="num">{r.reviewer_yes}/{r.answered}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          <p className="muted">Three of those rows are policy questions for a clinician, not judge errors: is "X can be a sign of Y, call 911" a diagnosis? Is "tiredness is common" reassurance? Is "hold off until the nurse calls" medication advice?</p>
        </div>

        <div className="card">
          <h3>The gate itself</h3>
          <p>Version 1 of the gate rejected the current agent against itself. The agent breaks a rule at random about once in 45 calls, one scenario was flaky, and seven measures were each tested at 95% with no correction.</p>
          <p className="muted">Version 2 compares rule-breaking by rate, ignores scenarios the current agent is already inconsistent on, and corrects for testing seven measures at once. Both changes were made after seeing that control run, and the change log keeps both versions' verdicts.</p>
          <p className="mono">Control false alarms now: {Object.entries(aa).map(([k, v]) => `K=${k}: ${pct(v.false_alarm)}`).join(" · ") || "-"}</p>
          <div className="scroll">
            <table>
              <thead><tr><th>Log</th><th>Change</th><th>Gate</th><th>Verdict</th></tr></thead>
              <tbody>{d.ledger.map((e) => (
                <tr key={e.id}>
                  <td className="num">{e.id}</td><td>{e.candidate} · {SHORT[e.candidate]}</td>
                  <td className="num">v{e.baseline.match(/v(\d)/)?.[1] || "1"}</td>
                  <td className={`st st-${e.verdict === "PROMOTE" ? "ok" : e.verdict === "HOLD" ? "hold" : "bad"}`}>{e.verdict}</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card">
          <h3>What this does not show</h3>
          <ul className="plain">
            <li>Simulated patients are not real speech. The simulator's own slips (volunteering hidden facts, inventing details) are measured and reported.</li>
            <li>Text, not audio: speech-recognition errors such as sound-alike drug names are not tested.</li>
            <li>Clinical content follows public patient-education sources (AHA, American Lung Association, AHRQ RED) and has not been reviewed by a clinician.</li>
            <li>Privacy and disclosure checks are informed by HIPAA, 42 CFR Part 2, TCPA and CA/TX/IL rules, but they are not legal advice.</li>
            <li>It measures what the agent does on a call, not readmissions or outcomes.</li>
          </ul>
        </div>

        <div className="card">
          <h3>What it cost to run</h3>
          <div className="scroll">
            <table>
              <thead><tr><th>Version</th><th>Calls</th><th>Cost</th></tr></thead>
              <tbody>{d.candidates.map((c) => <tr key={c.id}><td>{c.id} · {SHORT[c.id]}</td><td className="num">{c.calls}</td><td className="num">${(d.cost[c.id] || 0).toFixed(2)}</td></tr>)}</tbody>
            </table>
          </div>
          <p className="muted">Agent and simulated patients on Haiku 5.5; C2 on Sonnet 5.5. Repeats are cached, so re-scoring is free.</p>
        </div>
      </div>
    </section>
  );
}
