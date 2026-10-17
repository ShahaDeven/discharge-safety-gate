import { useEffect } from "react";
import { getJSON, go, type Job } from "../api";

/** A gate run in progress, then its verdict. Polls the job until it finishes. */
export default function JobCard({ job, onUpdate, onFinished, onError }: {
  job: Job; onUpdate: (j: Job) => void; onFinished: () => void; onError: (e: string) => void;
}) {
  useEffect(() => {
    if (job.state !== "running") return;
    const t = setInterval(() => getJSON<Job>(`/api/jobs/${job.id}`).then((j) => {
      onUpdate(j);
      if (j.state !== "running") onFinished();
    }).catch((e) => onError(String(e))), 1500);
    return () => clearInterval(t);
  }, [job.id, job.state]);

  const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
  const r = job.result;
  const ran = r && !r.blocked && r.sealed.verdict !== "not run";
  return (
    <div className="card">
      <div className="live-head"><span className="eyebrow">{job.pid} · {job.state === "running" ? job.phase : job.state}</span>
        <span className="status">${job.spent.toFixed(3)} spent</span></div>
      {job.state === "running" && <><div className="progress"><div style={{ width: `${pct}%` }} /></div>
        <span className="status">{job.total ? `${job.done} of ${job.total} ${job.phase}` : job.phase}</span></>}
      {job.error && <p className="err">{job.error}</p>}
      {r && (
        <>
          <div className="panel-head"><span className={`chip v-${r.verdict}`}>{r.verdict}</span>
            <span className="muted">{r.blocked ? "stopped by the prompt check: no calls placed"
              : !ran ? "no call completed"
              : `visible scenarios and sealed set (${r.sealed.scenarios} scenarios: ${r.sealed.verdict})`}</span></div>
          <ul className="plain">{(r.reasons.length ? r.reasons : ["Nothing got worse beyond normal run-to-run variation, on the visible or the sealed scenarios."]).map((x) => <li key={x}>{x}</li>)}</ul>
          {ran && <div className="controls">
            <button type="button" className="btn" onClick={() => go({ page: "changes", cand: job.pid })}>See the full comparison</button>
            <button type="button" className="btn" onClick={() => go({ page: "calls", cand: job.pid, compare: true })}>Read its calls</button>
          </div>}
        </>
      )}
    </div>
  );
}
