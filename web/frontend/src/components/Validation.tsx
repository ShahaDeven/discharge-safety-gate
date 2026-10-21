import { useEffect, useState } from "react";
import { getJSON, go, pct, RULES, shortName, STATUS, type Overview, type ValidationPack, type VoiceEvidence } from "../api";

const NAMES: Record<string, string> = {
  HF: "Heart failure", COPD: "COPD", PNA: "Pneumonia", AMI: "Heart attack", PSY: "Mental health",
  patient: "The patient", caregiver_authorized: "Relative on the contact list", caregiver_unauthorized: "Relative not on the list",
  wrong_person: "Wrong person", voicemail: "Voicemail", explicit: "Says it plainly", implicit: "Only hints at it",
  only_if_asked: "Mentions it only if asked", none: "No red flag", clinical: "Clinical", compliance: "Compliance",
  psych: "Mental health", fairness: "Fairness repeats", Spanish: "Spanish speaker", low_literacy: "Low health literacy",
  hard_of_hearing: "Hard of hearing",
};
const nm = (k: string) => NAMES[k] ?? k.replace(/_/g, " ");
const frac = ([k, n]: number[]) => (n ? `${k}/${n} (${Math.round((k / n) * 100)}%)` : "-");

function Counts({ title, data }: { title: string; data: Record<string, number> }) {
  return (
    <div><b>{title}</b>
      <ul className="plain">{Object.entries(data).map(([k, v]) => <li key={k}>{nm(k)}: {v}</li>)}</ul></div>
  );
}

const pval = (p: number) => (p < 0.001 ? p.toExponential(0) : p.toFixed(2));

/** The same agent heard through speech: single sentences, then full calls (C0's pack only). */
function VoiceSection({ voice }: { voice: VoiceEvidence }) {
  const b = voice.benchmark, c = voice.calls, w = voice.workflows;
  return (
    <>
      <h3>9. Voice: the same agent, hearing the patient through speech</h3>
      <p>The patient's words were spoken by a synthetic voice{b ? ` (${b.speech_model})` : ""}, passed through a simulated phone line, and transcribed{b ? ` (${b.transcription_model})` : ""}; the agent received only the transcript.</p>
      {b && (
        <table>
          <thead><tr><th>{b.lines} critical sentences, {b.voices.length} voices</th><th>Meaning kept</th><th>Lost vs clear</th><th>p</th></tr></thead>
          <tbody>{b.conditions.map((x) => (
            <tr key={x.id}><td>{x.label}</td><td className="num">{frac([x.kept, x.n])}</td><td className="num">{x.lost}</td><td className="num">{pval(x.p)}</td></tr>
          ))}</tbody>
        </table>
      )}
      {b && (
        <table>
          <thead><tr><th>Identity details</th><th>Clean phone line</th><th>Names hinted to the transcriber</th></tr></thead>
          <tbody>{b.names.map((x) => (
            <tr key={x.category}><td>{x.label}</td><td className="num">{x.phone ? frac(x.phone) : "-"}</td><td className="num">{x.phone_keyterms ? frac(x.phone_keyterms) : "-"}</td></tr>
          ))}</tbody>
        </table>
      )}
      {c && (
        <table>
          <thead><tr><th>Full calls ({c.scenarios.length} scenarios × {c.k}; vs {c.baseline})</th>{c.candidates.map((x) => <th key={x.id}>{x.id}</th>)}</tr></thead>
          <tbody>
            {c.candidates[0]?.metrics.map((m, i) => (
              <tr key={m.name}><td>{m.label} <span className="muted">(text {pct(m.base)})</span></td>{c.candidates.map((x) => <td key={x.id} className="num">{pct(x.metrics[i]?.cand)}</td>)}</tr>
            ))}
            <tr><td>Identity check failed on a misheard name</td>{c.candidates.map((x) => <td key={x.id} className="num">{x.identity_failed}/{x.calls}</td>)}</tr>
            <tr><td>Verdict</td>{c.candidates.map((x) => <td key={x.id}><span className={`chip v-${x.verdict}`}>{x.verdict}</span></td>)}</tr>
          </tbody>
        </table>
      )}
      {c && <ul>{c.candidates.map((x) => <li key={x.id}><b>{x.id}</b>: {x.description}.</li>)}</ul>}
      <p>On a clean phone line, uncommon and Spanish names were misheard and the identity check failed, so those calls never reached the warning signs: rejected. Giving the transcriber the names expected on the call, and having the agent spell details back, brought every safety measure back to the text result. Small sample; the noisier line was not re-run with the fix.</p>
      {w && (
        <table>
          <thead><tr><th>Requests (text, {w.k} replays)</th><th>Transferred to a person when asked</th><th>Callback booked when asked</th></tr></thead>
          <tbody>{w.candidates.map((x) => (
            <tr key={x.id}><td>{x.id}: {x.description}</td><td className="num">{frac(x.human)}</td><td className="num">{frac(x.callback)}</td></tr>
          ))}</tbody>
        </table>
      )}
    </>
  );
}

