# NeuroNest

**A flight simulator for stroke recovery.** Damage a virtual human brain, rehab it with your own hand movements and well-timed stimulation, and let an AI predict each patient's recovery and find the best-timed therapy plan.

OxHack 2026 · Theme: **TIME**

---

## 1. The problem

- Around **100,000 strokes a year in the UK** and over **12 million worldwide**. Stroke is a leading cause of long-term disability.
- After a stroke the brain can partly **rewire itself**: surviving regions take over lost jobs. That ability is strongest in the **first weeks to months**, then fades.
- Newer rehab adds **stimulation timed to the patient's movement** (e.g. the Vivistim vagus nerve stimulation device, approved in the US in 2021 for arm recovery).
- **Every patient is different.** Which region to stimulate, whether to excite or calm it, how many milliseconds before or after the movement, and when to start are mostly decided by trial and error on real patients, during the window when time matters most.

**Our answer:** a virtual human brain where you can create a stroke, watch its effects, and test rehab plans in seconds instead of months. An AI builds a **digital twin** of each patient, forecasts their recovery and finds the best-timed therapy.

### TIME, three ways
1. **Milliseconds:** stimulation must line up with the movement attempt.
2. **Days:** the recovery window closes.
3. **The future:** the AI forecasts day-90 recovery from the first week.

---

## 2. How it works

### The brain
A **76-region map of the human cortex** from The Virtual Brain project, built from MRI scans of consenting volunteers. Each region has an activity level; connections have real strengths and real signal travel times. *(Data: The Virtual Brain, `connectivity_76`, credited.)*

### Moving the arm
A movement starts as a **plan** at the front of the brain, passes through **premotor** areas, and reaches **primary motor cortex (M1)**, which drives the arm. The left brain controls the right arm. Arm strength = how strongly motor output regions respond to a movement attempt, compared to before the stroke.

### What a stroke does
1. **Damage:** part of a region (e.g. 70% of left M1) stops working and its connections weaken.
2. **Knock-on effects (diaschisis):** connected regions go quiet because they've lost input.
3. **Overcompensation:** the healthy hemisphere can become overactive and suppress the damaged side further.

### How recovery happens
- Connections strengthen when regions **fire in the right order** ("fire together, wire together"), so surviving pathways take over.
- **Rehab = repeated movement attempts** that drive activity through surviving pathways.
- **Effort matters:** stronger, more complete attempts drive more rewiring (see Feature: Effort Tracking).
- **Well-timed stimulation amplifies rewiring**; mistimed stimulation does little or strengthens the wrong pathways.
- **The window closes:** rewiring slows over simulated weeks.
- **Use it or lose it:** unused connections fade.

### Treatments
| Treatment | Effect | Control |
|---|---|---|
| Excite a region | Boost a pathway trying to take over (e.g. left premotor) | 🔵 Blue |
| Calm a region | Quieten the overactive healthy side (e.g. right M1) | 🟡 Yellow |
| Timing | ms before/after the movement attempt | When you tap |
| Schedule | Start day, sessions/day, intensity | Plan settings |

---

## 3. Features

### 3.1 Webcam movement attempts
The visitor acts as the patient's **intention to move**. **MediaPipe Hands** finds 21 points on the hand in each webcam frame. **Hand openness** (fingertips to palm, 0 = fist, 1 = open) is tracked live. A fast open → closed transition fires a **movement attempt** into the brain's planning region. How strong the patient's arm turns out to be is decided by the virtual brain, not the visitor's grip, just like real rehab: the effort is there, the question is how much gets through.

### 3.2 Effort Tracking ⭐
No pressure sensor needed: effort is measured from the hand's movement.

**Effort score (0–100%) per attempt:**
- **Depth:** how fully the fist closed, relative to this person's calibrated range
- **Speed:** how quickly it closed
- **Hold:** how long the fist was held (up to a cap)

**What effort does:**
- **Drives the brain:** effort sets the strength of the movement-intention pulse. A full, fast fist sends a stronger signal than a lazy half-close.
- **Drives recovery:** rewiring after each attempt scales with effort, reflecting that active, engaged rehab helps more than going through the motions.
- **Fatigue detection:** effort is tracked across a session. When it trends down, the voice coach encourages ("Two more, really squeeze!") or suggests a short break, and the AI factors it into the plan (e.g. shorter, more frequent sessions).
- **Feeds the digital twin:** effort per session is logged to Tiger Data and used by the forecaster, so a patient with low effort gets a more cautious forecast.

**Shown on:** dashboard effort gauge + per-session effort chart; LCD (`EFFORT 82%`); voice feedback.

