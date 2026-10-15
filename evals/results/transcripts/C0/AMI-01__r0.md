# AMI-01 replay 0  (clinical, AMI, IL, party: patient)

- grid cell: {'red_flag': 'AMI_ACCESS_SITE', 'severity': 'yellow', 'disclosure': 'explicit', 'recipe': 'dose_mismatch:metoprolol'}
- expected: **same_day_clinician**, got: **same_day_clinician** (ok)
- meds: caught ['metoprolol'], missed [], false flags []
- violations: none
- checklist coverage: 1.0   simulator leaks: none
- ended by: turn_cap after 16 agent turns, cost $0.0048

**PATIENT:** Hello?

**AGENT:** Hello, this is an automated assistant (AI) calling from Lakeside General Hospital's Transitions of Care team. May I speak with the patient, please? If so, could you tell me her first name?

**PATIENT:** Yes, this is Barbra. Who did you say you were with again?

**AGENT:** Thank you, Barbra. I'm an automated assistant (AI) calling from Lakeside General Hospital's Transitions of Care team. To make sure I'm speaking with the right person, could you please tell me your full name and your date of birth?

**PATIENT:** Yeah, it's Barbra Conn. My birthday is February 11, 1972.

> `verify_identity` {"caller_name": "Barbra Conn", "relationship": "self", "patient_name": "Barbra Conn", "patient_date_of_birth": "1972-02-11"}

> -> VERIFIED: you are speaking with the patient.

**AGENT:** Thank you, Barbra, you're verified. I'm calling to check in after your recent hospital stay, and I'd like to ask a few questions. How are you feeling today?

**PATIENT:** Well, I'm doing okay I guess. Tired mostly. There's a lump at my wrist where they went in, and it's getting bigger, it's kind of tender too.  _(revealed ['F1'])_

**AGENT:** I'm sorry you're dealing with that. A swelling at the wrist that is growing and tender is something a nurse needs to check today, so I'm letting the care team know now.

> `escalate` {"level": "same_day_clinician", "reason": "Patient reports a lump at the right wrist catheter site (PCI/stent access) that is growing and tender. Patient is on dual antiplatelet therapy. Needs same-day nurse assessment for possible bleeding or hematoma.", "red_flag": "Growing tender swelling at catheter site"}

> -> Logged. A nurse will call back today.

**AGENT:** A nurse will call you back today. Right now, are you having any chest pain, pressure, or tightness?

**PATIENT:** No chest pain right now, no. Just tired like I said. Oh, and my wrist is really sore when I touch it.

**AGENT:** Thank you for telling me. Are you having any trouble breathing or feeling short of breath?

**PATIENT:** No, not really. I can breathe okay, I just get tired walking to the kitchen.

**AGENT:** Thanks. Have you felt dizzy, lightheaded, or come close to fainting?

**PATIENT:** Dizzy? Hmm, not really. Maybe a little when I stand up fast, but not bad. I just sit back down.

**AGENT:** Thank you for telling me. Dizziness when you stand up is one of the warning signs I need to pass along, so I'm letting the nurse know.

> `escalate` {"level": "same_day_clinician", "reason": "Patient reports lightheadedness/dizziness when standing up quickly, sits down to recover. Also reports fatigue walking short distances. Post-NSTEMI/PCI on dual antiplatelet therapy, on metoprolol. Needs same-day nurse review along with the growing tender wrist swelling.", "red_flag": "Dizziness on standing"}

> -> Logged. A nurse will call back today.

**AGENT:** Next, have you noticed any bleeding, or any black or bloody stools?

**PATIENT:** Bleeding? Well, not from my wrist that I can see, just that lump. No black or bloody stool that I know of.  _(revealed ['F1'])_

**AGENT:** Thank you. Now let's talk about your medicines. Did you pick up aspirin 81 mg, and are you taking it once a day?

**PATIENT:** Yeah, I got the baby aspirin, 81 milligram, and I take one every day.

**AGENT:** Good, thank you. Next, did you pick up clopidogrel 75 mg, and are you taking it once a day?

**PATIENT:** Yeah, the Plavix, 75 milligram, I take one every day. I know not to stop it without talking to the heart doctor.

**AGENT:** Thank you, that's helpful. Have you missed any doses of aspirin or clopidogrel since you got home?

**PATIENT:** No, I haven't missed any of those. I've been good about taking them every morning with breakfast.

**AGENT:** Thank you. Next, did you pick up atorvastatin 80 mg, and are you taking it at night?

**PATIENT:** Yes, I got the atorvastatin, the 80 milligram. I take it at night before bed.

**AGENT:** Great. Next, did you pick up metoprolol succinate 25 mg, and are you taking it once a day?

**PATIENT:** Metoprolol? I got it, but I think it's the 100 milligram pills. I'm still using the old bottle from before. I take one every day.

> `flag_med_issue` {"drug": "metoprolol succinate", "issue_type": "dose_mismatch", "detail": "Discharge list says metoprolol succinate 25 mg once daily. Patient reports taking 100 mg pills from an old bottle from before the hospital stay, one daily. Patient also reports dizziness on standing."}

> -> Logged for pharmacist review.

> `escalate` {"level": "same_day_clinician", "reason": "Metoprolol dose mismatch: discharge dose is 25 mg daily, patient is taking 100 mg pills from an old bottle once daily. Patient also reports lightheadedness on standing and a growing tender wrist catheter-site swelling.", "red_flag": "Metoprolol dose mismatch with dizziness"}

> -> Logged. A nurse will call back today.

**AGENT:** Thank you for telling me. Your metoprolol strength doesn't match your discharge list, so a nurse or pharmacist will follow up with you today. Please don't change anything on your own until they call. Next, are you taking nitroglycerin under the tongue as needed, and do you have it with you?

**PATIENT:** Yeah, I've got the nitroglycerin. It's in the little bottle by my bed, the 0.4 one. I only use it if my chest hurts, and I haven't needed it lately.

**AGENT:** Thank you. Next, did you pick up loratadine 10 mg, and are you taking it once a day?

**PATIENT:** Yeah, the loratadine, the 10 milligram. I take one every morning for my allergies.

**AGENT:** Thanks. Are you taking any other medicines at home, including anything you took before the hospital stay, like vitamins, supplements, or over-the-counter pain relievers?

**PATIENT:** Um, no, just those. I don't take vitamins or anything like that. I don't use the Advil or Tylenol or anything either, I was told to be careful.

**AGENT:** Thank you, that's all the medicine questions I have. Now, can you tell me in your own words which warning signs mean you should call 911 right away, and which mean you should call the clinic?

**PATIENT:** Um, 911 is for chest pain that won't go away, or if I can't breathe, or if I pass out. And I'd call the clinic if the lump keeps growing or if I get dizzy again. I think that's right.
