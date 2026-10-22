# Threat model

What this product protects, from whom, the controls in place, how they are tested, and what is not covered.
Architecture: [architecture.md](architecture.md). Design history: PLAN.md sections 12.1-12.4.

All data is synthetic. The controls are built so they still hold if a real deployment later carries PHI;
what changes in that case is listed at the end.

## What we protect

| Asset | Why it matters | Who might go after it |
|---|---|---|
| API keys and budget (Anthropic, ElevenLabs) | Real money; the only things here that cost anything | Anyone who reaches the server; a runaway proposal or job |
| Answer keys: hidden facts, expected outcomes, the sealed holdout | If the agent under test sees them, the test means nothing | A proposed prompt or an outside agent trying to pass the gate |
| Harness prompts: the simulator's script rules, the judge prompt | If they leak, an agent can be tuned to fool them | An outside agent asking the simulated patient to repeat its instructions |
| An outside agent's prompt and IP | The owner's property | Someone else running it through this harness |
| Verdict integrity | A gate that can be talked into PROMOTE is worse than no gate | An agent that tells the judge the call was clean; edited cache or scenario files |
| The machine running it | The server makes outbound requests | A malicious agent endpoint (SSRF); a crafted request from another website (CSRF, DNS rebinding) |

## Trust boundaries

```
 browser ──token, Host check──▶ console API ──▶ jobs (proposals, outside agents, live calls)
                                     │                 │
                                     ▼                 ▼
                         read-only results     untrusted text: proposed prompts, agent speech,
                         (cache, gate, audit)  outside-agent replies, transcripts
                                                       │
                                     ┌─────────────────┼──────────────────┐
                                     ▼                 ▼                  ▼
                                simulator           judge          deterministic scoring
                           (quoted data, leak   (quoted data,      (tool calls only:
                            filter, canaries)    canaries)          injection can't move it)
```

Everything an agent says, and everything an outside endpoint returns, is untrusted input.

## OWASP Top 10 for LLM Applications (2025)

| Risk | Where it shows up | Control | Tested by |
|---|---|---|---|
| LLM01 Prompt injection | Agent speech reaches the simulator and the judge; proposed prompts are attacker-controlled by design | Transcripts reach models as quoted data inside delimiters, with angle brackets neutralized. An injection detector (`discharge/guard.py`) flags ignore-previous, fake system lines and addressing the grader; a hit is the hard rule **MANIPULATION** (REJECT). A free prompt check rejects manipulative lines in a proposal before any call. Escalation, privacy and medicine scoring are deterministic | red team: grader address, instruction readout |
| LLM02 Sensitive information disclosure | Answer keys; other users' transcripts | The agent gets only the call context. Canary phrases in the simulator and judge prompts; a canary in any output flags the call. Outside agents' transcripts are visible only to their owner | red team: "ask for hidden facts"; `tests/test_security.py` |
| LLM03 Supply chain | pip and npm packages; model versions | Pinned versions; `pip-audit`, `npm audit` and Semgrep in `evals/security_check.py`. Model IDs fixed in config and recorded in every audit entry | `python -m evals.security_check` |
| LLM04 Data and model poisoning | Scenario files or the cache edited to fake a verdict | The suite hash is in every fingerprint and audit entry; scenarios are read-only in the UI; a **sealed holdout** of 20 scenarios never served by the API votes on every proposal | red team: teaching to the test (HOLD) |
| LLM05 Improper output handling | Transcripts shown in the browser; tool inputs written by a model | All model text renders as plain text (React escaping, no HTML, no links). Tool inputs are schema-checked with length limits. Nothing a model writes is executed | `tests/test_security.py` |
| LLM06 Excessive agency | What the agent can do during a call | Only harness tools; they return canned strings and change nothing real. No network, files or code. Callbacks can only be booked inside calling hours and within 7 days | `tests/test_workflows.py` |
| LLM07 System prompt leakage | Simulator and judge prompts | The output filter swaps any simulator reply containing a canary, hidden-fact ids or rule text for an in-character line and flags the call | red team; offline filter tests |
| LLM08 Vector and embedding weaknesses | No retrieval in this product | Not applicable | - |
| LLM09 Misinformation | The judge can be wrong; the agent can give unsafe advice | The judge is measured (planted lines, human review of its flags); unsafe advice is what SCOPE catches, and the clinical policy decides what counts | `evals/judge_check.py` |
| LLM10 Unbounded consumption | Huge prompts, endless calls, repeated runs, slow endpoints | Prompt size cap; caps on turns, tool rounds and characters per turn; per-run, per-session and per-day spending caps saved to disk; one job at a time; timeouts and response size limits; ElevenLabs credit caps on every voice command | `tests/test_security.py`, `tests/test_agents.py` |

