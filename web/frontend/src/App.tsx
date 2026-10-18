import { useEffect, useState } from "react";
import { getJSON, readRoute, send, setToken, token, type Budget, type Overview as Data, type Route } from "./api";
import Calls from "./components/Calls";
import Changes from "./components/Changes";
import Live from "./components/Live";
import Method from "./components/Method";
import Overview from "./components/Overview";
import Agents from "./components/Agents";
import Policy from "./components/Policy";
import Propose from "./components/Propose";

const PAGES: [Route["page"], string][] = [["overview", "Overview"], ["changes", "Changes"], ["calls", "Calls"],
  ["live", "Live call"], ["propose", "Propose"], ["agents", "Agents"], ["policy", "Policy"], ["method", "Method"]];

/** Paste the token the server printed; it is checked once and kept for this browser tab only. */
function Unlock({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  async function check() {
    setToken(value.trim());
    try { await send("/api/session/check", "POST"); onClose(); }
    catch (e) { setToken(""); setErr(String((e as Error).message || e)); }
  }
  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-labelledby="unlock-h">
      <div className="modal">
        <h3 id="unlock-h">Unlock actions that spend money</h3>
        <p className="muted">Live calls and proposals cost real API time. Paste the access token printed in the terminal where the server is running. It stays in this browser tab only.</p>
        <label className="field" htmlFor="tok">Access token
          <input id="tok" type="password" autoComplete="off" value={value} onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && check()} /></label>
        {err && <p className="err">{err}</p>}
        <div className="controls"><button type="button" className="btn primary" onClick={check} disabled={!value.trim()}>Unlock</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}

export default function App() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [route, setRoute] = useState<Route>(readRoute());
  const [unlocking, setUnlocking] = useState(false);
  const [, force] = useState(0);

  const load = (refresh = false) => getJSON<Data>(`/api/overview${refresh ? "?refresh=true" : ""}`).then(setD).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const setBudget = (budget: Budget) => d && setD({ ...d, session: { ...d.session, budget } });
  const b = d?.session.budget;

  return (
    <>
      <header className="bar">
        <div className="bar-inner">
          <a href="#/overview" className="brand" style={{ color: "inherit", textDecoration: "none" }}>Discharge Safety Gate</a>
          <nav className="nav" aria-label="Sections">
            {PAGES.map(([p, label]) => <a key={p} href={`#/${p}`} aria-current={route.page === p ? "page" : undefined}>{label}</a>)}
          </nav>
          {d && (
            <div className="budget lock">
              {b && <span>spent today ${b.today.toFixed(2)} of ${b.day_max.toFixed(2)}</span>}
              {d.session.read_only ? <span className="state">read-only demo</span>
                : token() ? <button type="button" className="state ok" onClick={() => { setToken(""); force((x) => x + 1); }} title="Forget the token">unlocked</button>
                : <button type="button" className="state" onClick={() => setUnlocking(true)}>locked: unlock</button>}
            </div>
          )}
        </div>
      </header>
      {unlocking && <Unlock onClose={() => { setUnlocking(false); force((x) => x + 1); load(); }} />}
      <main className="main">
        {err && <p className="err">Couldn't reach the API ({err}). Start it with: python -m web</p>}
        {!d && !err && <p className="loading">Loading the results...</p>}
        {d && route.page === "overview" && <Overview d={d} />}
        {d && route.page === "changes" && <Changes d={d} id={route.cand} />}
        {d && route.page === "calls" && <Calls d={d} route={route} />}
        {d && route.page === "live" && <Live d={d} route={route} onBudget={setBudget} />}
        {d && route.page === "propose" && <Propose d={d} onChanged={() => load(true)} />}
        {d && route.page === "agents" && <Agents d={d} onChanged={() => load(true)} />}
        {d && route.page === "policy" && <Policy d={d} onChanged={() => load(true)} />}
        {d && route.page === "method" && <Method d={d} />}
      </main>
    </>
  );
}