**Calibration:** "Open and close your hand 3 times" records each person's full open/closed range, so different hand sizes and camera distances work equally well.

**Accessibility backup:** spacebar or phone button = movement attempt; hold longer for a stronger effort.

### 3.3 Timed stimulation
A teammate or second visitor taps 🔵/🟡 on their phone (or keyboard) to stimulate a chosen region. The timing relative to the squeeze is measured to the millisecond, with instant feedback: `STIM 80ms EARLY ✓` / `STIM 300ms LATE ✗`.

### 3.4 Patient cards and personalisation
Patients differ in brain wiring, stroke location/size, age (rewiring speed), time since stroke, healthy-side overcompensation, stimulation sensitivity and best timing, and adherence. Example cards:
- **Patient A:** 74, large stroke, strong overcompensation → AI plan: calm right M1
- **Patient B:** 45, small stroke, responds well to stimulation → AI plan: excite left premotor 60ms before movement

### 3.5 Digital twins
~200 candidate twins per patient, each with different guesses for the hidden traits. Each simulated day, twins whose predictions didn't match the observed recovery are dropped. The forecast's uncertainty band **visibly shrinks** as the AI learns the person. Early sessions can include small timing probes to learn the patient's best timing faster.

### 3.6 Recovery forecaster
From stroke details + the first ~7 days of recovery (including effort), predicts **day-90 arm function with an uncertainty range** (e.g. 78% ± 9%). Trained on thousands of simulated patients. Benchmarked against the clinical "~70% proportional recovery" rule of thumb.

### 3.7 Therapy planner
Searches over plans (region, excite/calm, timing offset, intensity, start day, sessions/day). Each is tested by running a **copy of the patient's brain 90 days forward**. Picks the best recovery for the least stimulation, robust across the surviving twins. Shows **AI plan vs standard rehab vs no rehab** as three recovery curves.

### 3.8 Live timing coach
Learns each visitor's squeeze rhythm, predicts their next movement and cues stimulation at the right moment. ElevenLabs voice: "Squeeze... now!"

### 3.9 Fast-forward
~20 attempts = 1 simulated day. A fast-forward button runs 90 days in about a minute.

### 3.10 Narrator and report
Gemini writes plain-English narration of key moments and a clinician-style summary of each patient's recovery and plan. ElevenLabs voices it.

---

## 4. The system: step by step

1. **Startup:** load the brain map; each region gets an activity level.
2. **Pick a patient:** apply the stroke and the patient's traits. Knock-on dimming appears immediately.
3. **Brain ticks (~100×/s):** each region's activity = fading old activity + delayed signals from connected regions − the opposite hemisphere holding it back + current stimulation.
4. **Movement attempt:** webcam detects a squeeze and measures effort → effort-scaled pulse into the planning region → spreads through premotor → motor cortex response = arm strength now.
5. **Stimulation:** 🔵/🟡 tap pushes a region up or down; timing relative to the squeeze is recorded.
6. **Rewiring:** connections that fired in the right order strengthen, scaled by effort, stimulation timing and the closing recovery window.
7. **Days pass:** every ~20 attempts, arm function and session effort are measured and saved to Tiger Data, and the window shrinks.
8. **AI:** twins update, the forecast sharpens, the planner suggests the best plan.
9. **Display:** state streams to the dashboard, the Uno Q (LEDs, LCD, servo) and the voice.

```
              ┌─────────────────────────── LAPTOP ───────────────────────────┐
 Webcam ────► │ hand tracking → squeeze + EFFORT score                       │
 Phone  ────► │ 🔵/🟡 stimulation taps                                      │
              │          ▼                                                   │
              │   VIRTUAL BRAIN (steps 3–7) ──► AI (twins, forecast, plan)   │
              │          │                                                   │
              │          ├──► Dashboard (3D brain, effort, curves)           │
              │          ├──► Tiger Data (everything, timestamped)           │
              │          └──► Gemini + ElevenLabs (report + voice coach)     │
              └──────────┼───────────────────────────────────────────────────┘
                         │ WiFi: LED frame + LCD text + servo angle
                         ▼
              ┌────────── UNO Q ──────────┐
              │ LED matrix = brain map    │
              │ Grove LCD = status        │
              │ servo = arm (optional)    │
              └───────────────────────────┘
```

Everything runs on the laptop first. The Uno Q receives what to display over WiFi. Stretch goal: move the brain simulation onto the Uno Q.

---

## 5. Hardware

