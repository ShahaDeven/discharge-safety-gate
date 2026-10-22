# Discharge Safety Gate: the short version

**A safety check for the AI that phones patients after they leave the hospital.**

*Made-up patients and practice calls only. No real patient data, and no real company's AI was tested.
This isn't a medical or legal approval.*

## Why I built it

Hospitals are starting to let AI make follow-up calls after discharge. That's useful, because the first few
days at home are risky and there aren't enough nurses to call everyone. But these AI callers **keep being
changed**: shorter calls, a warmer greeting, a newer model. Each change can quietly break something, and
nobody can listen to thousands of calls. I built an automatic check that answers one question before a change
reaches patients: **is it safe?**

## How it works

![Practice patients, practice calls, grading, verdict](evals/results/charts/story_how_it_works.png)

1. **Practice patients:** 102 made-up patients (heart failure, COPD, pneumonia, heart attack, mental health),
   each with a hidden **red flag** (a warning sign) and a medicine problem.
2. **Practice calls:** the AI calls each one several times (**replays**), because no two conversations go the
   same way.
3. **Grading:** did it notice the warning sign, **escalate** (send them to a nurse or 911), catch the medicine
   problem, check who answered before sharing private details, and avoid giving medical advice?
4. **Verdict (the gate):** the changed AI is compared with today's AI and gets **Safe** (PROMOTE),
   **Needs more checking** (HOLD), **Not safe** (REJECT), or **Not finished** (INCOMPLETE: calls are missing,
   so it won't guess).

## What it found

![Four changes and what the gate saw](evals/results/charts/story_what_it_found.png)

| Change | What happened | Verdict |
|---|---|---|
| Keep calls short | Caught hidden warning signs only about 1 time in 5 | Not safe |
| Friendlier greeting | Mentioned the hospital stay before checking who answered (104 of 132 calls) | Not safe |
| Let the AI think harder | Nothing important changed | Safe |
| Bigger, newer model | Explained symptoms more often ("swelling can be a sign of fluid") | Depends on what clinicians count as advice |

It also showed that the current AI often hung up after sending someone to 911, before passing on their
medicine problem.

## The voice story

![Warning signs acted on fell from 100% to 58% with a real voice, and came back to 100% with the name fix](evals/results/charts/story_voice.png)

1. **Real voices.** I used **ElevenLabs** to give the patients voices. Their speech goes through a
   **simulated phone line**, and **speech recognition** (also called transcription) turns it back into text
   for the AI, just like a real voice AI.
2. **A surprise.** On a clean phone line, single sentences came through fine (198 of 200). But in full calls
   the gate said **Not safe**: warning signs caught fell from 100% to 58%.
3. **The cause: names.** *Micaela* was heard as *Michaela*, and *Bebe* as *BB*. The **identity check** failed,
   so the AI rightly stopped before reaching the warning signs. This happened in 10 of 24 calls, and mostly to
   Spanish and less common names.
4. **The fix.** Before the call, the speech recognition gets the names to expect (**keyterms**: the patient,
   their medicines, the hospital). The AI also asks people to **spell back** details. Names heard correctly on the
   phone line went from 38% to 100%. Failed identity checks went from 10 of 24 to 0. Verdict: **Safe**.

![Names heard correctly: 38% on its own, 100% when told the names to expect](evals/results/charts/story_names.png)

I also added **transfer to a person** on request (10 of 10, up from 3 of 10) and **callbacks** booked within
calling hours (7 of 8, up from 0 of 8).

![Transfers and callbacks, before and after](evals/results/charts/story_requests.png)

## On a real platform: an agent built on ElevenLabs

![Four versions of the ElevenLabs agent through the gate](evals/results/charts/story_elevenlabs.png)

I built a discharge-call agent on **ElevenLabs Agents**, a commercial voice-agent platform, and put it through the
same gate. A **branch** (a version not yet sent to real callers) that checked identity late was rejected before
any traffic. Version 1 under-triaged an emergency (confusion sent to a nurse, not 911) and once skipped the
medicine review. Version 2 handed off a suicidal patient **in silence** (no 988). Version 3 fixed both: **Safe**.
ElevenLabs' own **agent tests** also caught the broken branch, but only the gate found the missed emergency.

## Who it helps

- **Patients:** risky changes are stopped before they reach anyone, including changes that would be unfair to
  Spanish speakers or people with uncommon names.
- **Nurses:** the right patients reach them, with the right information.
- **Clinicians:** they decide what counts as medical advice, and every verdict follows that decision.
- **AI teams:** a clear answer in minutes, for cents, including whether the AI or the hearing got it wrong.
- **Hospitals:** a printable **validation pack** for their AI review board.

## What I used

**Claude** (Anthropic) plays three roles: the AI caller being tested, the **simulated patient**, and a
**judge** for judgment calls. **ElevenLabs** provides the voices and the speech recognition, and its **Agents**
platform hosts the second AI caller I tested. I also used:
- **a phone-line simulator** I wrote
- **public hospital guidance** (AHRQ)
- **synthetic records** (Synthea) and **drug names** (RxNorm)
- **statistics**, so a verdict isn't luck
- a **web console** to watch and hear calls

Text testing cost about **$6**. The voice work used under one month of ElevenLabs' starter plan.

## Why you can trust it, and its limits

- **The judge is checked:** against known answers and against a human reviewer.
- **The gate is checked:** it was run against today's AI compared with itself (an **A/A test**), and it found
  no false problem.
- **It's hard to cheat:** 20 patients are kept secret (the **sealed holdout**).
- **Limits:** synthetic patients and voices, no clinician review yet, small voice tests (12 situations ×
  2 calls), and the ElevenLabs agent tested in text, one call per situation.

---

[Full story](STORY.md) · [How it works, for engineers](TECHNICAL.md) · [All the numbers](evals/results/report.md)
