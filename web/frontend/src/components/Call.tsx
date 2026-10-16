import { Fragment, type ReactNode } from "react";
import { BLOCKS, DISC, DISP, RANK, ROLE, RULES, TOOL, type Mark, type Row, type Scenario, type Summary } from "../api";

/** Who the patient is, who picks up, and the hidden answer key (optionally covered for live viewing). */
export function ScenarioCard({ sc, hideKey = false, onReveal }: { sc: Scenario; hideKey?: boolean; onReveal?: () => void }) {
  const p = sc.party;
  const who = ROLE[p.role] + (p.relationship ? ` (${p.relationship}${p.name ? ", " + p.name : ""})` : "");
  const extras = [
    sc.patient.language !== "English" && `${sc.patient.language} speaker`,
    sc.patient.literacy === "low" && "low health literacy",
    sc.patient.hearing === "hard_of_hearing" && "hard of hearing",
  ].filter(Boolean);
  const flags = sc.facts.filter((f) => f.red_flag);
  return (
    <div className="card">
      <span className="eyebrow">{sc.id} · {BLOCKS[sc.block]}</span>
      <h3>{sc.condition}</h3>
      <KV k="Who picks up">{who}</KV>
      <KV k="Patient">{sc.patient.name}, {sc.patient.age}{extras.length ? ` · ${extras.join(", ")}` : ""}</KV>
      <KV k="State rules">{sc.jurisdiction}{sc.part2 ? " · record under 42 CFR Part 2" : ""}</KV>
      {hideKey ? (
        <div className="hidden-key">
          <span className="muted">The answer key is hidden, so you see the call the way the agent does.</span>
          <button type="button" className="btn" onClick={onReveal}>Show the answer key</button>
        </div>
      ) : (
        <>
          {flags.length ? flags.map((f) => (
            <div className="kv" key={f.id}>
              <span className="k">Hidden warning sign</span>
              <span className={`sev sev-${f.severity}`}>{f.severity === "red" ? "Needs 911" : "Needs a nurse today"}</span>
              <span className="quote">"{f.phrasing}"</span>
              <span className="muted">The patient {DISC[f.disclosure]}. Truth: {f.truth}</span>
            </div>
          )) : <KV k="Hidden warning sign">None. This patient is fine; escalating would be a false alarm.</KV>}
          {sc.tags.recipe && <KV k="Planted medicine problem">{sc.tags.recipe.replace(":", ": ").replace(/_/g, " ")}</KV>}
          {sc.events.map((e, i) => <KV k="Scripted moment" key={i}>"{e.text}"</KV>)}
        </>
      )}
      <KV k="Discharge medicines">{sc.discharge_meds.join("; ")}</KV>
    </div>
  );
}

function KV({ k, children }: { k: string; children: ReactNode }) {
  return <div className="kv"><span className="k">{k}</span><span>{children}</span></div>;
}

/** No action -> Routine -> Nurse today -> 911 now: what was expected (dashed) and what the agent did (filled). */
export function Ladder({ expected, got, ok }: { expected: string; got?: string; ok?: boolean }) {
  return (
    <div className="kv">
      <span className="k">Expected vs what the agent did</span>
      <div className="ladder">
        {Object.keys(DISP).map((d) => (
          <div key={d} className={["rung", d === expected && "exp", d === got && "got", d === got && !ok && "missed"].filter(Boolean).join(" ")}>
            {DISP[d]}
          </div>
        ))}
      </div>
      <div className="legend">
        <span>dashed = expected</span>
        {got && <span>{ok ? "filled green = what it did" : "filled red = what it did (unsafe)"}</span>}
      </div>
    </div>
  );
}

export function ResultPills({ s }: { s: Summary }) {
  const pills: [string, string?][] = [];
  if (RANK[s.expected] >= 2) pills.push([s.heard ? "warning sign came up on the call" : "warning sign never came up", s.heard ? "good" : "bad"]);
  if (s.med_expected.length) pills.push([s.ended_on_911 ? "ended on 911 before the medicine review" : `medicine problem ${s.med_caught.length ? "caught" : "missed"}`, s.med_caught.length ? "good" : "bad"]);
  if (s.crisis === false) pills.push(["no crisis line said out loud", "bad"]);
  if (s.told === false) pills.push(["escalated without saying what happens next", "bad"]);
  for (const v of s.violations) pills.push([RULES[v] || v, "bad"]);
  pills.push([`${s.turns} agent turns · ${(TOOL[s.ended_by] || s.ended_by.replace(/_/g, " ")).toLowerCase()}`]);
  return <div className="result">{pills.map(([t, c], i) => <span key={i} className={`pill ${c || ""}`}>{t}</span>)}</div>;
}

function marked(text: string, marks?: Mark[]) {
  const terms = (marks || []).map((m) => (m.rule === "SCOPE" || m.rule === "NO_THERAPY" ? m.quote : m.evidence))
    .filter((t) => t && t.length > 1 && !/^no |^never /.test(t));
  if (!terms.length) return text;
  const re = new RegExp(terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
  const out: ReactNode[] = [];
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text)) && m[0].length) {
    out.push(text.slice(last, m.index), <mark key={i++} className="viol">{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

/** The conversation, with tool calls as small action rows and rule breaks marked in the agent's words. */
export function Transcript({ rows, sc, typing }: { rows: Row[]; sc: Scenario; typing?: string }) {
  const flagIds = sc.facts.filter((f) => f.red_flag).map((f) => f.id);
  const items: ReactNode[] = [];
  for (let i = 0; i < rows.length; i++) {
    const e = rows[i];
    if (e.k === "s") {
      items.push(
        <div key={i} className={`line ${e.w}${e.v ? " flagged" : ""}`}>
          <span className="who">{e.w === "a" ? "Agent" : ROLE[sc.party.role]}</span>
          <div className="text">{marked(e.t || "", e.v)}</div>
          {e.rev?.some((id) => flagIds.includes(id)) && <span className="rev">reveals the warning sign</span>}
          {(e.v || []).map((v, j) => <span key={j} className="note">{RULES[v.rule] || v.rule}</span>)}
        </div>,
      );
    } else if (e.k === "c") {
      const res = rows[i + 1]?.k === "r" ? rows[++i] : null;
      const inp = e.i || {};
      const detail = e.n === "escalate" ? ` → ${DISP[String(inp.level)] || inp.level}: ${inp.red_flag || ""}`
        : e.n === "flag_med_issue" ? ` → ${inp.drug} (${String(inp.issue_type).replace(/_/g, " ")})`
        : e.n === "leave_voicemail" ? `: "${inp.message}"` : res ? ` → ${res.t}` : "";
      items.push(
        <div key={i} className="action">
          <b>{TOOL[e.n || ""] || e.n}</b>{detail}
          {(e.v || []).map((v, j) => <Fragment key={j}><br /><span className="st st-bad">{RULES[v.rule] || v.rule}</span></Fragment>)}
        </div>,
      );
    } else if (e.k === "e") {
      items.push(<div key={i} className="action">Agent error: {e.t}</div>);
    }
  }
  return <div className="transcript" aria-live="polite">{items}{typing && <span className="typing">{typing}</span>}</div>;
}
