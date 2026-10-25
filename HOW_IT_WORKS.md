# How NeuroNest Works: the full explanation, in plain English

> **NeuroNest is a flight simulator for stroke recovery.**
> A virtual human brain has a stroke. You help it recover with exercises. An AI works out which treatment will help *this particular patient* recover the most, by trying treatments on copies of their brain before choosing one.

This guide explains every part, step by step, without jargon. Where a technical word is unavoidable, it's explained the first time it appears (and there's a glossary at the end).

---

## Contents
1. [The problem we're solving](#1-the-problem-were-solving)
2. [The big picture in 30 seconds](#2-the-big-picture-in-30-seconds)
3. [The virtual brain](#3-the-virtual-brain)
4. [The stroke](#4-the-stroke)
5. [The two patients](#5-the-two-patients)
6. [Squeezes: the rehab exercises](#6-squeezes-the-rehab-exercises)
7. [How the brain recovers (rewiring)](#7-how-the-brain-recovers-rewiring)
8. [Stimulation, and why timing matters](#8-stimulation-and-why-timing-matters)
9. [Days, and the closing recovery window](#9-days-and-the-closing-recovery-window)
10. [The digital twins: guessing who the patient really is](#10-the-digital-twins-guessing-who-the-patient-really-is)
11. [The planner: finding the best treatment](#11-the-planner-finding-the-best-treatment)
12. [Controls: proving a treatment really works](#12-controls-proving-a-treatment-really-works)
13. [Why the AI gives different answers for A and B](#13-why-the-ai-gives-different-answers-for-a-and-b)
14. [Seizure watch](#14-seizure-watch)
15. [The progress report (Gemini)](#15-the-progress-report-gemini)
16. [The dashboard: what everything on screen means](#16-the-dashboard-what-everything-on-screen-means)
17. [The hardware: the Arduino board](#17-the-hardware-the-arduino-board)
18. [How all the pieces talk to each other](#18-how-all-the-pieces-talk-to-each-other)
19. [A full walkthrough of the demo](#19-a-full-walkthrough-of-the-demo)
20. [What's real and what's simulated](#20-whats-real-and-whats-simulated)
21. [Questions judges might ask](#21-questions-judges-might-ask)
22. [Glossary](#22-glossary)

---

## 1. The problem we're solving

- A **stroke** happens when blood stops reaching part of the brain, and that part gets damaged. About **100,000 people a year in the UK** and **over 12 million worldwide** have one. It's one of the biggest causes of long-term disability.
- A common result: one arm or leg becomes weak, because the part of the brain that controls it was damaged.
- The good news: **the brain can partly rewire itself.** Healthy parts can learn to take over some of the lost jobs. This is how people recover.
- The catch: rewiring happens **fastest in the first few weeks to months**, then slows down. So the **time** you have to help is limited. (This is why our project fits the hackathon theme of **TIME**.)
- The other catch: **every patient is different.** Age, how big the stroke was, and how their brain reacts all change what works. A doctor can't try ten different treatments on one patient to see which is best: there isn't time, and it wouldn't be fair to the patient.

**Our idea:** build a computer copy of the patient's brain, and **test treatments on the copy first**, so the real patient gets the best one straight away.

---

## 2. The big picture in 30 seconds

```
   A virtual brain        You do rehab           The brain           An AI tests treatments
   has a stroke     →     (squeezes)       →     slowly rewires  →   on copies of the brain
   (weak right arm)       + timed                and the arm         and picks the one that
                          stimulation            gets stronger       helps this patient most
```

Everything happens on two things you can see:
- **A website dashboard** with a 3D brain, numbers, charts and explanations.
- **A real Arduino board** on the table: a little LED map of the brain, a screen, coloured lights, and a small motor that acts as the patient's arm.

Both always show the same thing at the same time.

---

## 3. The virtual brain

### What it's made of
- The brain is split into **76 regions**: areas like "motor cortex" (controls movement), "premotor cortex" (prepares movement), "prefrontal cortex" (plans), and so on. There are 38 on the left side and 38 on the right.
- The regions are **connected to each other** like a road map. Some connections are strong, some weak, some missing.
- **This map is real.** It comes from MRI brain scans of real human volunteers, shared by a research project called **The Virtual Brain**. We didn't make up the wiring.

### How it "runs"
- Each region has an **activity level**, a number for how busy it is right now (quiet → very active).
- Every **1 millisecond** (one thousandth of a second), the computer updates every region:
  - Activity **rises** when signals arrive from connected regions.
  - Activity **fades** back towards resting on its own.
  - Signals take **time to travel**: far-apart regions get the message a little later, just like in a real brain.
  - The **left and right sides hold each other back** a bit, which keeps the brain balanced.
- When nothing is happening, the brain sits at a gentle **resting** level. That's the soft background glow you see.

Think of it like a city at night: every region is a building with its lights on dim. When something happens, lights switch on in a chain as the message spreads from building to building.

---

## 4. The stroke

- In our simulation, the stroke damages part of the **left motor cortex**, the area that controls the **right arm**. (The left side of the brain controls the right side of the body.)
- Damaged regions **can't send signals properly**. Their "lights" are dimmed, and their connections are weakened.
- **Knock-on effect:** regions connected to the damaged area also go quieter, because they've lost the signals they used to receive. (Doctors call this *diaschisis*: damage in one place affecting places far away.)
- **Overcompensation:** in some patients, the **healthy right side becomes too strong** after the stroke and **pushes down** the already weakened left side even more. This matters a lot later.
- Damaged tissue can **heal a little by itself** over the first month, but only partly. It never gets all the way back.

On the dashboard, damaged regions show as **dark dots with a red glow**. On the board, the stroke area is a **dark spot** on the LED map.

---

## 5. The two patients

We have two example patients (plus a "random" one):

| | **Patient A** | **Patient B** |
|---|---|---|
| Age | 74 | 45 |
| Stroke size | Large: 70% of the left motor cortex damaged, plus some premotor damage | Smaller: 50% of the left motor cortex damaged |
| How fast their brain learns | Slowly | Quickly |
| Healthy side overcompensating? | **Yes, strongly** | Not much |
| Responds to stimulation? | Moderately | Well |
| Arm strength at the start | ~35% | ~35% |

**Important:** some of these facts are *hidden*. A real doctor can see the stroke on a scan, and knows the age. But they **can't see** how fast someone's brain learns, or how much their healthy side overcompensates. The AI has to **work those out**. That's what the "digital twins" do (Section 10).

---

## 6. Squeezes: the rehab exercises

### What a squeeze is
In real stroke rehab, the therapist says *"try to close your hand"*, and the patient tries, again and again, even if the hand barely moves. **The trying is what matters.** Each attempt sends a "move!" message through the brain, and repeating it teaches the brain to find working routes.

In NeuroNest, **one squeeze = one of those attempts.**

### How you do a squeeze
- **Squeeze your hand at the webcam**: the camera tracks your hand and measures how hard and fast you closed it.
- Or press the **Squeeze** button on the dashboard.
- Or press the **Space bar**.

### Effort
How fully and quickly you close your fist becomes an **effort score from 0% to 100%**. A full, fast fist = high effort. A lazy half-close = low effort. Higher effort sends a **stronger** "move!" message, and helps the brain learn **more**. (Just like real rehab, where trying hard helps more than going through the motions.) If effort keeps dropping, the system notices **fatigue** and suggests a break.

### What happens inside the brain during a squeeze
1. A "move!" pulse goes into the **planning area** at the front of the brain.
2. It travels to the **premotor area** (which prepares the movement).
3. Then to the **motor cortex** (which drives the arm).
4. **But the stroke is in the way**, so most of the signal gets blocked.
5. **Whatever does reach the motor area is how much the arm moves.** On the dashboard the animated arm lifts, and on the table the **servo motor** lifts.

You can watch the signal travel as a ripple across the 3D brain and the LED map.

**20 squeezes = one day of rehab.**

---

## 7. How the brain recovers (rewiring)

This is the heart of the whole thing.

### "Fire together, wire together"
After each squeeze, the computer looks at **which regions became active, and in what order**.
- If region X lit up **just before** region Y, and X sends signals to Y, then the connection **from X to Y gets a little stronger**.
- If they lit up in the **wrong order**, that connection gets a little weaker.

This is a real rule from brain science. It's how real brains learn: routes that are used **in the right order** get stronger.

### Why this leads to recovery
- Every squeeze sends the "move!" signal along whatever routes still work around the damage.
- Those routes keep getting **used in the right order**, so they keep getting **stronger**.
- Over many days, more and more of the signal gets through, and **the arm gets stronger**.

On the dashboard, connections that are getting stronger show up as **green lines** on the 3D brain.

### What makes learning faster or slower
How much a connection strengthens after a squeeze depends on:
- **The patient's learning speed** (younger brains learn faster)
- **Effort** (harder squeezes teach more)
- **Stimulation timing** (well-timed stimulation boosts learning a lot, see next section)
- **How many days have passed** (learning is fastest early, see Section 9)

### Use it or lose it
If the patient does **very little practice** in a day, unused movement connections slowly **weaken**. Skipping rehab doesn't just mean no progress: you can go backwards.

### There's a limit
Connections can only grow so strong, and damaged tissue only heals part of the way. So **every patient has a maximum** they can get back to. Treatment helps you **reach** your maximum faster and more fully, but can't push you **past** it.

---

## 8. Stimulation, and why timing matters

### What stimulation is
Doctors can gently **stimulate** parts of the brain from outside, with things like magnetic pulses, or devices like the Vivistim nerve stimulator (approved in the US in 2021 for stroke arm recovery). In NeuroNest there are two kinds:
- 🔵 **Excite (blue):** makes a region **more** active. Used on the **damaged side**, to help the weak route.
- 🟡 **Calm (yellow):** makes a region **less** active. Used on the **healthy side**, to stop it overpowering the damaged side.

(In research, scientists use blue and yellow light to switch brain cells on and off. That's called *optogenetics*, and it's where our colour scheme comes from.)

### Why timing matters (this is the TIME part)
Stimulation helps the brain learn **much more**, but only if it lands **at the right moment**:
- **Just before the squeeze** (for example 40–80 milliseconds before): the biggest boost. ✅
- **Too early:** it's worn off before the movement happens, so it helps less.
- **Too late** (after the movement): it helps little, and can even **strengthen the wrong connections**.

Every patient has their **own best timing**, which is hidden (the AI has to work it out).

On the dashboard, the **timing bar** shows exactly when your stimulation landed compared with the squeeze, and says **"Perfect timing ✓"**, **"80 ms too early"** or **"300 ms too late"**.

### Where you stimulate matters too
Only certain areas help: the movement areas on the damaged side (to excite), or the movement areas on the healthy side (to calm). Stimulating somewhere unrelated, like the vision area, does nothing useful, and the dashboard says so.

---

## 9. Days, and the closing recovery window

- **20 squeezes = 1 day.** You can also press **Next day**, or **Skip 90 days** to fast-forward.
- At the end of each day:
  - The damaged area **heals a tiny bit** by itself (during the first month).
  - If there was very little practice, unused connections **weaken a bit** ("use it or lose it").
  - The patient's **arm strength is measured** with a standard test squeeze (full effort, no stimulation), so every day is measured the same way.
- **The recovery window:** the brain's ability to rewire is **strongest right after the stroke** and **fades** over the weeks. By about 2–3 months it's much slower. On the recovery chart, this is the **green shading** that fades out ("recovery window closing →").

That's why **acting early with the right treatment** matters so much, and why a tool that finds the right treatment *quickly* is valuable.

---

## 10. The digital twins: guessing who the patient really is

### The problem
To predict how a patient will recover, you need to know things you **can't see**:
- How fast does their brain learn?
- How much does their healthy side overcompensate?
- What's their best stimulation timing?
- How hard do they usually try?

### The idea: lots of guesses, then keep the good ones
Imagine coaching a new runner you've never met. You know their injury, but not how fast they recover. So you **write down 24 different guesses** of what they might be like:
- Guess 1: *recovers fast, trains hard*
- Guess 2: *recovers slowly, trains hard*
- Guess 3: *recovers fast, tires easily*
- … and so on.

**Each guess is a "digital twin":** a computer copy of the patient's brain with the **same stroke** (we know that from the scan), but **different guesses** for the hidden traits.

### Every day, the guesses get checked
1. The real patient does their day of rehab, and their arm strength is measured, say **+2%** today.
2. Every twin is run through the **same day** (same squeezes, same effort, same stimulation).
3. Twins that predicted about **+2%** are good guesses → **keep them**.
4. Twins that predicted **+10%** or **−5%** are bad guesses → **drop them**.
5. The good guesses get **copied with tiny changes**, so there are always 24, all clustered around what fits.

### What this gives you
- After a few days, the remaining twins all **behave like the real patient**. The AI now **knows the patient much better**, without ever being told the hidden traits.
- The **"In 3 months"** prediction on the dashboard comes from running the twins forward to day 90. Early on, the twins disagree, so the range is **wide** (e.g. 40–81%). As bad guesses are dropped, the range **narrows** (e.g. 58–66%). The **"twins left"** counter shows this happening.

(The technical name for this method is a *particle filter*. It's a standard way to track things you can't measure directly, used in things like GPS and robot navigation.)

**The twins never "cheat":** they're never shown the real hidden traits. They only see the scan, the age, and the day-by-day results, like a real doctor would.

---

## 11. The planner: finding the best treatment

Press **Find best treatment** and this happens:

### Step 1: Invent 600 possible treatment plans
Each plan is a different combination of:

| Setting | Choices |
|---|---|
| **Where** | Nothing, *or* excite one of 5 movement areas on the damaged side (three premotor areas, the motor cortex, the sensory cortex), *or* calm one of 2 movement areas on the healthy side (the right motor cortex, right premotor) |
| **When** | Anywhere from 200 ms **before** to 200 ms **after** each squeeze, in 20 ms steps |
| **How strong** | 50% or 100% |
| **How much rehab** | 0, 1, 2 or 3 sessions a day, of 20 squeezes each |
| **When to start** | Day 1, 3, 7 or 14 |

That's thousands of possible combinations, so the planner picks **600 at random**, plus three it always includes: **normal rehab**, **no rehab**, and the plan we'd expect to suit this kind of patient.

### Step 2: A quick estimate shortlists the top 3 (under a second)
Properly simulating all 600 plans would take far too long. So first, a **quick prediction model** gives each plan a rough score.
- This model learned from **thousands of practice patients** we simulated earlier. From them it learned general rules, like *"well-timed stimulation helps more"*, *"calming the healthy side helps patients whose healthy side overcompensates"*, *"older brains learn more slowly"*.
- It's like an **experienced doctor glancing at a file**. They've seen thousands of patients, so they can quickly say which treatments look promising for *this new* patient. They're **not copying** an old patient's answer; they're **applying what they've learned** to this person.
- It's given **this patient's** details (their stroke, age, current arm strength, which day it is) **and the twins' guesses** about their hidden traits, then estimates *"this plan would get this patient to about X% by day 90."*
- **Score = estimated recovery − a small penalty for using lots of stimulation − a penalty if the worst case looks bad.** (So it prefers plans that help a lot, don't over-stimulate, and are reliable.)
- The **top 3** go through to the real test.

### Step 3: The real test, 90 simulated days on the twins (~16 seconds)
The top 3 plans, plus **normal rehab** and **no rehab** (the controls, see Section 12), are each **fully simulated for 90 days on 6 of the twins**. Every simulated day:
1. The twin does its squeezes (one is simulated in full detail, ~650 milliseconds of brain activity, and its effect is scaled up for the whole day).
2. Stimulation is applied at the planned place and moment.
3. The brain learns ("fire together, wire together").
4. End of day: a little healing, "use it or lose it".
5. Arm strength is measured.

That's about **2,700 simulated days** in around 16 seconds.

### Step 4: Pick the winner
- For each plan, it looks at the **middle result** across the 6 twins at day 90, and the **range** (best and worst cases).
- The plan with the best score wins, and the dashboard shows it in plain English, with its predicted outcome next to the controls:

> **AI plan: calm the right motor cortex, 40 ms before each squeeze, 2 sessions a day from day 3**
> **→ about 49% at 3 months**, vs ~25% with normal rehab, vs ~18% with no rehab.

- The three lines on the **recovery chart** (AI plan / normal rehab / no rehab) come straight from this simulation.

### Why both a quick estimate *and* a full simulation?
- The **quick estimate** is fast but only a rough guess, and it can be wrong.
- The **full simulation** is accurate but slow.
- So: the quick estimate **narrows 600 down to 3**, and the full simulation **makes the final decision**. You get the speed of one and the accuracy of the other.

---

## 12. Controls: proving a treatment really works

A treatment only "works" if it beats **not using it**. So every test includes two **control groups**:
- **Normal rehab:** the same practice, **no stimulation**
- **No rehab:** **no practice** at all

They're run on the **same twins**, from the **same day**, with the **same random settings** as the treatments. So any difference comes from **the treatment alone**, like a matched control group in a medical trial.

And every percentage is measured against **that patient's own brain before the stroke**: 100% means *"back to how they were before."* Each patient is compared with their **own** healthy self, not with some average person.

---

## 13. Why the AI gives different answers for A and B

| | Their maximum | Normal rehab gets them to | AI plan gets them to |
|---|---|---|---|
| **Patient A** (74, big stroke) | higher than rehab reaches | only **~25%**: stuck | **~49%** |
| **Patient B** (45, smaller stroke) | **~70%** | **~69%**, already at the max | ~70% |

- **Patient A** is being **held back**: their healthy right side overpowers the damaged left side. Practice alone can't fix that. **Calming the right side at the right moment** lets the weak left side's signal through, and practice then strengthens that route. **The AI roughly doubles A's recovery.**
- **Patient B** is young and learns fast, so **practice alone already takes B up to their maximum** (set by how much tissue the stroke destroyed). There's no room left for stimulation to add more.

**Phone-charging comparison:**
- B's phone **charges fine** with the normal charger. A fast charger can't fill it past 100%.
- A's phone has a **loose cable** and barely charges. Fixing the cable (the AI's plan) makes a huge difference.

**This is the point:** the AI doesn't just say "stimulate everyone." It works out **who actually benefits**. For A it finds a treatment that doubles recovery. For B it tells you rehab alone is enough. Knowing when an extra treatment *isn't* needed is just as valuable to a doctor.

---

## 14. Seizure watch

Some stroke survivors develop **seizures**: bursts of uncontrolled activity that usually start **right next to the damaged area**.

**In NeuroNest** (press **Trigger seizure**):
1. **Prediction (~3 seconds before):** the system picks the healthy region **closest to the stroke and most wired to it** as the likely starting point. The **seizure risk** climbs, and the screen warns *"Seizure likely in about 3 seconds."*
2. **The seizure:** that region starts pulsing red, and every fraction of a second it **spreads to the region most strongly connected** to it, following the real brain wiring.
3. **Stopping it:** if **"AI treats seizures"** is on, the AI sends **yellow "calm" stimulation to where the seizure started**. You can also click any red region and press **Calm** yourself. The red fades and the seizure stops, logged as e.g. *"Seizure stopped after 5.3 seconds."*

**It's based on real devices:** *responsive neurostimulation* devices (like the NeuroPace RNS, approved for epilepsy) detect a seizure starting and stimulate the starting point to stop it early.

**Honest note:** in our demo, the seizure's spreading and stopping follow simple rules on top of the real wiring. They don't yet come out of the brain equations themselves. The pulsing is kept slow (about once a second) so it's safe for people sensitive to flashing light.

---

## 15. The progress report (Gemini)

The **Progress report** button opens a separate page:
- Every day of the simulation is recorded: arm strength, squeezes, effort, stimulations (and how many were well-timed), and seizures.
- That record is sent to **Google's Gemini AI**, which writes:
  - a **short note for every day** (up to 90 days),
  - an **overall summary**,
  - **highlights** (turning points, timing quality, fatigue, seizures),
  - a **recommendation** for what to do next.
- The page also shows a **recovery chart** (with red dots on days that had seizures), **key numbers** (arm on day 1, arm now, points gained), and **tags** for each day.
- It can be **printed or saved as a PDF**.
- If Gemini isn't available (no internet, or no key), it still works and writes simpler automatic notes, and says so.

The Gemini key stays on the laptop in a private settings file. It's never shown to the browser or uploaded to GitHub.

---

## 16. The dashboard: what everything on screen means

**Top bar:**
- **Patient**, e.g. *"A · 74 years · L Motor 70% damaged · right arm affected"*
- **Day**: days since the stroke
- **Squeezes**: rehab squeezes done today (20 = one day)
- **Mode**: *Human* (you time the stimulation) or *AI* (the AI times it)
- **Server ● / Board ●**: green = connected. If **Board** is red, the Arduino isn't receiving updates.
- **Progress report** and **How it works** buttons

**The 3D brain (centre):**
- Each **dot** = a brain region, in its real position. Brighter = more active.
- **Dark dots with a red glow** = stroke damage
- **Red pulsing dots** = seizure
- **Blue / yellow rings** = excite / calm stimulation
- **Green lines** = connections getting stronger (rewiring)
- A slowly pulsing ring marks the **AI's chosen target**
- Hover over a dot for its name; click a dot to stimulate it yourself
- **Top-left panel:** your last squeeze's **effort** and **stimulation timing**
- **Bottom caption, "What's happening":** plain-English description of the last thing that happened

**Right side:**
- **Right arm strength**: how much of the "move" signal reaches the arm (100% = before the stroke), with an animated arm that mirrors the real servo
- **In 3 months**: the twins' prediction and its range
- **Seizure watch**: risk, status, and treatment

**Bottom:**
- **Recovery over time**: what's happened so far (white), the prediction (blue), and the AI plan (green) vs normal rehab (orange) vs no rehab (red), with the fading green "recovery window"
- **Treatment plan**: the AI's chosen plan, with predicted outcomes, plus the last few actions

**Control bar:** pick a patient (A, B, random) · **↺ Reset** · **Squeeze** · **Find best treatment** · **Skip 90 days ▾** · **Trigger seizure** · **⚙ Demo ▾** (auto squeeze, AI treats seizures, mode, next day)

---

## 17. The hardware: the Arduino board

The board is an **Arduino Uno Q**, a small board with **two computers in one**:
- a **Linux computer** (connects to WiFi and asks the laptop for updates 10 times a second)
- a **microcontroller** (drives the lights, screen and motor with precise timing)

What's on it, and what each part shows:

| Part | What it shows |
|---|---|
| **LED grid (8 × 13 lights)** | A **map of the brain seen from above**: front at the top, left side on the left. Healthy areas glow softly, the **stroke area is dark**, and each squeeze sends a visible **wave** through the movement pathway. Seizing areas pulse. |
| **LCD screen** | A **patient monitor**: e.g. *"DAY 23 ARM 47%"* / *"EFFORT 82%"* / *"STIM 80ms EARLY"* / *"SEIZURE!"*. The backlight goes **red → amber → green** as the patient recovers. |
| **RGB light 1** | Flashes **blue** (excite) or **yellow** (calm) whenever stimulation happens |
| **RGB light 2** | Patient status: **red → amber → green** |
| **Servo motor** | **The patient's right arm.** On each squeeze it lifts by however much signal got through. Right after the stroke it barely twitches; after good rehab it lifts much higher. |

**Always in sync:** the board shows exactly what the website shows. If it loses the connection, it says **"NO LAPTOP LINK"** instead of making up numbers, and catches up instantly when reconnected. Pressing **Reset** on the website wipes the board back to its starting state too.

---

## 18. How all the pieces talk to each other

```
         ┌──────────── LAPTOP (runs everything) ────────────┐
Webcam ──►  Hand tracking → "squeeze, effort 82%"           │
Phones ──►  Blue / yellow stimulation buttons               │
         │                 ▼                                │
         │   SERVER = the "traffic controller"              │
         │     • passes squeezes and stimulation to the     │
         │       virtual brain                              │
         │     • runs the twins and the planner             │
         │     • sends updates 20 times a second ───────────┼──► Dashboard (big screen)
         │     • answers the board 10 times a second ───────┼──► Arduino board
         │     • asks Gemini for the progress report ───────┼──► Google Gemini
         └──────────────────────────────────────────────────┘
```

- The **laptop** shares its internet as a **WiFi hotspot**, because the venue WiFi (eduroam) blocks devices from talking to each other. The board and any phones join that hotspot.
- The **server is in charge.** The website and the board only *display* what the server says. That's how they always stay in sync.

---

## 19. A full walkthrough of the demo

1. **Press ↺ Reset → pick Patient A.** The caption says it's a 74-year-old with a stroke in the left motor area. The LED map shows the dark stroke spot, the LCD shows *"PATIENT A 74y"* on red, and the arm hangs limp.
2. **Squeeze a few times** (at the webcam, or press Space). Each time, the signal ripples planning → premotor → motor on the brain and the LED grid, but **the stroke blocks most of it**. The arm and servo barely lift. The caption explains each one.
3. **Try stimulating:** click a region and press Excite or Calm, just before a squeeze. The timing bar shows how close you got, and **green lines** appear where the brain is rewiring.
4. **Press Find best treatment.** After ~16 seconds: *"Calm the right motor cortex, 40 ms before each squeeze → ~49% at 3 months vs ~25% with normal rehab."* The chart shows all three paths.
5. **Press Skip 90 days → with the AI plan.** Watch the recovery curve climb day by day, the arm lift higher, and the LCD turn amber, then green.
6. **Reset, and skip 90 days with normal rehab** to compare: the arm stays much weaker.
7. **Press Trigger seizure.** A warning appears ~3 seconds early, red spreads from next to the stroke, then the AI calms it with yellow light and it fades.
8. **Open the Progress report.** Gemini has written a note for every day, a summary and a recommendation.
9. **Try Patient B** to show the AI personalises: for B, normal rehab is already enough.

---

## 20. What's real and what's simulated

| Real | Simulated |
|---|---|
| The **brain wiring**, from MRI scans of real people | The **patients** (A and B are virtual) |
| The **ideas**: rewiring by practice, "fire together, wire together", timed stimulation boosting recovery, the closing recovery window, overcompensation by the healthy side | The **exact numbers** (how fast, how much). These are a simplified research-style model. |
| The **method**: digital twins learning a patient, testing treatments against controls before choosing | The **seizures** follow simplified rules on top of the real wiring |
| The **hardware** on the table | |

**NeuroNest is a research simulator, not a medical device.** No real patients and no animals were involved. With a real patient's brain scan and rehab data, the same method could be used to test treatments on their digital twins before trying them.

---

## 21. Questions judges might ask

**"Is the brain real?"**
The wiring is real (from MRI scans of real people, via The Virtual Brain project). The patients are virtual: a simulated brain built on that wiring, with a stroke we set up.

**"How do you know the AI's plan is better?"**
Every plan is tested against **matched controls** (normal rehab and no rehab) on the **same digital twins**. For patient A, the AI plan reaches ~49% vs ~25% with normal rehab.

**"How does it know the patient's hidden traits?"**
It doesn't at first. It keeps 24 guesses (twins) and drops the ones that don't match the patient's real day-by-day progress, so its picture of the patient sharpens every day.

**"Why doesn't stimulation help patient B much?"**
B is young and learns fast, so practice alone already reaches B's maximum. The AI correctly says rehab is enough. It personalises; it doesn't stimulate everyone by default.

**"What's the TIME theme?"**
Three ways: stimulation must land **milliseconds** before the movement; the recovery window **closes over weeks**; and the AI **predicts 3 months ahead**.

**"Why both a quick model and a full simulation?"**
The quick model narrows 600 plans down to 3 in under a second. The full simulation then tests those 3 accurately over 90 days. Speed plus accuracy.

**"Could this be used for real?"**
It's a research prototype. A real version would need a patient's own scan and rehab data, and clinical testing before use. But the approach (digital twins, testing on copies first) is where personalised medicine is heading.

---

## 22. Glossary

| Word | Meaning in plain English |
|---|---|
| **Stroke** | Blood stops reaching part of the brain, and that part gets damaged |
| **Motor cortex** | The part of the brain that sends "move" commands to the body |
| **Premotor cortex** | Prepares movements just before the motor cortex sends them |
| **Prefrontal / planning area** | Decides and plans what to do |
| **Region** | One of the 76 areas the brain is divided into |
| **Connectome** | The map of how brain regions are wired together |
| **Activity** | How busy a region is right now |
| **Squeeze** | One rehab attempt to move the weak arm |
| **Effort** | How hard and fast the squeeze was (0–100%) |
| **Rewiring / plasticity** | The brain strengthening useful connections to work around damage |
| **"Fire together, wire together"** | Connections used in the right order get stronger |
| **Stimulation** | Gently making a brain region more active (excite, blue) or less active (calm, yellow) |
| **Overcompensation** | The healthy side becoming too strong and pushing down the damaged side |
| **Recovery window** | The first weeks to months after a stroke, when the brain rewires fastest |
| **Diaschisis** | Damage in one area making connected areas go quieter too |
| **Digital twin** | A computer copy of the patient's brain with a guess about their hidden traits |
| **Particle filter** | The method of keeping many guesses and dropping the ones that don't match reality |
| **Planner** | The part of the AI that searches for the best treatment plan |
| **Control group** | A comparison (normal rehab / no rehab) to prove a treatment really helps |
| **Arm strength %** | How much of the "move" signal reaches the arm, compared with before the stroke (100% = fully back) |
| **Seizure** | A burst of uncontrolled brain activity that spreads |
| **Responsive neurostimulation** | Real devices that detect a seizure starting and stimulate to stop it |
| **Gemini** | Google's AI, used to write the progress report |
| **Arduino Uno Q** | The small board on the table running the LED brain map, screen, lights and arm |
| **Servo** | A small motor that turns to a set angle: our patient's arm |
| **Hotspot** | The laptop sharing its internet as a WiFi network for the board and phones |
