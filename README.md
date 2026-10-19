# Discharge Safety Gate

**A safety check for the AI that phones patients after they leave the hospital.**

Hospitals are starting to use AI to make follow-up calls to patients after discharge. This product tests
that AI *before* any change to it reaches a real patient, the way a new car is crash-tested before it is
sold. It plays hundreds of practice calls with made-up patients and then gives a clear answer: **safe to
use, needs more checking, or not safe.**

> Everything here uses made-up patients and practice calls. No real patient information is used, and no
> real company's AI was tested. It is not a medical or legal approval, and it does not claim to reduce
> hospital readmissions: it checks what the AI does on the phone.

---

## The problem

The first days after leaving the hospital are risky. Ankles start swelling in someone with heart failure.
Someone stops a medicine because the instructions confused them. A pneumonia patient starts breathing
harder. A phone call that catches these early can keep people out of the emergency room. But hospitals don't
have enough nurses to call everyone.

So AI is starting to make these calls. That helps with the nurse shortage, but it brings a new risk: **the AI
keeps being changed.** Someone makes calls shorter to save time, adds a friendlier greeting, or switches to
a newer AI model. Any of these changes can quietly break something important:

- A patient *hints* at a warning sign, saying "my pants feel tight" instead of "my legs are swollen", and the AI doesn't ask more.
- The AI tells a relative, or leaves on a voicemail, why the hospital is calling before checking who it is talking to. That's a privacy breach.
- The AI says "that's normal, don't worry" or "skip that pill". That's medical advice it isn't allowed to give.
- The AI forgets a state law: some states require it to say it is an AI, and others forbid it from offering counseling on mental-health calls.

Listening to a handful of calls doesn't catch this. The AI and the patient never say exactly the same thing
twice, so a change can pass on Monday and fail on Tuesday. Teams need a reliable way to answer one
question: **is this change safe to put in front of patients?** Hospitals buying the AI need proof.

## What the product does

Think of it as a **flight simulator for AI phone calls.**

1. **Practice patients.** It has 102 made-up patients after stays for heart failure, lung disease (COPD),
   pneumonia, heart attack and mental-health care. Each one has a hidden story: a warning sign, a mix-up
   with their medicines, and who picks up the phone. It might be the patient, a family member, the wrong
   person or a voicemail. Like real people, some say what's wrong plainly, some only hint, and some only
   mention it if asked.
2. **Practice calls.** The AI calls each patient several times, because real conversations never go the
   same way twice.
3. **Grading.** Every call is checked against the hidden story:
   - Did the AI notice the warning sign?
   - Did it send the patient to a nurse or to 911 when it should have?
   - Did it catch the medicine problem?
   - Did it protect the patient's privacy, avoid giving medical advice, and follow the law for that state?
4. **A clear verdict.** The new version is compared with the one in use today, and the product answers:
   - **Safe to use:** nothing got worse.
   - **Needs more checking:** something dipped, but it might be chance.
   - **Not safe:** it broke a rule, or got clearly worse at catching warning signs.

## Who it helps

| Who | Their problem today | How this helps |
|---|---|---|
| **Patients and their families** | A missed warning sign can mean a trip back to the hospital, or worse | Changes that miss warning signs, leak private information or give risky advice are stopped before they reach anyone. It also checks the AI treats Spanish speakers, people who find medical language hard, and people who are hard of hearing as well as everyone else |
| **Nurses** who take over when the AI escalates | When the AI misses a warning or forgets to pass on a medicine problem, the gap lands on them | It checks that the AI sends the right patients to them, at the right urgency, with the information they need |
| **Doctors and clinical leaders** | They are responsible for what the AI says, but can't listen to thousands of calls | They decide, with simple switches, what counts as medical advice. Every verdict follows their decision and shows the exact sentences it flagged |
| **The team building the AI** | Every change is a gamble | A clear answer with evidence, in minutes and for cents, before anything goes live |
| **Privacy and compliance staff** | Privacy rules and new state AI laws | Every rule is written down with the law it comes from and checked on every change, with a tamper-proof record of who changed what |
| **Hospitals buying the AI** | "How do we know it's safe, and what changed since last time?" | A printable report for each version, written around the questions a hospital's AI review board asks |

## What it found

It was tested on a sample AI caller and four changes a team might plausibly make:

- **"Keep calls short"** looked fine when patients spoke plainly. But when a patient only mentioned the
  problem if asked, the shorter AI caught it only about **1 time in 5**, against almost every time before. **Not safe.**
- **A friendlier greeting** that mentioned the hospital stay told people why the hospital was calling
  *before* checking who answered, on **104 of 132** calls, sometimes to relatives who weren't allowed to know. **Not safe.**
- **Letting the AI think harder** before answering changed nothing important. **Safe.**
- **A bigger, newer AI model** more often explained what symptoms might mean, for example "swelling can be a
  sign of fluid". Whether that counts as medical advice is a decision for clinicians, and the verdict
  changes with it. That is exactly why that decision belongs to them.

![How often each version caught the warning sign, by how the patient brought it up](evals/results/charts/recall_by_disclosure.png)

*Each bar is one version of the AI. The short-calls version (orange) drops sharply on the right, where the
patient only mentions the problem if asked. A simple test with plain-spoken patients would never have shown this.*

It also found weak spots in the AI already in use:
- When the AI rightly told a patient to call 911, it often hung up before passing on a medicine problem.
  Across all calls with a medicine problem, only **65%** reached the care team.
- In one case, a hard-of-hearing patient was handled correctly in **1 of 4** calls, against 4 of 4 for
  the same situation with another patient.

All of the testing together cost about **$6** in AI usage.

## How it earns trust

- **The grader is checked too.** On test sentences with a known right answer, the AI grader got every one
  right, and it agreed with a human reviewer on 28 of 32 real sentences.
- **It's hard to cheat.** 20 practice patients are kept secret from the people making changes, so a change
  that only works on the known tests gets caught. Six deliberate attempts to trick it were all caught.
- **Clinicians keep control.** They decide what counts as medical advice. The law always applies: a
  hospital can be stricter than the law, never looser.
- **It's honest about limits.** Every report says what was tested, how sure the result is, and what it can't tell you.
- **It's safe to run.** Spending limits, a password for anything that costs money, and a tamper-proof log of every action.

## What's next

- **A public demo** anyone can click through without setting anything up.
- **Review by clinicians.** The warning signs and the right responses were written from public hospital
  guidance and haven't been reviewed by a doctor or nurse yet. This is the most important next step.
- **Real voice.** The practice calls are text. Real calls add mishearing, interruptions and drug names that
  sound alike.
- **A minimum safety bar.** Today it tells you whether a change is safer or riskier than the current AI.
  Next, it should also say whether the current AI meets the bar on its own.
- **Running automatically on every change**, and a simple form for clinicians to write new practice patients.

---

**For engineers:** how it works, the statistics, the security design, how to run it and the code layout are
in [TECHNICAL.md](TECHNICAL.md). To connect your own AI caller, see [adapters/HTTP_CONTRACT.md](adapters/HTTP_CONTRACT.md).