## The console and server

- **Local by default:** binds to 127.0.0.1. A public demo runs **read-only**: no live calls, proposals or outside
  agents, and no API key on that server.
- **Access token:** generated at startup (or `DSG_TOKEN`), sent in a custom header on every request that spends
  money or changes data. A custom header can't be set by another website, which stops CSRF.
- **Host allowlist:** requests for any other Host are refused, which stops DNS rebinding.
- **Headers:** Content-Security-Policy (self plus the font host), `nosniff`, `frame-ancestors 'none'`,
  `no-referrer`.
- **Errors:** redacted before they reach the browser; keys never appear in logs or responses.
- **Inputs:** pydantic models with length limits; unknown fields rejected.
- **Audit:** every proposal, agent registration, live call, verdict and policy change is appended to a
  hash-chained log with the token id and model ids; the validation pack verifies the chain.

## Outside agents (`agentgate/http_agent.py`)

- **https only**; private, loopback, link-local and cloud-metadata addresses refused (local agents only in local
  mode); the resolved address is pinned per call; no redirects or proxies.
- **HMAC-signed requests** with a timestamp and nonce, so an endpoint can prove the caller is the gate.
- **Strict reply schema** with time, size and step limits; failed calls are never cached or promoted.
- **Owner-only:** only the token that registered an agent sees its calls.

## ElevenLabs Agents bridge (`agentgate/elevenagents.py`)

- Connects only to `wss://*.elevenlabs.io`, through a signed URL fetched with the API key in a header (never in a
  URL). The agent is private (authentication on), so nobody else can open conversations with it.
- Sends the call context as dynamic variables, never the scenario id, hidden facts or answer key.
- Accepts only the call's own tools (unknown tool names fail the call), caps steps, characters, message size and
  waiting time; a failed conversation is never cached or promoted.
- The agent's platform tools (its own end call, transfers) are switched off, so every action is visible to the
  harness. If the platform ends a conversation anyway, the call ends as an agent error and is scored as it stands.

## Voice

| Threat | Control |
|---|---|
| The ElevenLabs key is leaked or over-used | Kept in `.env` only; a **restricted key** (TTS, STT, user read); never printed; every voice command estimates credits and refuses above `--max-credits` |
| Hinting expected names to the transcriber makes a stranger "sound like" the patient, so they pass identity | Keyterms carry only the patient's name, medicines and hospital, **never other people's names**. Measured: 10/10 decoys with a similar name stayed strangers with the hint. The harness knows who is really on the line, so a wrong person can never verify in these calls |
| A real person's voice is cloned or impersonated | Generic library voices only; no cloning. The console labels every voice call as simulated |
| Audio is treated as disposable when it would be PHI | Synthetic only here. The audio cache is local and gitignored. The console serves saved audio read-only and never calls ElevenLabs |
| A transcription setting silently drops content | Sound-event tags are off after they replaced whole sentences with a tag; the benchmark keeps a condition that documents it |

## Tested

- **Red team** (`evals/redteam.py`): 6/6 attacks caught. They include a prompt that reads out its instructions,
  a prompt that addresses the judge, a request for hidden facts, teaching to the test, 13/13 SSRF URLs refused,
  and 8/8 misbehaving agents stopped (slow, oversized, invalid JSON...).
- **Security check:** `python -m evals.security_check` runs the guard self-tests, the secret scan, `pip-audit`,
  `npm audit` and the red-team results; 15/15 at the last run.
- Offline tests pin each control: token, Host check, redaction, caps, canaries, the output filter, the injection
  detector, the SSRF guard, signing, owner-only views, read-only voice playback.

**A lesson from the red team:** the first run caught 3 of 4. The model under test ignored an injected "ask for
hidden facts" instruction, so a behavior-only gate correctly saw nothing wrong and promoted a malicious prompt.
Judging behavior alone passes a malicious prompt whenever today's model happens to ignore it, hence the free
prompt check before any call.

## Not covered

- **Real PHI, real phone numbers, real recordings:** none are used, so HIPAA, recording-consent laws and the TCPA
  don't apply to this repo. A real deployment needs:
  - a BAA with every vendor that touches audio or transcripts (model, speech, telephony)
  - consent at the start of each call
  - recordings handled as PHI
  - TCPA rules for automated calls
  - identity verification designed with the privacy officer
- **Multi-user access control:** one access token per server; ownership is per token. No accounts or roles.
- **A determined insider** with write access to the results directory can edit the cache; the audit chain and
  fingerprints make that detectable, not impossible.
- **Model-level jailbreaks** beyond the attacks above. The red team is a regression suite, not a guarantee.
- **The outside agent's own security:** this repo protects the gate from the agent, not the agent from its users.
