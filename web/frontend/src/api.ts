// Shapes returned by web/api.py (built by evals/dashboard.py) and the plain-English labels the UI uses.

export type Mark = { rule: string; evidence: string; quote: string };
export type Row = {
  k: "s" | "c" | "r" | "e";
  w?: "a" | "p";
  t?: string;
  n?: string;
  i?: Record<string, unknown>;
  rev?: string[];
  v?: Mark[];
};
export type Summary = {
  r: number | string; expected: string; got: string; ok: boolean; under: boolean; false_esc: boolean;
  heard: boolean; elicited: string[]; med_expected: string[]; med_caught: string[]; med_missed: string[];
  med_false: string[]; ended_on_911: boolean; violations: string[]; told: boolean | null;
  crisis: boolean | null; coverage: number | null; ended_by: string; turns: number; cost: number;
};
export type Fact = { id: string; domain: string; phrasing: string; disclosure: string; truth: string; red_flag: string | null; severity?: string };
export type Scenario = {
  id: string; block: string; condition: string; jurisdiction: string; part2: boolean;
  party: { role: string; name: string; relationship: string };
  patient: { name: string; age: number; sex: string; language: string; literacy: string; hearing: string };
  discharge_date: string; call_date: string; discharge_meds: string[]; facts: Fact[];
  events: { type: string; text: string; at_reply: number }[];
  expected: { disposition: string; red_flags: string[]; med_issues: { drug: string; issue_type: string }[] };
  tags: Record<string, string>;
};
export type Metric = { name: string; tier: string; label: string; base: number | null; cand: number | null; diff: number; lo: number; hi: number; n: number; status: string; new_misses: string[] };
export type Rule = { rule: string; base_calls: number; base_n: number; cand_calls: number; cand_n: number; p: number; status: string; scenarios: string[] };
export type Gate = { verdict: "PROMOTE" | "HOLD" | "REJECT"; reasons: string[]; scenarios: number; version: number; metrics: Metric[]; rules: Rule[]; new_violations: Record<string, string[]>; description?: string;
  sealed?: { verdict: string; scenarios: number; reasons: string[] } };
export type Candidate = { id: string; description: string; k: number; scenarios: number; calls: number; why: string; proposal?: boolean; agent?: boolean };
export type Finding = { cand: string; sid: string; r: number; compare: boolean; title: string; body: string };
export type Overview = {
  candidates: Candidate[]; scenarios: Record<string, Scenario>; calls: Record<string, Record<string, Summary[]>>;
  gate: Record<string, Gate> & { noise_floor?: Record<string, number> };
  curve: { draws?: number; "A/A"?: Record<string, { false_alarm: number }>; candidates?: Record<string, { full_verdict: string; k: Record<string, { agree_with_full: number }> }> };
  ledger: { id: string; candidate: string; baseline: string; verdict: string; reasons: string[] }[];
  judge_check: { violations_caught: [number, number]; allowed_flagged: [number, number]; borderline_flagged: [number, number] } | null;
  judge_flags?: { label: string; kind: string; lines: number; answered: number; reviewer_yes: number }[];
  findings: Finding[]; cost: Record<string, number>; session: Session; policy?: PolicyDef;
  redteam: null | { caught: number; total: number; results: { attack: string; proposal: string; expected: string[]; verdict: string; caught: boolean; caught_by?: string[]; detail?: string }[] };
};

export async function getJSON<T>(url: string): Promise<T> {
  const t = token();          // an owner sees more (their outside agents); nobody else does
  const r = await fetch(url, t ? { headers: { "X-DSG-Token": t } } : undefined);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const CONTROL = "A/A";
export const SHORT: Record<string, string> = { C0: "Current agent", C1: "Short calls", C2: "Sonnet model", C3: "Warm greeting", C4: "Higher effort" };
export const DISP: Record<string, string> = { none: "No action", routine_followup: "Routine", same_day_clinician: "Nurse today", emergency_now: "911 now" };
export const RANK: Record<string, number> = { none: 0, routine_followup: 1, same_day_clinician: 2, emergency_now: 3 };
export const RULES: Record<string, string> = {
  PHI_BEFORE_VERIFY: "Health details before checking identity",
  CAREGIVER_SCOPE: "Health details to a relative not on the contact list",
  VOICEMAIL_MIN_NECESSARY: "Health details left on voicemail",
  PART2_LEAK: "Addiction treatment disclosed (42 CFR Part 2)",
  OPT_OUT: "Ignored a request to stop calling",
  AI_DISCLOSURE: "Didn't say it's an AI, or no way to reach a human",
  SCOPE: "Medical advice or reassurance (judge)",
  NO_THERAPY: "Therapy on a mental-health call (judge)",
};
export const ROLE: Record<string, string> = {
  patient: "The patient", caregiver_authorized: "A relative on the contact list",
  caregiver_unauthorized: "A relative not on the contact list", wrong_person: "The wrong person", voicemail: "Voicemail",
};
export const DISC: Record<string, string> = { explicit: "says it plainly", implicit: "only hints at it", only_if_asked: "mentions it only if asked" };
export const STATUS: Record<string, [string, string]> = {
  same: ["no change", "st-same"], improved: ["better", "st-ok"], worse: ["worse", "st-bad"],
  worse_noise: ["lower, within noise", "st-hold"], new_miss: ["new misses", "st-bad"],
  new_kind: ["new kind of violation", "st-bad"], more_often: ["more often", "st-bad"],
};
export const TOOL: Record<string, string> = {
  verify_identity: "Checked identity", escalate: "Escalated", flag_med_issue: "Flagged a medicine problem",
  handoff_to_human: "Transferred to a nurse", record_opt_out: "Recorded opt-out", leave_voicemail: "Left a voicemail",
  end_call: "Ended the call",
};
export const BLOCKS: Record<string, string> = { clinical: "Clinical", compliance: "Compliance", psych: "Mental health", fairness: "Fairness repeats" };
export const pct = (x: number | null | undefined) => (x == null ? "-" : `${Math.round(x * 100)}%`);
export const safe = (s: Summary) => s.ok && !s.violations.length && s.crisis !== false;

// Where to send people: a hash route the whole app reads.
export type Route = { page: "overview" | "changes" | "calls" | "live" | "propose" | "agents" | "policy" | "validation" | "method"; cand?: string; sid?: string; r?: number; compare?: boolean };
export function go(route: Route) {
  const q = new URLSearchParams();
  if (route.cand) q.set("cand", route.cand);
  if (route.sid) q.set("sid", route.sid);
  if (route.r != null) q.set("r", String(route.r));
  if (route.compare) q.set("compare", "1");
  location.hash = `/${route.page}${q.toString() ? "?" + q : ""}`;
  window.scrollTo({ top: 0 });
}
export function readRoute(): Route {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const q = new URLSearchParams(query || "");
  const page = (["overview", "changes", "calls", "live", "propose", "agents", "policy", "validation", "method"].includes(path) ? path : "overview") as Route["page"];
  return { page, cand: q.get("cand") || undefined, sid: q.get("sid") || undefined,
    r: q.has("r") ? Number(q.get("r")) : undefined, compare: q.get("compare") === "1" };
}

// ---------- access token (kept for this browser tab only) and requests that spend or change things
const TOKEN_KEY = "dsg-token";
export function getToken(): string {
  try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(t: string) {
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked: token lives in memory only */ }
  memToken = t;
}
let memToken = "";
export const token = () => getToken() || memToken;

async function errorText(r: Response) {
  const j = await r.json().catch(() => null);
  return (j && (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail))) || `${r.status} ${r.statusText}`;
}
export async function send<T>(url: string, method: "POST" | "GET", body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method, headers: { "Content-Type": "application/json", "X-DSG-Token": token() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await errorText(r));
  return r.json() as Promise<T>;
}

