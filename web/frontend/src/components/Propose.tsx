import { useEffect, useMemo, useState } from "react";
import { getJSON, go, send, token, type Estimate, type Job, type Overview, type Proposal } from "../api";
import JobCard from "./JobCard";

const MIN = 200, MAX = 12000;

/** Line diff (longest common subsequence): what the proposal changes in the current prompt. */
function diff(a: string, b: string): { kind: "same" | "add" | "del"; text: string }[] {
  const x = a.split("\n"), y = b.split("\n");
  const n = x.length, m = y.length;
  const L = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = x[i] === y[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out: { kind: "same" | "add" | "del"; text: string }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (x[i] === y[j]) { out.push({ kind: "same", text: x[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ kind: "del", text: x[i++] });
    else out.push({ kind: "add", text: y[j++] });
  }
  while (i < n) out.push({ kind: "del", text: x[i++] });
  while (j < m) out.push({ kind: "add", text: y[j++] });
  return out;
}

export default function Propose({ d, onChanged }: { d: Overview; onChanged: () => void }) {
  const [base, setBase] = useState("");
  const [prompt, setPrompt] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [model, setModel] = useState("claude-haiku-5-5");
  const [effort, setEffort] = useState("medium");
  const [set, setSet] = useState<"quick" | "full">("quick");
  const [saved, setSaved] = useState<(Proposal & { estimate: Estimate }) | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState<Proposal[]>([]);

  const blocked = d.session.read_only ? "This is a read-only demo: proposals are turned off." : !token() ? "Unlock with the access token (top right) to propose a change." : "";
  const lines = useMemo(() => diff(base, prompt), [base, prompt]);
  const changed = lines.filter((l) => l.kind !== "same").length;

  useEffect(() => {
    getJSON<{ prompt: string }>("/api/prompts/C0").then((r) => { setBase(r.prompt); setPrompt(r.prompt); }).catch((e) => setErr(String(e)));
    refreshHistory();
  }, []);
  function refreshHistory() { getJSON<Proposal[]>("/api/proposals").then(setHistory).catch(() => undefined); }

  async function save() {
    setErr(""); setSaved(null); setJob(null);
    try {
      setSaved(await send<Proposal & { estimate: Estimate }>("/api/proposals", "POST",
        { name, note, prompt, model, effort, scenario_set: set }));
      refreshHistory();
    } catch (e) { setErr(String((e as Error).message || e)); }
  }
  async function run() {
    if (!saved) return;
    setErr("");
    try { setJob(await send<Job>(`/api/proposals/${saved.id}/run`, "POST")); }
    catch (e) { setErr(String((e as Error).message || e)); }
  }

  const tooShort = prompt.length < MIN, tooLong = prompt.length > MAX;

  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Propose a change</span><h2>Change the agent, and let the gate decide</h2>
        <p className="muted">Edit the agent's instructions (or its model or effort), save the change, and run it through the same checks as everything else. Besides the scenarios you can browse, a sealed set you never see votes on every verdict, so a change tuned to the visible tests gets caught.</p></div>
      {blocked && <p className="warn">{blocked}</p>}

      <div className="panel">
        <div className="row4">
          <label className="field" htmlFor="p-name">Name of the change
            <input id="p-name" value={name} maxLength={60} placeholder="e.g. Personalised greeting" onChange={(e) => setName(e.target.value)} /></label>
          <label className="field" htmlFor="p-model">Model
            <select id="p-model" value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="claude-haiku-5-5">Haiku 5.5 (about $0.003 a call)</option>
              <option value="claude-sonnet-5-5">Sonnet 5.5 (about $0.024 a call)</option>
            </select></label>
          <label className="field" htmlFor="p-effort">Effort
            <select id="p-effort" value={effort} onChange={(e) => setEffort(e.target.value)}>
              {["low", "medium", "high"].map((x) => <option key={x}>{x}</option>)}
            </select></label>
          <label className="field" htmlFor="p-set">Scenarios
            <select id="p-set" value={set} onChange={(e) => setSet(e.target.value as "quick" | "full")}>
              <option value="quick">Quick: 30 visible + 10 sealed, 2 replays each</option>
              <option value="full">Full: 102 visible + 20 sealed, 3 replays each</option>
            </select></label>
        </div>
        <label className="field" htmlFor="p-note">Why (optional)
          <input id="p-note" value={note} maxLength={500} placeholder="What is this change meant to improve?" onChange={(e) => setNote(e.target.value)} /></label>
        <div className="row2">
          <label className="field" htmlFor="p-prompt">Agent instructions ({prompt.length.toLocaleString()} of {MAX.toLocaleString()} characters)
            <textarea id="p-prompt" value={prompt} spellCheck={false} onChange={(e) => setPrompt(e.target.value)} /></label>
          <div className="field"><span>What changes ({changed} {changed === 1 ? "line" : "lines"})</span>
            <div className="diff" aria-label="Difference from the current agent">
              {changed === 0 ? <div className="same">No changes yet. Edit the instructions on the left.</div>
                : lines.map((l, i) => <div key={i} className={l.kind}>{l.kind === "add" ? "+ " : l.kind === "del" ? "- " : "  "}{l.text || " "}</div>)}
            </div>
          </div>
        </div>
        <div className="controls">
          <button type="button" className="btn" onClick={() => setPrompt(base)} disabled={!changed}>Start over</button>
          <button type="button" className="btn primary" onClick={save}
            disabled={!!blocked || !changed || tooShort || tooLong || name.trim().length < 3 || job?.state === "running"}>
            Save and see the cost</button>
          {saved && !job && (
            <button type="button" className="btn primary" onClick={run}>
              Run the gate on {saved.id} (about ${saved.estimate.usd.toFixed(2)})</button>
          )}
        </div>
        {saved && !job && saved.estimate.blocked.length > 0 && (
          <div className="notice">{saved.id} saved, but the prompt check flagged lines that try to work on the evaluator,
            so running it rejects it without placing any calls ($0):
            <ul className="plain">{saved.estimate.blocked.map((f) => <li key={f}>{f}</li>)}</ul></div>
        )}
        {saved && !job && !saved.estimate.blocked.length && (
          <p className="notice">{saved.id} saved. Running it places {saved.estimate.calls} calls
            {saved.estimate.baseline_calls ? ` plus ${saved.estimate.baseline_calls} for the current agent on the sealed set (once, then cached)` : ""},
            about ${saved.estimate.usd.toFixed(2)}, within your caps (${(d.session.budget.day_max - d.session.budget.today).toFixed(2)} left today).</p>
        )}
        {err && <p className="err">{err}</p>}
        {job && <JobCard job={job} onUpdate={setJob} onFinished={() => { refreshHistory(); onChanged(); }} onError={setErr} />}
      </div>

      {history.length > 0 && (
        <>
          <h3>Earlier proposals</h3>
          <div className="plist">
            {history.map((p) => (
              <div key={p.id} className="pitem">
                <span className="mono">{p.id}</span>
                <span>{p.name} <span className="muted">· {p.model.replace("claude-", "")}, {p.effort} effort · {p.created_at.slice(0, 16).replace("T", " ")}</span></span>
                {p.result?.blocked ? <span className={`chip v-${p.result.verdict}`} title="Stopped by the prompt check: no calls placed">{p.result.verdict}</span>
                  : p.result ? <button type="button" className={`chip v-${p.result.verdict}`} style={{ border: 0 }} onClick={() => go({ page: "changes", cand: p.id })}>{p.result.verdict}</button>
                  : <span className="status">{p.status}</span>}
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
