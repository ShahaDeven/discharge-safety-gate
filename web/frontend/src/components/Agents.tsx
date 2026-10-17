import { useEffect, useState } from "react";
import { getJSON, go, send, token, type Agent, type AgentCheck, type Job, type Overview } from "../api";
import JobCard from "./JobCard";

const CONTRACT = `POST <your URL>   (JSON, signed: X-DSG-Timestamp, X-DSG-Nonce, X-DSG-Signature)

{"type": "ping"}                                       -> {"ok": true}
{"type": "start", "call_id", "context", "tools"}       -> {"ok": true}
{"type": "hear", "call_id", "utterance"}               -> reply
{"type": "tool_results", "call_id", "results": [...]}  -> reply

reply = {"steps": [{"type": "speech", "text": "..."},
                   {"type": "tool_call", "id": "t1", "name": "verify_identity", "input": {...}}],
         "error": null}`;

export default function Agents({ d, onChanged }: { d: Overview; onChanged: () => void }) {
  const [list, setList] = useState<Agent[]>([]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [version, setVersion] = useState("1");
  const [note, setNote] = useState("");
  const [local, setLocal] = useState(false);
  const [set, setSet] = useState<"quick" | "full">("quick");
  const [sel, setSel] = useState<Agent | null>(null);
  const [secret, setSecret] = useState<{ id: string; value: string } | null>(null);
  const [check, setCheck] = useState<AgentCheck | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState("");

  const blocked = d.session.read_only ? "This is a read-only demo: outside agents are turned off."
    : !token() ? "Unlock with the access token (top right) to register or run an agent." : "";

  useEffect(() => { refresh(); }, []);
  function refresh() { getJSON<Agent[]>("/api/agents").then(setList).catch(() => undefined); }
  async function open(id: string) {
    setErr(""); setCheck(null); setJob(null);
    try { setSel(await getJSON<Agent>(`/api/agents/${id}`)); } catch (e) { setErr(String((e as Error).message || e)); }
  }

  async function register() {
    setErr(""); setSecret(null); setCheck(null); setJob(null);
    try {
      const a = await send<Agent>("/api/agents", "POST", { name, url, version, note, local, scenario_set: set });
      setSecret({ id: a.id, value: a.secret || "" });
      setSel(a);
      refresh();
    } catch (e) { setErr(String((e as Error).message || e)); }
  }
  async function ping() {
    if (!sel) return;
    setErr(""); setCheck(null);
    try { setCheck(await send<AgentCheck>(`/api/agents/${sel.id}/check`, "POST")); }
    catch (e) { setErr(String((e as Error).message || e)); }
  }
  async function run() {
    if (!sel) return;
    setErr("");
    try { setJob(await send<Job>(`/api/agents/${sel.id}/run`, "POST")); }
    catch (e) { setErr(String((e as Error).message || e)); }
  }

  const left = d.session.budget.day_max - d.session.budget.today;
  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Bring your own agent</span><h2>Put your own agent through the gate</h2>
        <p className="muted">Point the gate at any call agent that speaks a small HTTP contract. It gets the same simulated calls, the same rules and the same verdict as the built-in agents, judged against the current agent on the scenarios you can browse and on a sealed set.</p></div>
      {blocked && <p className="warn">{blocked}</p>}

      <div className="row2">
        <div className="panel">
          <h3>Register an agent</h3>
          <div className="row2">
            <label className="field" htmlFor="a-name">Name
              <input id="a-name" value={name} maxLength={60} placeholder="e.g. Our discharge agent" onChange={(e) => setName(e.target.value)} /></label>
            <label className="field" htmlFor="a-version">Version (change it when your agent changes)
              <input id="a-version" value={version} maxLength={40} onChange={(e) => setVersion(e.target.value)} /></label>
          </div>
          <label className="field" htmlFor="a-url">Endpoint URL
            <input id="a-url" value={url} maxLength={300} placeholder={local ? "http://127.0.0.1:8100/agent" : "https://agent.example.com/dsg"} onChange={(e) => setUrl(e.target.value)} /></label>
          <label className="field inline" htmlFor="a-local">
            <input id="a-local" type="checkbox" checked={local} onChange={(e) => setLocal(e.target.checked)} />
            Runs on this machine (allows http://localhost; nothing else private)</label>
          <div className="row2">
            <label className="field" htmlFor="a-set">Scenarios
              <select id="a-set" value={set} onChange={(e) => setSet(e.target.value as "quick" | "full")}>
                <option value="quick">Quick: 30 visible + 10 sealed, 2 replays each</option>
                <option value="full">Full: 102 visible + 20 sealed, 3 replays each</option>
              </select></label>
            <label className="field" htmlFor="a-note">Note (optional)
              <input id="a-note" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} /></label>
          </div>
          <div className="controls">
            <button type="button" className="btn primary" onClick={register}
              disabled={!!blocked || name.trim().length < 3 || url.trim().length < 10 || !version.trim()}>Register</button>
          </div>
          {err && <p className="err">{err}</p>}
        </div>

        <div className="panel">
          <h3>The contract</h3>
          <pre className="contract">{CONTRACT}</pre>
          <ul className="plain muted">
            <li>Full contract: <span className="mono">adapters/HTTP_CONTRACT.md</span>. A working example (the current agent behind a URL): <span className="mono">adapters/example_agent.py</span>.</li>
            <li>https only, to a public address. Private, loopback, link-local and cloud-metadata addresses are refused before anything is sent; redirects are not followed.</li>
            <li>15 s and 64 KB per reply, 8 steps per turn, unknown fields or tools refused. A call that breaks a limit ends with a clear error, isn't scored or cached, and the run can't be promoted.</li>
            <li>Your agent gets the call context and tools only: never the scenario id, the person's hidden facts or the answer key. We can't read its instructions, so only what it does on the calls is judged.</li>
          </ul>
        </div>
      </div>

      {sel && sel.owned && (
        <div className="panel">
          <div className="panel-head"><span className="mono">{sel.id}</span><b>{sel.name}</b>
            <span className="muted">{sel.endpoint} · version {sel.version}{sel.local ? " · local" : ""}</span></div>
          {secret && secret.id === sel.id && (
            <div className="secret">
              <b>Signing secret, shown once. Copy it now.</b>
              <code>{secret.value}</code>
              <span className="muted">Your agent uses it to check that requests come from this gate. For the example agent, set it as DSG_AGENT_SECRET. Lost it? <span className="mono">python -m evals.agents secret {sel.id}</span> on this machine.</span>
            </div>
          )}
          {sel.estimate && (
            <p className="notice">Running it places {sel.estimate.calls} calls to your agent
              {sel.estimate.baseline_calls ? ` plus ${sel.estimate.baseline_calls} for the current agent on the sealed set (once, then cached)` : ""}.
              Our side (the simulated person and the judge) is about ${sel.estimate.usd.toFixed(2)}, within your caps (${left.toFixed(2)} left today); your agent's own costs are yours.</p>
          )}
          <div className="controls">
            <button type="button" className="btn" onClick={ping} disabled={!!blocked}>Test connection</button>
            <button type="button" className="btn primary" onClick={run} disabled={!!blocked || job?.state === "running"}>
              Run the gate{sel.estimate ? ` (about $${sel.estimate.usd.toFixed(2)})` : ""}</button>
          </div>
          {check && (check.ok ? <p className="notice">Connected: signed ping answered in {check.seconds?.toFixed(2)} s.</p>
            : <p className="err">Not connected: {check.error}</p>)}
          {job && <JobCard job={job} onUpdate={setJob} onFinished={() => { refresh(); onChanged(); }} onError={setErr} />}
        </div>
      )}

      {list.length > 0 && (
        <>
          <h3>Registered agents</h3>
          <div className="plist">
            {list.map((a) => (
              <div key={a.id} className="pitem">
                <span className="mono">{a.id}</span>
                <span>{a.owned ? <button type="button" className="linklike" onClick={() => open(a.id)}>{a.name}</button> : a.name}
                  <span className="muted"> · {a.owned ? `${a.endpoint} · v${a.version}` : "registered by another session"} · {a.created_at.slice(0, 16).replace("T", " ")}</span></span>
                {a.result && a.owned && a.result.sealed.verdict !== "not run"
                  ? <button type="button" className={`chip v-${a.result.verdict}`} style={{ border: 0 }} onClick={() => go({ page: "changes", cand: a.id })}>{a.result.verdict}</button>
                  : a.result ? <span className={`chip v-${a.result.verdict}`}>{a.result.verdict}</span>
                  : <span className="status">{a.status}</span>}
              </div>
            ))}
          </div>
          <p className="muted">An agent belongs to the access token that registered it: only that token sees its address and calls. The token changes when the server restarts unless DSG_TOKEN is set in .env.</p>
        </>
      )}
    </section>
  );
}