export default function Validation({ d, cand }: { d: Overview; cand?: string }) {
  const options = ["C0", ...d.candidates.map((c) => c.id).filter((id) => id !== "C0" && d.gate[id])];
  const id = cand && options.includes(cand) ? cand : "C0";
  const [v, setV] = useState<ValidationPack | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { setV(null); setErr(""); getJSON<ValidationPack>(`/api/validation/${id}`).then(setV).catch((e) => setErr(String(e))); }, [id]);

  const today = new Date().toISOString().slice(0, 10);

  /** The browser names a saved PDF after the page title: while a pack is open, the title is its file name,
   *  so the button and Ctrl+P both suggest it. The app's usual title comes back when you leave the page. */
  const fileName = v ? `Validation pack ${v.candidate.id} ${shortName(d, v.candidate.id).replace(/[^A-Za-z0-9 ._-]/g, "").trim()}`
    + ` - results ${v.candidate.results_date}${today !== v.candidate.results_date ? ` - printed ${today}` : ""}` : "";
  useEffect(() => {
    if (!fileName) return;
    const previous = document.title;
    document.title = fileName;
    return () => { document.title = previous; };
  }, [fileName]);

  return (
    <section className="pack">
      <div className="controls no-print">
        <label htmlFor="v-cand">Agent version
          <select id="v-cand" value={id} onChange={(e) => go({ page: "validation", cand: e.target.value })}>
            {options.map((o) => <option key={o} value={o}>{o} · {shortName(d, o)}</option>)}
          </select></label>
        <button type="button" className="btn primary" onClick={() => window.print()} disabled={!v}>Print or save as PDF</button>
      </div>
      {err && <p className="err">{err}</p>}
      {!v && !err && <p className="loading">Assembling the pack…</p>}
      {v && (
        <article className="doc">
          <header className="doc-head">
            <span className="eyebrow">Validation pack · results of {v.candidate.results_date}{today !== v.candidate.results_date ? ` · printed ${today}` : ""}</span>
            <h2>{v.candidate.id}: {v.candidate.description}</h2>
            <p className="panel-head"><span className={`chip v-${v.verdict.verdict}`}>{v.verdict.verdict}</span>
              <span className="muted">{v.verdict.baseline ? "current agent, checked against itself (replays 1–2 vs 3–4)" : "against the current agent (C0)"} · gate v{v.verdict.gate_version} · clinical policy {v.policy.name} <span className="mono">[{v.policy.fingerprint}]</span></span></p>
            <p className="muted">Synthetic patients and simulated calls only. Not clinical validation, not legal advice, does not measure readmissions. Assembled from saved results; nothing was re-run.</p>
          </header>

          <h3>1. Intended use</h3>
          <p>{v.intended_use}</p>
          {v.candidate.why && <p className="muted">This version: {v.candidate.why}</p>}

          <h3>2. What it was tested on</h3>
          <p>{v.testing.scenarios} scenarios you can inspect{v.testing.sealed ? `, plus ${v.testing.sealed} sealed scenarios its author never saw` : ""}; {v.candidate.calls} calls{v.candidate.k ? `, each scenario replayed ${v.candidate.k} times` : ""}. Agent model {v.testing.models.agent}; simulated person {v.testing.models["simulated person"]}; judge {v.testing.models.judge}.</p>
          <div className="doc-grid">
            <Counts title="Condition" data={v.testing.conditions} />
            <Counts title="Who answered" data={v.testing.answered_by} />
            <Counts title="How the warning sign comes up" data={v.testing.disclosure} />
            <Counts title="State" data={v.testing.states} />
          </div>

          <h3>3. Results</h3>
          {v.verdict.reasons.length > 0 && <ul>{v.verdict.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
          {v.verdict.sealed && <p>Sealed set ({v.verdict.sealed.scenarios} scenarios): <b>{v.verdict.sealed.verdict}</b></p>}
          <table>
            <thead><tr><th>On its own</th><th>Rate</th><th>95% range</th><th>Calls</th></tr></thead>
            <tbody>{v.performance.absolute.map((a) => (
              <tr key={a.label}><td>{a.label}</td><td className="num">{pct(a.rate)}</td><td className="num">{pct(a.lo)} to {pct(a.hi)}</td><td className="num">{a.k}/{a.n}</td></tr>
            ))}</tbody>
          </table>
          <p className="muted">Each replay counts as a call, so the ranges are slightly narrower than with one call per scenario.</p>
          <table>
            <thead><tr><th>{v.verdict.baseline ? "Replays 1–2 vs 3–4" : "Compared with the current agent"}</th><th>Before</th><th>After</th><th>Change, 95% range</th><th></th></tr></thead>
            <tbody>{v.performance.metrics.map((m) => (
              <tr key={m.name}><td>{m.label} <span className="muted">({m.tier})</span></td><td className="num">{pct(m.base)}</td><td className="num">{pct(m.cand)}</td>
                <td className="num">{(m.diff * 100).toFixed(1)} ({(m.lo * 100).toFixed(1)} to {(m.hi * 100).toFixed(1)})</td>
                <td>{STATUS[m.status]?.[0] ?? m.status}</td></tr>
            ))}</tbody>
          </table>
          {v.performance.rules.length > 0 && (
            <table>
              <thead><tr><th>Rule</th><th>Before</th><th>After</th><th></th></tr></thead>
              <tbody>{v.performance.rules.map((r) => (
                <tr key={r.rule}><td>{RULES[r.rule] ?? r.rule}</td><td className="num">{r.base_calls}/{r.base_n}</td><td className="num">{r.cand_calls}/{r.cand_n}</td><td>{STATUS[r.status]?.[0] ?? r.status}</td></tr>
              ))}</tbody>
            </table>
          )}

          <h3>4. Subgroups</h3>
          {v.subgroups.styles.length > 0 && (
            <table>
              <thead><tr><th>How the person brings up the warning sign</th><th>Escalated correctly</th>{!v.verdict.baseline && <th>Current agent</th>}</tr></thead>
              <tbody>{v.subgroups.styles.map((s) => (
                <tr key={s.group}><td>{nm(s.group)}</td><td className="num">{frac(s.cand)}</td>{!v.verdict.baseline && <td className="num">{frac(s.base)}</td>}</tr>
              ))}</tbody>
            </table>
          )}
          {v.subgroups.repeats.length > 0 ? (
            <table>
              <thead><tr><th>Same case, different person</th><th>Correct and clean</th><th>Original case</th></tr></thead>
              <tbody>{v.subgroups.repeats.map((r) => (
                <tr key={r.id} className={r.variant[0] < r.original[0] ? "changed" : undefined}>
                  <td>{r.id}: {nm(r.persona)} <span className="muted">(of {r.source})</span></td><td className="num">{frac(r.variant)}</td><td className="num">{frac(r.original)}</td></tr>
              ))}</tbody>
            </table>
          ) : <p className="muted">No fairness repeats in this version's scenario set.</p>}

          <h3>5. Rules checked</h3>
          <table>
            <thead><tr><th>Rule</th><th>Where</th><th>Checked by</th><th>Source</th></tr></thead>
            <tbody>{v.rules.map((r) => (
              <tr key={r.rule}><td><b>{RULES[r.rule] ?? r.rule}</b><br /><span className="muted">{r.summary}</span></td><td>{r.where}</td><td>{r.check === "judge" ? "LLM judge" : "code"}</td><td className="muted">{r.source}</td></tr>
            ))}</tbody>
          </table>
          <p>Counted as medical advice under policy "{v.policy.name}": {v.policy_categories.filter((c) => v.policy.scope.includes(c.key)).map((c) => c.label.toLowerCase()).join("; ")}.
            {v.policy.extend.length > 0 && ` State rules applied in every state: ${v.policy.extend.map((e) => e.replace(/_/g, " ")).join(", ")}.`}</p>

          <h3>6. How far to trust the judge</h3>
          <ul>
            {v.judge.planted && <li>Planted lines with a known answer: caught {v.judge.planted.violations_caught[0]} of {v.judge.planted.violations_caught[1]} violations; wrongly flagged {v.judge.planted.allowed_flagged[0]} of {v.judge.planted.allowed_flagged[1]} allowed lines{v.judge.planted.category_right ? `; right category on ${v.judge.planted.category_right[0]} of ${v.judge.planted.category_right[1]}` : ""}.</li>}
            {v.judge.reviewer && <li>Lines a person reviewed one by one: the judge's category matched on {v.judge.reviewer.agree} of {v.judge.reviewer.n}.</li>}
            {v.judge.held_out && <li>Held-out calls a person labeled: agreement {v.judge.held_out.agree} of {v.judge.held_out.n} (Cohen's kappa {v.judge.held_out.kappa.toFixed(2)}; with only {v.judge.held_out.human_yes} violations by the person and {v.judge.held_out.judge_yes} by the judge, kappa is unstable).</li>}
          </ul>

          <h3>7. Security and safety testing</h3>
          {v.security.redteam ? (
            <ul>{v.security.redteam.results.map((r) => <li key={r.attack}>{r.attack.replace(/_/g, " ")}: {r.caught ? `caught${r.caught_by?.length ? ` by ${r.caught_by.join(" and ")}` : ""}` : "MISSED"}{r.detail ? ` (${r.detail})` : ""}</li>)}</ul>
          ) : <p className="muted">Red-team suite not run.</p>}
          <p>Audit log: {v.security.audit.ok ? `hash chain intact over ${v.security.audit.entries} entries` : `BROKEN: ${v.security.audit.message}`}. Access token for actions that spend money; host check; security headers; spending caps; sealed scenarios never served by the API.</p>

          <h3>8. Change history</h3>
          {v.changes.length ? (
            <table>
              <thead><tr><th>Entry</th><th>Compared with</th><th>Verdict</th></tr></thead>
              <tbody>{v.changes.map((e) => <tr key={e.id}><td className="mono">{e.id}</td><td>{e.baseline}</td><td><span className={`chip v-${e.verdict}`}>{e.verdict}</span></td></tr>)}</tbody>
            </table>
          ) : <p className="muted">No ledger entries for this version.</p>}

          {v.voice && <VoiceSection voice={v.voice} />}

          <h3>{v.voice ? 10 : 9}. Limits</h3>
          <ul>{v.limits.map((l) => <li key={l}>{l}</li>)}</ul>
        </article>
      )}
    </section>
  );
}
