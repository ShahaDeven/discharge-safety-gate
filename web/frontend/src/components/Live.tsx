import { useEffect, useRef, useState } from "react";
import { ROLE, go, shortName, token, type Budget, type Overview, type Route, type Row, type Summary } from "../api";
import { Ladder, ResultPills, ScenarioCard, Transcript } from "./Call";
import { ScenarioSelect } from "./Calls";
import { useSpeech } from "./useSpeech";

type Phase = "idle" | "running" | "done" | "error";

/** Reads the server-sent-event stream with fetch, so a refused live call (spending cap) shows its reason. */
async function stream(url: string, signal: AbortSignal, on: (event: string, data: any) => void) {
  const res = await fetch(url, { signal, headers: { "X-DSG-Token": token() } });
  if (!res.ok || !res.body) throw new Error((await res.json().catch(() => null))?.detail || `${res.status} ${res.statusText}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(chunk)?.[1];
      const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (ev && data) on(ev, JSON.parse(data));
    }
  }
}

export default function Live({ d, route, onBudget }: { d: Overview; route: Route; onBudget: (b: Budget) => void }) {
  const cand = route.cand && d.calls[route.cand] ? route.cand : "C3";
  const ids = Object.keys(d.calls[cand] || {});
  const sid = route.sid && ids.includes(route.sid) ? route.sid : ids[0];
  const replays = d.calls[cand][sid] || [];
  const sc = d.scenarios[sid];
  const est = cand === "C2" ? 0.024 : cand === "C4" ? 0.004 : 0.003;
  const voice = useSpeech(sc?.patient.language === "Spanish" ? "es" : "en");

  const [mode, setMode] = useState<"replay" | "live">("replay");
  const [r, setR] = useState(route.r ?? 0);
  const [speed, setSpeed] = useState(1);
  const [showKey, setShowKey] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [cost, setCost] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { stop(); setRows([]); setSummary(null); setPhase("idle"); setR(route.r ?? 0); setShowKey(false); }, [cand, sid]);
  useEffect(() => { if (phase === "running") bottom.current?.scrollIntoView({ block: "nearest" }); }, [rows.length, phase]);
  useEffect(() => () => abort.current?.abort(), []);

  function stop() { abort.current?.abort(); abort.current = null; voice.stop(); }

  async function start() {
    stop();
    const ctl = new AbortController(); abort.current = ctl;
    setRows([]); setSummary(null); setCost(null); setMsg(""); setPhase("running");
    // Spoken lines take ~3x longer than reading pace, so a voiced replay slows down to stay in step.
    const pace = voice.on && mode === "replay" ? Math.max(0.3, speed * 0.3) : speed;
    const q = new URLSearchParams({ cand, sid, mode, r: String(r), speed: String(pace) });
    try {
      await stream(`/api/stream?${q}`, ctl.signal, (ev, data) => {
        if (ev === "row") { setRows((x) => [...x, data]); if (data.k === "s") voice.say(data.t, data.w); }
        if (ev === "scored") { setRows(data.transcript); setSummary(data.summary); }
        if (ev === "done") { setCost(data.cost); if (data.budget) onBudget(data.budget); setPhase("done"); }
        if (ev === "error") { setMsg(data.message); setPhase("error"); }
      });
      setPhase((p) => (p === "running" ? "done" : p));
    } catch (e: any) {
      if (e?.name !== "AbortError") { setMsg(String(e.message || e)); setPhase("error"); }
    }
  }

  const b = d.session.budget;
  const left = Math.max(0, Math.min(b.session_max - b.session, b.day_max - b.today));
  const liveBlocked = d.session.read_only ? "Live calls are off on this read-only demo." : !token() ? "Unlock with the access token (top right) to place live calls." : "";
  const last = rows[rows.length - 1];
  const typing = phase !== "running" ? undefined
    : !rows.length ? "dialing" : last?.k === "s" && last.w === "a" ? `${ROLE[sc.party.role].toLowerCase()} is answering` : "agent is responding";

  return (
    <section>
      <div className="sect-head"><span className="eyebrow">Live call</span><h2>Watch a call happen</h2>
        <p className="muted"><b>Replay</b> plays a call from the results at talking speed, for free. <b>Live</b> places a brand-new call through the real agent and simulated patient, then scores it. Live calls are saved separately and never change the results.</p></div>
      <div className="controls">
        <label htmlFor="l-cand">Agent version
          <select id="l-cand" value={cand} onChange={(e) => go({ page: "live", cand: e.target.value, sid: Object.keys(d.calls[e.target.value]).includes(sid) ? sid : undefined })}>
            {d.candidates.map((c) => <option key={c.id} value={c.id}>{c.id} · {shortName(d, c.id)}</option>)}
          </select>
        </label>
        <label htmlFor="l-sid">Scenario<ScenarioSelect id="l-sid" d={d} cand={cand} sid={sid} onChange={(s) => go({ page: "live", cand, sid: s })} /></label>
        <div className="seg" role="group" aria-label="Mode">
          <button type="button" aria-pressed={mode === "replay"} onClick={() => setMode("replay")}>Replay</button>
          <button type="button" aria-pressed={mode === "live"} onClick={() => setMode("live")}>Live</button>
        </div>
        {mode === "replay" ? (
          <>
            <label htmlFor="l-r">Replay
              <select id="l-r" value={r} onChange={(e) => setR(Number(e.target.value))}>
                {replays.map((x) => <option key={String(x.r)} value={Number(x.r)}>{Number(x.r) + 1}{x.ok && !x.violations.length ? "" : " (went wrong)"}</option>)}
              </select>
            </label>
            <label htmlFor="l-speed">Speed
              <select id="l-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                {[1, 2, 4].map((s) => <option key={s} value={s}>{s}x</option>)}
              </select>
            </label>
          </>
        ) : (
          <span className="status">about ${est.toFixed(3)} per call · ${left.toFixed(2)} left under today's and this session's caps</span>
        )}
        {voice.supported && (
          <label className="check" htmlFor="l-voice">
            <input id="l-voice" type="checkbox" checked={voice.on} onChange={(e) => voice.setOn(e.target.checked)} /> Hear the call
          </label>
        )}
        {phase === "running"
          ? <button type="button" className="btn danger" onClick={() => { stop(); setPhase("idle"); }}>Stop</button>
          : <button type="button" className="btn primary" onClick={start} disabled={mode === "live" && (left < est || !!liveBlocked)}>
              {mode === "replay" ? "Play the call" : `Place a live call (~$${est.toFixed(3)})`}
            </button>}
      </div>
      {mode === "live" && liveBlocked && <p className="warn">{liveBlocked}</p>}
      {mode === "live" && cand === "C2" && <p className="warn">C2 runs on Sonnet, about ten times the cost of the other versions.</p>}
      {voice.on && <p className="sim-label">Simulated call · synthetic patient · computer voices, nothing is recorded</p>}
      <div className="explore">
        <ScenarioCard sc={sc} hideKey={!showKey && phase !== "done"} onReveal={() => setShowKey(true)} />
        <div className="card">
          <div className="live-head">
            <span className="eyebrow">{cand} · {shortName(d, cand)} · {mode === "replay" ? `replay ${r + 1}` : "live"}</span>
            <span className="status">
              {phase === "idle" && "ready"}{phase === "running" && "on the call"}
              {phase === "done" && (cost ? `scored · cost $${cost.toFixed(4)}` : "scored")}{phase === "error" && "stopped"}
            </span>
          </div>
          {summary ? <><Ladder expected={summary.expected} got={summary.got} ok={summary.ok} /><ResultPills s={summary} /></>
            : <Ladder expected={showKey ? sc.expected.disposition : ""} />}
          {msg && <p className="err">{msg}</p>}
          {phase === "idle" && !rows.length && <p className="empty">Press play. The patient's answers, the agent's questions and every action it takes appear as they happen; the score lands when the call ends.</p>}
          <Transcript rows={rows} sc={sc} typing={typing} />
          <div ref={bottom} />
        </div>
      </div>
    </section>
  );
}
