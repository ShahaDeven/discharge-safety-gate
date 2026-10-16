import { useEffect, useState } from "react";
import { getJSON, readRoute, type Overview as Data, type Route } from "./api";
import Calls from "./components/Calls";
import Changes from "./components/Changes";
import Live from "./components/Live";
import Method from "./components/Method";
import Overview from "./components/Overview";

const PAGES: [Route["page"], string][] = [["overview", "Overview"], ["changes", "Changes"], ["calls", "Calls"], ["live", "Live call"], ["method", "Method"]];

export default function App() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [route, setRoute] = useState<Route>(readRoute());

  useEffect(() => { getJSON<Data>("/api/overview").then(setD).catch((e) => setErr(String(e))); }, []);
  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  return (
    <>
      <header className="bar">
        <div className="bar-inner">
          <a href="#/overview" className="brand" style={{ color: "inherit", textDecoration: "none" }}>Discharge Safety Gate</a>
          <nav className="nav" aria-label="Sections">
            {PAGES.map(([p, label]) => <a key={p} href={`#/${p}`} aria-current={route.page === p ? "page" : undefined}>{label}</a>)}
          </nav>
          {d && <span className="budget">live calls: ${d.live.spent.toFixed(3)} of ${d.live.cap.toFixed(2)} this session</span>}
        </div>
      </header>
      <main className="main">
        {err && <p className="err">Couldn't reach the API ({err}). Start it with: uvicorn web.api:app --port 8001</p>}
        {!d && !err && <p className="loading">Loading the results...</p>}
        {d && route.page === "overview" && <Overview d={d} />}
        {d && route.page === "changes" && <Changes d={d} id={route.cand} />}
        {d && route.page === "calls" && <Calls d={d} route={route} />}
        {d && route.page === "live" && <Live d={d} route={route} onSpent={(spent) => setD({ ...d, live: { ...d.live, spent } })} />}
        {d && route.page === "method" && <Method d={d} />}
      </main>
    </>
  );
}