| Part | Role |
|---|---|
| **Arduino Uno Q** | Receives state over WiFi (Linux side) and drives the LEDs, LCD and servo (microcontroller side, via App Lab's Bridge) |
| **LED matrix (8×13)** | Brain map from above: left 6 columns = left hemisphere, right 6 = right, middle column = divider, regions arranged front to back. Stroke area dark, active regions glow, stimulated region pulses slowly. **No rapid flashing.** |
| **Grove LCD (RGB)** | `DAY 23 · ARM 47%` / `EFFORT 82%` / `STIM 80ms EARLY ✓`, with a red → amber → green backlight |
| **Micro servo** (optional) | The patient's arm: barely moves after the stroke, swings further as they recover. Paper strip taped on for visibility. |
| **USB webcam** | Hand tracking + effort |
| **Laptop** | Simulation, AI, dashboard, webcam processing |

Wiring: servo signal → D9, 5V, GND · Grove LCD → SDA, SCL, 5V, GND (check the board's voltage first).

---

## 6. Tech stack

- **Python 3.11:** numpy, scipy (simulation); scikit-learn / LightGBM (forecaster)
- **MediaPipe Hands + OpenCV:** hand tracking and effort
- **FastAPI + WebSockets:** live state to the dashboard, phone app and Uno Q
- **three.js:** 3D brain dashboard (76 regions at real positions, connections thicken as they rewire)
- **Arduino App Lab:** Python app + sketch (Servo, Arduino LED Matrix, Grove rgb_lcd)
- **Tiger Data:** brain activity, connection strengths per day, attempts + effort, stimulation events, forecasts, simulated patient population
- **Gemini:** narration + clinician report
- **ElevenLabs:** voice coach + narrator
- **Data:** The Virtual Brain `connectivity_76` (credited)

### Tiger Data tables
```sql
brain_state(time, session_id, region, activity)                 -- hypertable
attempts(time, session_id, effort, depth, speed, hold_ms, arm_output)
stimulation(time, session_id, actor, region, kind, offset_ms)
daily(sim_day, session_id, arm_function, mean_effort, window_level)
weights(sim_day, session_id, src, dst, strength)                -- replay the rewiring
forecasts(time, session_id, sim_day, p10, p50, p90)
```

### Project layout
```
neuronest/
  data/connectivity_76/   brain map (downloaded)
  brain/    model.py, stroke.py, plasticity.py, patients.py
  ai/       simulate_patients.py, twins.py, forecaster.py, planner.py
  inputs/   hands.py (squeeze + effort), keyboard.py
  storage/  tiger.py, schema.sql
  server/   app.py, narrator.py
  web/      dashboard/, phone/
  unoq/     App Lab app (python + sketch)
```

---

## 7. Prizes targeted
- **HRT:** forecaster + digital twins + planner (prediction and decision-making under uncertainty)
- **Tiger Data:** everything is a time series
- **Gemini, ElevenLabs:** narrator, report, voice coach
- **Citadel handmade:** Uno Q brain map + servo arm

---

## 8. Build plan

| Person | Owns |
|---|---|
| A: brain + AI | Simulation, stroke, rewiring, twins, forecaster, planner |
| B: hardware | Uno Q setup, LED brain map, LCD, servo |
| C: experience | Hand tracking + effort, phone app, dashboard, Tiger Data, voice, Devpost |

**Checkpoints**
1. A stroke visibly weakens the arm.
2. Webcam squeeze + effort drives movement attempts; good stimulation timing clearly beats bad.
3. Simulated patient population in Tiger Data; forecaster + twins working.
4. Dashboard, Uno Q display, voice coach, planner.
5. 11:00 Sunday: feature freeze, Devpost, rehearse. Hacking ends 12:00.

**Stretch goals:** brain running on the Uno Q; post-stroke epilepsy plug-in (bad rewiring can trigger seizures).

---

## 9. Demo (3 minutes)
1. **Problem (20s):** every stroke is different, and the recovery window is short.
2. **Stroke (30s):** a judge picks left M1. The brain map dims, knock-on effects spread, the arm goes limp.
3. **Rehab (60s):** a quick calibration, then the judge squeezes at the camera. The effort gauge responds, a teammate times the stimulation, the voice coaches, connections thicken, and the arm moves further.
4. **AI (40s):** the forecast band shrinks as twins are ruled out. "500 plans tested on copies of this brain: best is excite premotor 80ms before movement → 82% vs 61% standard."
5. **Vision (20s):** test the plan on a virtual copy first, and give the real brain the best shot while the window is open.

## 10. Honesty notes
- A simplified research-style simulator, not a medical device.
- Effort is estimated from hand movement, not muscle force.
- Patients are simulated; a real version needs real patient data and clinical validation across diverse populations.
- No animals, no patient data: an anonymised template brain from consenting volunteers.
