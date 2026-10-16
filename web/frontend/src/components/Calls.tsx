import { useEffect, useState } from "react";
import { BLOCKS, getJSON, go, safe, shortName, type Overview, type Route, type Row, type Summary } from "../api";
import { Ladder, ResultPills, ScenarioCard, Transcript } from "./Call";

export function ScenarioSelect({ d, cand, sid, onChange, id }: { d: Overview; cand: string; sid: string; onChange: (s: string) => void; id: string }) {
  const ids = Object.keys(d.calls[cand] || {});
  return (
    <select id={id} value={sid} onChange={(e) => onChange(e.target.value)}>
      {Object.entries(BLOCKS).map(([b, label]) => {
        const items = ids.filter((i) => d.scenarios[i].block === b);
        return items.length ? (
          <optgroup key={b} label={label}>
            {items.map((i) => {
              const sc = d.scenarios[i];
              return <option key={i} value={i}>{i} · {sc.condition}{sc.tags.case ? ` · ${sc.tags.case.replace(/_/g, " ")}` : ""}</option>;
            })}
          </optgroup>
        ) : null;
      })}
    </select>
  );
}

function CallPanel({ d, cand, sid, r, onPick }: { d: Overview; cand: string; sid: string; r: number; onPick: (r: number) => void }) {
  const calls: Summary[] = d.calls[cand]?.[sid] || [];
  const [data, setData] = useState<{ summary: Summary; transcript: Row[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!calls.length) return;
    setData(null); setErr("");
    getJSON<{ summary: Summary; transcript: Row[] }>(`/api/call/${cand}/${sid}/${r}`).then(setData).catch((e) => setErr(String(e)));
  }, [cand, sid, r, calls.length]);
  if (!calls.length) return <div className="card"><span className="eyebrow">{cand} · {shortName(d, cand)}</span><p className="empty">This version was not run on this scenario.</p></div>;
  return (
    <div className="card">
      <div className="live-head">
        <span className="eyebrow">{cand} · {shortName(d, cand)}</span>
        <button type="button" className="btn" onClick={() => go({ page: "live", cand, sid, r })}>Watch it play</button>
      </div>
      <div className="replays">
        <span>Replays</span>
        {calls.map((x) => (
          <button key={String(x.r)} type="button" className={`dot ${safe(x) ? "pass" : "fail"}`} aria-pressed={x.r === r}
            title={safe(x) ? "kept the patient safe" : "missed a red flag or broke a rule"} onClick={() => onPick(Number(x.r))}>
            {Number(x.r) + 1}
          </button>
        ))}
      </div>
      {err && <p className="err">{err}</p>}
      {data ? (
        <>
          <Ladder expected={data.summary.expected} got={data.summary.got} ok={data.summary.ok} />
          <ResultPills s={data.summary} />
          <Transcript rows={data.transcript} sc={d.scenarios[sid]} />
        </>
      ) : !err && <p className="empty">Loading the call...</p>}
    </div>
  );
}

export default function Calls({ d, route }: { d: Overview; route: Route }) {
  const cand = route.cand && d.calls[route.cand] ? route.cand : "C3";
  const ids = Object.keys(d.calls[cand] || {});
  const sid = route.sid && ids.includes(route.sid) ? route.sid : ids[0];
  const first = (d.calls[cand][sid] || []).find((x) => !safe(x));
  const [r, setR] = useState<number>(route.r ?? Number(first?.r ?? 0));
  const [rBase, setRBase] = useState(0);
  const compare = !!route.compare && cand !== "C0";
  useEffect(() => { setR(route.r ?? Number((d.calls[cand][sid] || []).find((x) => !safe(x))?.r ?? 0)); setRBase(0); }, [cand, sid, route.r]);
  const set = (patch: Partial<Route>) => go({ page: "calls", cand, sid, compare, ...patch });

  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Calls</span><h2>Read any call</h2>
        <p className="muted">Marked text is where a rule was broken. Dots are the replays of the same scenario: green kept the patient safe, red did not.</p></div>
      <div className="controls">
        <label htmlFor="c-cand">Agent version
          <select id="c-cand" value={cand} onChange={(e) => set({ cand: e.target.value, sid: Object.keys(d.calls[e.target.value]).includes(sid) ? sid : undefined, r: undefined })}>
            {d.candidates.map((c) => <option key={c.id} value={c.id}>{c.id} · {shortName(d, c.id)}</option>)}
          </select>
        </label>
        <label htmlFor="c-sid">Scenario<ScenarioSelect id="c-sid" d={d} cand={cand} sid={sid} onChange={(s) => set({ sid: s, r: undefined })} /></label>
        <label className="check" htmlFor="c-cmp">
          <input id="c-cmp" type="checkbox" checked={compare} disabled={cand === "C0"} onChange={(e) => set({ compare: e.target.checked, r })} />
          Side by side with the current agent
        </label>
      </div>
      <div className="explore">
        <ScenarioCard sc={d.scenarios[sid]} />
        <div className={`calls${compare ? " two" : ""}`}>
          {compare && <CallPanel d={d} cand="C0" sid={sid} r={rBase} onPick={setRBase} />}
          <CallPanel d={d} cand={cand} sid={sid} r={r} onPick={setR} />
        </div>
      </div>
    </section>
  );
}