export type Budget = { session: number; session_max: number; today: number; day_max: number; run_max: number };
export type Session = { read_only: boolean; budget: Budget; token_required: boolean };
export type Estimate = { calls: number; baseline_calls: number; visible: number; holdout: number; k: number; usd: number; blocked: string[] };
export type Proposal = {
  id: string; name: string; note: string; model: string; effort: string; scenario_set: string; created_at: string;
  status: "saved" | "running" | "done" | "error"; prompt?: string; estimate?: Estimate;
  result: null | { verdict: Gate["verdict"]; reasons: string[]; spent_usd: number; k: number; blocked?: boolean; sealed: { verdict: string; scenarios: number; reasons: string[] } };
};
export type Job = { id: string; pid: string; state: "running" | "done" | "error"; phase: string; done: number; total: number; spent: number; error: string | null; estimate: Estimate; result: Proposal["result"] };

export type Agent = {
  id: string; name: string; status: Proposal["status"]; created_at: string; owned: boolean; result: Proposal["result"];
  url?: string; endpoint?: string; version?: string; note?: string; local?: boolean; scenario_set?: string;
  estimate?: Estimate; secret?: string;
};
export type PolicyDef = { name: string; fingerprint: string; scope: string[]; extend: string[] };
export type PolicyVerdicts = Record<string, { verdict: Gate["verdict"]; reasons: string[]; rules: Record<string, number[]> }>;
export type PolicyPage = {
  categories: { key: string; label: string; example: string; locked: boolean; default: boolean }[];
  extensions: { key: string; label: string; law: string; rule: string }[];
  active: PolicyDef; history: (PolicyDef & { note: string; owner: string; applied_at: string })[];
  counts: { categories: Record<string, { lines: number; calls: number }>; judged: number; calls: number };
  reviewer: null | { rows: { category: string; label: string; lines: number; judge_agrees: number }[]; agree: number; n: number };
  verdicts: PolicyVerdicts;
};
type Pair = [number, number];
export type ValidationPack = {
  candidate: { id: string; description: string; why: string; k: number | null; calls: number; agent: boolean; proposal: boolean; results_date: string };
  verdict: { verdict: Gate["verdict"]; reasons: string[]; sealed: Gate["sealed"] | null; gate_version: number; baseline: boolean };
  intended_use: string;
  testing: { scenarios: number; sealed: number; conditions: Record<string, number>; states: Record<string, number>;
    answered_by: Record<string, number>; blocks: Record<string, number>; disclosure: Record<string, number>; models: Record<string, string> };
  performance: { metrics: Metric[]; rules: Rule[]; scenarios: number;
    absolute: { label: string; k: number; n: number; rate: number; lo: number; hi: number }[] };
  subgroups: { styles: { group: string; cand: Pair; base: Pair }[];
    repeats: { id: string; source: string; persona: string; variant: Pair; original: Pair }[] };
  rules: { rule: string; summary: string; source: string; check: string; where: string }[];
  policy: PolicyDef; policy_categories: PolicyPage["categories"];
  judge: { planted: (Overview["judge_check"] & { category_right?: Pair }) | null; reviewer: PolicyPage["reviewer"];
    held_out: { n: number; human_yes: number; judge_yes: number; agree: number; kappa: number } | null };
  security: { redteam: Overview["redteam"]; audit: { ok: boolean; entries: number; message: string } };
  changes: Overview["ledger"]; limits: string[];
};
export type AgentCheck = { ok: boolean; seconds: number | null; error: string | null };

/** Short label for any candidate, including proposals (P-001 ...). */
export function shortName(d: { candidates: Candidate[] }, id: string): string {
  return SHORT[id] || d.candidates.find((c) => c.id === id)?.description || id;
}
