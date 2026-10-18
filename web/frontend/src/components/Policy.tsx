import { useEffect, useRef, useState } from "react";
import { getJSON, send, shortName, token, type Job, type Overview, type PolicyPage, type PolicyVerdicts } from "../api";

const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

export default function Policy({ d, onChanged }: { d: Overview; onChanged: () => void }) {
  const [page, setPage] = useState<PolicyPage | null>(null);
  const [scope, setScope] = useState<string[]>([]);
  const [extend, setExtend] = useState<string[]>([]);
  const [draft, setDraft] = useState<PolicyVerdicts | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [job, setJob] = useState<(Job & { result: null | { changes: { id: string; before: string; after: string }[] } }) | null>(null);
  const [err, setErr] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const blocked = d.session.read_only ? "Read-only demo: you can try the switches, but not apply a policy."
    : !token() ? "Unlock with the access token (top right) to apply a policy. Trying the switches is free and open." : "";

  function load() {
    getJSON<PolicyPage>("/api/policy").then((p) => {
      setPage(p); setScope(p.active.scope); setExtend(p.active.extend); setDraft(null);
    }).catch((e) => setErr(String(e)));
  }
  useEffect(load, []);

  const dirty = !!page && (!sameSet(scope, page.active.scope) || !sameSet(extend, page.active.extend));
  useEffect(() => {
    if (!page) return;
    window.clearTimeout(timer.current);
    if (!dirty) { setDraft(null); return; }
    timer.current = window.setTimeout(() => {
      setBusy(true);
      send<{ verdicts: PolicyVerdicts }>("/api/policy/preview", "POST", { scope, extend })
        .then((r) => { setDraft(r.verdicts); setErr(""); })
        .catch((e) => setErr(String((e as Error).message || e)))
        .finally(() => setBusy(false));
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [scope.join(), extend.join(), page]);

  useEffect(() => {
    if (!job || job.state !== "running") return;
    const t = setInterval(() => getJSON<typeof job>(`/api/jobs/${job.id}`).then((j) => {
      setJob(j);
      if (j.state !== "running") { load(); onChanged(); }
    }).catch((e) => setErr(String(e))), 1200);
    return () => clearInterval(t);
  }, [job?.id, job?.state]);

  async function apply() {
    setErr("");
    try { setJob(await send("/api/policy", "POST", { name, note, scope, extend })); }
    catch (e) { setErr(String((e as Error).message || e)); }
  }
  const toggle = (list: string[], set: (x: string[]) => void, key: string) =>
    set(list.includes(key) ? list.filter((x) => x !== key) : [...list, key]);

  if (!page) return <p className="loading">{err || "Loading the policy…"}</p>;
  const missing = page.counts.calls - page.counts.judged;
  const rows = Object.keys(page.verdicts);
  const label = (id: string) => (id === "A/A" ? "Current agent vs itself (noise check)" : `${id} · ${shortName(d, id)}`);

  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Clinical policy</span><h2>Your clinicians set the line; the gate applies it</h2>
        <p className="muted">The judge labels what kind of line the agent said; it doesn't decide what counts. These switches do. Changing one re-scores every call from the saved labels, at no cost, and every verdict records which policy it was made under.</p></div>
      {missing > 0 && <p className="warn">{missing} of {page.counts.calls} calls have no category labels yet, so their medical-advice checks are missing. Run <span className="mono">python -m evals.policy rejudge</span> once (about ${(missing * 0.0003).toFixed(2)}).</p>}
      {blocked && <p className="warn">{blocked}</p>}

      <div className="row2">
        <div className="panel">
          <h3>What counts as giving medical advice</h3>
          <div className="switches">
            {page.categories.map((c) => {
              const n = page.counts.categories[c.key];
              return (
                <label key={c.key} className={`switch${c.locked ? " locked" : ""}`} htmlFor={`s-${c.key}`}>
                  <input id={`s-${c.key}`} type="checkbox" disabled={c.locked} checked={c.locked || scope.includes(c.key)}
                    onChange={() => toggle(scope, setScope, c.key)} />
                  <span><b>{c.label}</b>{c.locked && <span className="chip st-bad">always</span>}
                    <span className="muted quote">"{c.example}"</span>
                    <span className="muted">{n ? `${n.lines} lines in ${n.calls} calls` : ""}</span></span>
                </label>
              );
            })}
          </div>
          <h3>Stricter than the law</h3>
          <div className="switches">
            {page.extensions.map((e) => (
              <label key={e.key} className="switch" htmlFor={`x-${e.key}`}>
                <input id={`x-${e.key}`} type="checkbox" checked={extend.includes(e.key)} onChange={() => toggle(extend, setExtend, e.key)} />
                <span><b>{e.label}</b><span className="muted">{e.law}; this applies it in every state</span></span>
              </label>
            ))}
          </div>
          <p className="muted">A policy can be stricter than the law, never looser: where a state rule applies, it applies whatever these say.</p>
        </div>

        <div className="panel">
          <div className="panel-head"><h3>What changes</h3>{busy && <span className="status">recomputing…</span>}</div>
          <table>
            <thead><tr><th>Agent version</th><th>Under "{page.active.name}"</th><th>With these switches</th></tr></thead>
            <tbody>{rows.map((id) => {
              const now = page.verdicts[id].verdict, then = (draft ?? page.verdicts)[id]?.verdict;
              return (
                <tr key={id} className={draft && now !== then ? "changed" : undefined}>
                  <td>{label(id)}</td><td><span className={`chip v-${now}`}>{now}</span></td>
                  <td>{then && <span className={`chip v-${then}`}>{then}</span>}</td>
                </tr>
              );
            })}</tbody>
          </table>
          {draft && rows.every((id) => page.verdicts[id].verdict === draft[id]?.verdict) &&
            <p className="notice">No verdict changes with these switches; the rule counts behind them may.</p>}

          <h3>Apply</h3>
          <div className="row2">
            <label className="field" htmlFor="pol-name">Policy name
              <input id="pol-name" value={name} maxLength={60} placeholder="e.g. Cardiology, Oct 2026" onChange={(e) => setName(e.target.value)} /></label>
            <label className="field" htmlFor="pol-note">Why (optional)
              <input id="pol-note" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
          </div>
          <div className="controls">
            <button type="button" className="btn primary" onClick={apply}
              disabled={!!blocked || name.trim().length < 3 || job?.state === "running"}>Apply to every verdict ($0)</button>
            {dirty && <button type="button" className="btn" onClick={() => { setScope(page.active.scope); setExtend(page.active.extend); }}>Reset</button>}
          </div>
          {job && (
            <div className="card">
              <span className="eyebrow">{job.state === "running" ? `re-gating: ${job.phase}` : job.state}</span>
              {job.error && <p className="err">{job.error}</p>}
              {job.result && (job.result.changes.length
                ? <ul className="plain">{job.result.changes.map((c) => <li key={c.id}>{label(c.id)}: {c.before} → <b>{c.after}</b></li>)}</ul>
                : <p className="muted">Applied. No verdict changed.</p>)}
            </div>
          )}
          {err && <p className="err">{err}</p>}
        </div>
      </div>

      <div className="row2">
        <div className="panel">
          <h3>How far to trust the judge's labels</h3>
          {page.reviewer && page.reviewer.n ? (
            <>
              <div className="stat-line"><span className="big">{page.reviewer.agree}/{page.reviewer.n}</span>
                <span className="muted">reviewed lines where the judge's category matches the reviewer's</span></div>
              <ul className="plain">{page.reviewer.rows.map((r) => <li key={r.category}>{r.label}: {r.judge_agrees}/{r.lines}</li>)}</ul>
            </>
          ) : <p className="muted">Shown once the calls have category labels.</p>}
        </div>
        <div className="panel">
          <h3>Policies applied</h3>
          {page.history.length ? (
            <ul className="plain">{page.history.map((h) => (
              <li key={h.applied_at}><b>{h.name}</b> <span className="mono muted">[{h.fingerprint}]</span> <span className="muted">· {h.applied_at.slice(0, 16).replace("T", " ")}{h.note ? ` · ${h.note}` : ""}</span></li>
            ))}</ul>
          ) : <p className="muted">None yet: everything runs under the default, "{page.active.name}" <span className="mono">[{page.active.fingerprint}]</span>, set from the line-by-line review.</p>}
        </div>
      </div>
    </section>
  );
}
