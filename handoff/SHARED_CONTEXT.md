# SHARED CONTEXT: NeuroNest (OxHack 2026)

> **For Claude (any teammate's session): read this whole file before doing anything.** It's the single source of truth for the project idea, the hardware, the team split, and every interface between the three parts. Your person-specific brief (`PERSON_A_BRAIN_AI.md`, `PERSON_B_HARDWARE.md` or `PERSON_C_EXPERIENCE.md`) tells you what *your* human builds. If your brief and this file ever disagree, **this file wins on interfaces**. Don't change any interface in Section 7 without the whole team agreeing; if you must, update this file in the same commit and tell your human to tell the others.

---

## 0. Event facts

- **Event:** OxHack 2026, Mathematical Institute, University of Oxford. MLH member event, ~350 hackers.
- **Theme:** **TIME**
- **Hacking:** started 12:00 Saturday 24 Oct 2026 → **ends 12:00 Sunday 25 Oct 2026**. The clocks go back at 02:00 → 01:00 overnight, so it's 25 hours. **Watch out for timestamps around 01:00–02:00 Sunday:** always use UTC or monotonic clocks in code, never local wall-clock time.
- **Schedule:** Dinner 18:00–19:00 · MLH workshop 19:00 (C1) · HRT midnight pizza 00:00 · Breakfast 08:00–09:00 · **Devpost submission workshop 09:00 (C1)** · Hacking ends 12:00 · Lunch 12:00 · **Judging 13:00–15:45** · Closing 16:00.
- **Team:** 3 CS students: **A** (brain + AI), **B** (hardware), **C** (experience + presenter). Each works with their own Claude.

### Prizes we're targeting
| Prize | How we qualify |
|---|---|
| **HRT: Best use of Data for Predictions & Decision Making** (headline sponsor) | Recovery forecaster, digital twins, therapy planner: prediction and decision-making under uncertainty |
| **Citadel: Best Handmade Project** | Uno Q LED brain map + servo arm + physical presentation |
| **MLH: Best Use of Tiger Data** | Everything is a time series in Tiger Data (managed Postgres + TimescaleDB) |
| **MLH: Best Use of Gemini API** | Narrator + clinician report |
| **MLH: Best Use of ElevenLabs** | Voice coach + narrator voice |
| (Overall prizes) | Real problem, fits TIME, good demo |

---

## 1. The project in one paragraph

**NeuroNest is a flight simulator for stroke recovery.** A virtual human brain (76 cortical regions, real MRI-derived wiring from The Virtual Brain project) is given a stroke. Visitors rehab it by **squeezing their hand at a webcam** (each squeeze = the patient's attempt to move; **effort** is measured from the hand movement) while someone **times brain stimulation** from a phone. Well-timed stimulation + effortful practice makes surviving pathways **rewire**, and the patient's arm (a servo + the dashboard) recovers over simulated days. An AI builds **digital twins** of each patient, **forecasts day-90 recovery with uncertainty**, and **plans the best-timed therapy** by testing plans on copies of the brain. It's shown on a 3D dashboard and on the Arduino Uno Q's LED matrix (a brain map), with a Grove LCD status screen and voice coaching.

**Pitch line:** *"After a stroke, the brain can rewire, but only if you hit the right spot at the right time while the window is still open. NeuroNest predicts how a patient will recover and finds the best-timed therapy before anyone touches their real brain."*

---

## 2. The problem (use these facts consistently)

- ~**100,000 strokes/year in the UK**, **12M+ worldwide**. A leading cause of long-term disability.
- After a stroke the brain partly **rewires** (plasticity); surviving regions take over lost function. It's strongest in the **first weeks to ~3 months**, then fades.
- **Paired stimulation** works by firing at the moment of movement, e.g. **Vivistim** vagus nerve stimulation, approved in the US in 2021 for upper-limb stroke recovery.
- Every patient differs: lesion, wiring, age, how much the healthy hemisphere overcompensates, response to stimulation, best timing. Choosing target / excite-vs-calm / timing / schedule is mostly trial and error on real patients during the critical window.
- Clinical prediction tools exist (e.g. **PREP2** predicts arm recovery from early tests). The "~70% **proportional recovery** rule" says patients recover ~70% of lost function; it's **debated**. We use it as a baseline to compare against.

### TIME, three ways
1. **Milliseconds:** stimulation must line up with the movement attempt.
2. **Days:** the recovery window closes.
3. **The future:** forecast day-90 recovery from the first week.

### Honesty (always say this; never overclaim)
- It's a **simplified research-style simulator, not a medical device**.
- **Effort** is estimated from hand movement, not muscle force.
- Patients are **simulated**; a real tool needs real patient data and clinical validation across diverse populations.
- **No animals, no patient data:** an anonymised template brain from consenting volunteers. Credit **The Virtual Brain** (thevirtualbrain.org) for `connectivity_76`.

---

## 3. The science model (the concept; Person A's brief has the exact maths)

- **Regions:** 76 (38 per hemisphere), each with an activity level `r ∈ [0,1]`.
- **Connections:** directed weights + **travel delays** from tract lengths (signals arrive later from further away).
- **Hemispheres check each other:** each region is inhibited by its homolog on the other side.
- **Movement attempt:** a pulse (scaled by **effort**) enters the planning regions of the **lesioned hemisphere** (PFCDL, PMCM) → spreads via premotor (PMCDL, PMCVL) → primary motor cortex (M1). **Arm output** = motor regions' response. Left brain controls the right arm; default lesion is the **left** hemisphere, so the **right arm** is affected.
- **Stroke:** a region's health `h ∈ [0,1]` drops (e.g. lM1 → 0.3); its output is scaled by `h`. **Diaschisis:** connected regions go quiet. **Overcompensation:** the healthy side inhibits the lesioned side more.
- **Rewiring:** connections where the source fires shortly **before** the target strengthen (timing-based Hebbian rule), scaled by **effort**, a **stimulation timing gate**, and a **recovery window** that shrinks over days. "Use it or lose it" decay when practice is low. Some early spontaneous recovery of perilesional health.
- **Stimulation:** 🔵 **excite** (positive input) or 🟡 **calm** (negative input) to a chosen region for ~150 ms; timing relative to the attempt onset matters. Stimulation too late can be **maladaptive**.
- **Days:** ~20 attempts = 1 simulated day. A **fast-forward** button runs 90 days.
- **Arm function** = motor response to a standard test attempt ÷ the pre-stroke response (0–1.2).

### Patient traits (hidden from the AI; this is what the twins infer)
`plasticity` (rewiring speed, lower when older), `overcomp` (healthy-side overcompensation), `stim_sens` (response to stimulation), `best_offset_ms` (best stimulation timing), `window_tau_days`, plus the lesion (regions + severity), age and adherence.

### Patient cards (must produce DIFFERENT best plans; Person A tunes this)
| Card | Who | Lesion | Traits | Expected AI plan |
|---|---|---|---|---|
| **A** | 74 y | lM1 70% damaged (h=0.30), lPMCDL 20% (h=0.80) | plasticity 0.6, overcomp 1.6, stim_sens 0.7, best_offset −40 ms, window τ 22 d | **Calm rM1** (release the overcompensating healthy side) |
| **B** | 45 y | lM1 50% (h=0.50) | plasticity 1.3, overcomp 0.8, stim_sens 1.3, best_offset −80 ms, window τ 36 d | **Excite lPMCDL ~80 ms before movement** |
| **R** | random | random | random | whatever the planner finds |

---

## 4. Features (all must exist by the demo, in priority order)

**MUST (core demo)**
1. Virtual brain sim + stroke + knock-on dimming (A)
2. Webcam hand squeeze → movement attempt with **Effort Tracking** (depth, speed, hold → effort 0–100%), per-person calibration, spacebar backup (C)
3. Timed stimulation from phone/keyboard with ms timing feedback (C UI, A model)
4. Rewiring over attempts/days; arm function improves; good timing beats bad (A)
5. Dashboard: 3D brain, arm %, effort gauge, day, timing feedback, recovery curve (C)
6. Uno Q: LED brain map + Grove LCD status (B)
7. Tiger Data logging of everything (C, with A writing simulation data through C's module)

**SHOULD**
8. Digital twins + forecast band that shrinks (A)
9. Recovery forecaster (ML) + proportional-recovery baseline (A)
10. Therapy planner: AI plan vs standard vs no rehab curves; patient cards A/B differ (A)
11. Servo arm (B)
12. ElevenLabs voice coach ("Squeeze... now!", fatigue encouragement) + narrator (C)
13. Fatigue detection from falling effort (C computes, A uses as a forecast feature)
14. Gemini narrator lines + clinician report (C)
15. Fast-forward 90 days (A engine, C button)

**COULD (stretch)**
16. Phone-colour detection via webcam (blue/yellow screen = stim) (C)
17. Misc LCD showing the forecast (B)
18. Live timing coach that learns each visitor's rhythm (C + A)
19. Brain sim running on the Uno Q itself (A + B)
20. Post-stroke epilepsy plug-in (A)

---

## 5. Hardware inventory (this is everything we have)

| Item | Qty | Notes |
|---|---|---|
| **Arduino UNO Q** | 1 | Dual brain: Qualcomm Dragonwing MPU running **Debian Linux** (Python, WiFi) + **STM32U585** microcontroller (Arduino sketches). Linked by **Bridge** (RPC). Built-in **8×13 LED matrix** (104 LEDs). USB-C for power/data. MCU I/O is **3.3 V logic**; 5V and 3V3 pins on the headers. Programmed with **Arduino App Lab**. |
| **Grove LCD RGB Backlight** | 1 | 16×2 characters, I2C. LCD at `0x3E`; RGB controller at `0x62` (V4) or `0x30` (V5). **Check the version label on the back.** |
| **Misc LCD** | 1 | **Unidentified.** 4 pins (I2C backpack, address 0x27/0x3F) or 16 pins (parallel HD44780). Optional. |
| **Micro servo** | 1 | Likely SG90-type: brown/black = GND, red = 5V, orange/yellow = signal. 0–180°. |
| **USB webcam** | 1 | Plugs into the **demo laptop** (not the Uno Q; we have no USB-C hub). |
| **Breadboard** | 1 | Power rails for 5V/GND sharing. |
| **Grove cables** | several | Grove 4-pin: black GND, red VCC, white SDA, yellow SCL. Possibly Grove-to-Grove only. |
| **Female-to-female jumpers** | many | |
| **Male-to-female jumpers** | many | |
| **Laptops** | 3 | Laptop 1 (Windows 11, Python 3.11.8) = demo machine. Others = dev. |
| **Phones** | 3+ | Stimulation controller pages; hotspot backup. |
| *No:* cardboard, pulse/IMU/EMG sensors, USB-C hub, speakers (use laptop audio). | | Paper/tape can probably be borrowed from organisers. |

---

## 6. Architecture

```
 ┌────────────────────────────── LAPTOP 1 (demo) ──────────────────────────────┐
 │  inputs/hands.py  (separate process; own venv OK)                           │
 │    webcam → MediaPipe → openness → squeeze + EFFORT ──ws──┐                 │
 │                                                           ▼                 │
 │  server/app.py  (FastAPI, uvicorn, port 8000)  ◄──ws── phone pages (stim)   │
 │    ├── brain/ (A): Session — sim, stroke, plasticity, patients              │
 │    ├── ai/    (A): twins, forecaster, planner                               │
 │    ├── storage/tiger.py (C): Tiger Data writer                              │
 │    ├── server/narrator.py (C): Gemini + ElevenLabs                          │
 │    ├──ws──► web/dashboard (three.js, big screen)                            │
 │    ├──ws──► web/phone (stim buttons, timing feedback)                       │
 │    └──HTTP GET /api/device (polled 10×/s) ◄── UNO Q                         │
 └─────────────────────────────────────────────────────────────────────────────┘
                                   │ WiFi (same network!)
 ┌──────────────── ARDUINO UNO Q ───────────────┐
 │ Linux: unoq/python/main.py — polls /api/device, calls Bridge                │
 │ MCU:   unoq/sketch/sketch.ino — setLeds / setLcd / setServo                 │
 │        LED matrix · Grove LCD · servo · (misc LCD)                          │
 └──────────────────────────────────────────────┘
          Cloud: Tiger Data (Postgres) · Gemini API · ElevenLabs API
```

**Key design decisions (already made):**
- **Everything heavy runs on Laptop 1.** The Uno Q is a display/actuator client. (Stretch: run the sim on the Uno Q.)
- **The Uno Q polls over HTTP** (Python stdlib `urllib`, no extra packages on the board), not WebSockets. That's simpler and more robust.
- **hands.py is a separate process** so MediaPipe's dependencies can't break the server's environment.
- **The server is the clock.** All event times are server-side `time.monotonic()` ms. hands.py runs on the same laptop and can send its own monotonic timestamps. Phone events are timestamped **on receipt** minus an estimated half round-trip time.
- **Attempt resolution window:** the server waits **400 ms after the attempt onset** before simulating that attempt, so it can include stimulation taps from 400 ms **before** to 400 ms **after** onset. The animation therefore lags reality by ~0.4 s, which is fine.

---

## 7. Interfaces (CONTRACT: do not change without the team)

### 7.1 Repo layout
```
neuronest/
  CLAUDE.md                  ← auto-loaded by Claude Code; points here
  PROJECT.md  TEAM_PLAN.md   ← human-readable plan
  handoff/                   ← these briefs
  data/connectivity_76/      ← TVB brain map (weights.txt, tract_lengths.txt, centres.txt, ...)
  brain/      (A) __init__.py, connectome.py, model.py, stroke.py, plasticity.py, patients.py, session.py, layout.py, fake.py
  ai/         (A) twins.py, population.py, forecaster.py, planner.py, models/ (saved models)
  inputs/     (C) hands.py, keyboard.py
  storage/    (C) tiger.py, schema.sql
  server/     (C) app.py, narrator.py, device.py, audio_cache/
  web/        (C) dashboard/ (index.html, app.js, style.css), phone/ (index.html, phone.js)
  unoq/       (B) python/main.py, sketch/sketch.ino, README.md
  tests/      everyone: test_*.py
  requirements.txt  requirements-hands.txt  .env.example  .gitignore  DECISIONS.md
```
⚠️ The folder is `inputs/`, **not** `io/`, because `io` would shadow Python's standard library.

### 7.2 Environment (`.env`, never committed; `.env.example` is committed)
```
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash        # check AI Studio for the current fast model and update
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=                 # pick a calm voice in the ElevenLabs voice library
ELEVENLABS_MODEL=eleven_flash_v2_5   # low-latency model; confirm in the ElevenLabs docs
TIGER_DATABASE_URL=postgres://...    # from the Tiger Cloud console (service connection string)
SERVER_PORT=8000
```
Load with `python-dotenv`. All network calls must **fail soft** (log + continue) if a key is missing or the internet is down.

### 7.3 Python API between A (brain/ai) and C (server) — `brain/session.py`
C builds against `brain/fake.py` (`FakeSession`, same signatures, plausible random data) until A's real one is ready. **A ships `fake.py` first, in the first hour.**

```python
from brain.session import Session, AttemptResult, DayResult

s = Session(card: str = "B", seed: int = 0)       # card in {"A","B","R"}
s.info() -> dict          # {"card","age","lesion":{"lM1":0.3,...},"affected_arm":"right","regions":[76 names],"centres":[[x,y,z]*76]}

s.submit_stim(t_ms: float, region: str, kind: str, intensity: float = 1.0, actor: str = "human") -> None
    # kind in {"excite","calm"}; t_ms = server monotonic ms. Buffered until an attempt resolves.

s.submit_attempt(t_ms: float, effort: float, depth: float, speed: float, hold_ms: float) -> AttemptResult
    # Call this at onset + 400 ms (the server handles the wait). Uses stims in [t_ms-400, t_ms+400].

@dataclass AttemptResult:
    t_ms: float
    effort: float
    frames: np.ndarray        # shape [F, 76], activity sampled every 20 ms over the window (F ≈ 35), for animation
    frame_dt_ms: float        # 20.0
    arm_output: float         # 0..1.2, peak motor response this attempt (servo uses this)
    arm_function: float       # current arm function estimate after this attempt (0..1.2)
    stim_offset_ms: float | None   # nearest stim time minus onset (negative = before), None if no stim
    stim_verdict: str | None       # "good" | "early" | "late" | "wrong_region" | None
    gate: float               # plasticity gate multiplier applied (≥1)
    attempts_today: int
    day: int
    weight_change: np.ndarray # shape [76,76], W_now - W_prestroke (for dashboard edges); may be sparse/float32

s.end_day() -> DayResult        # also called automatically after 20 attempts
@dataclass DayResult:
    day: int; arm_function: float; mean_effort: float; attempts: int; window_level: float; stim_count: int

s.fast_forward(days: int, plan: dict | None = None) -> list[DayResult]
    # plan None = "standard rehab" (20 attempts/day, effort ~0.7, no stim). See 7.4 for the plan schema.

s.state() -> dict               # current snapshot for the dashboard (see 7.5 "state" fields A owns)
s.idle_frame(t_ms) -> np.ndarray   # [76] gentle resting activity for animating between attempts

from ai.twins import TwinFilter
s.twins.update(day_result) ; s.twins.forecast() -> {"day":int,"p10":float,"p50":float,"p90":float,
    "curve":{"days":[...],"p10":[...],"p50":[...],"p90":[...]}, "n_alive":int}

from ai.planner import plan_for
plan_for(s, budget_s: float = 10.0) -> {
    "best": plan_dict, "explanation": str,
    "curves": {"ai":{"days":[...],"p50":[...],"p10":[...],"p90":[...]},
               "standard":{...}, "none":{...}},
    "candidates_tested": int}

from brain.layout import led_layout, led_string
led_layout() -> dict[str, tuple[int,int]]          # region name -> (row, col) on the 8×13 matrix
led_string(activity: np.ndarray, health: np.ndarray, stim_region: str|None, t_ms: float) -> str  # 104 chars '0'..'9'
```

### 7.4 Plan schema
```json
{"target": "lPMCDL", "kind": "excite", "offset_ms": -80, "intensity": 1.0,
 "sessions_per_day": 2, "attempts_per_session": 20, "start_day": 3}
```
`target` may be `null` (no stimulation). The standard plan = `{"target": null, "sessions_per_day": 1, "attempts_per_session": 20, "start_day": 1}`. No rehab = `{"target": null, "sessions_per_day": 0}`.

### 7.5 WebSocket + HTTP messages (server = `ws://<laptop-ip>:8000`)

**Endpoints**
| Endpoint | Direction | Who |
|---|---|---|
| `GET /` → redirect to `/dashboard/` | | |
| `GET /dashboard/`, `GET /phone/` | static files | browsers |
| `WS /ws/input` | client → server | hands.py, keyboard.py, phone (also receives `ack`) |
| `WS /ws/state` | server → client | dashboard, phone |
| `GET /api/device` | Uno Q polls | returns the latest `device` JSON |
| `POST /api/control` | dashboard → server | same JSON as `control` messages |
| `GET /api/health` | anyone | `{"ok":true,"t_ms":...}` |

**Into the server (`/ws/input`)**
```json
{"type":"hello","client":"hands"|"keyboard"|"phone","id":"phone-3"}
{"type":"ping","client_t":123456.7}                   → server replies {"type":"pong","client_t":...,"server_t_ms":...}
{"type":"attempt","t_ms":1730120.0,"effort":0.82,"depth":0.90,"speed":0.70,"hold_ms":380,"source":"webcam"|"keyboard"|"phone"}
{"type":"attempt_onset","t_ms":1730120.0,"source":"webcam"}   (optional early notice for the UI: "squeeze detected")
{"type":"stim","region":"lPMCDL","kind":"excite"|"calm","intensity":1.0,"client_t":..., "rtt_ms":42, "id":"phone-3"}
     → the server sets t_ms = receive_time − rtt_ms/2 for phones; keyboard/hands may send t_ms directly
{"type":"calibration","open":0.93,"closed":0.18,"v_ref":3.1}  (hands → server, for display/logging)
{"type":"control","action":"new_patient","card":"A"|"B"|"R"}
{"type":"control","action":"end_day"}
{"type":"control","action":"fast_forward","days":90,"plan":"ai"|"standard"|"none"}
{"type":"control","action":"run_planner"}
{"type":"control","action":"set_mode","mode":"human"|"ai"}     (ai = planner's stim applied automatically)
{"type":"control","action":"calibrate"}                         (tells hands.py to run calibration)
{"type":"control","action":"report"}                            (Gemini clinician report)
{"type":"control","action":"reset"}
```
In the **attempt** message, `t_ms` = **onset** time (when openness crossed the squeeze threshold). It's sent at onset + 400 ms (or on release if sooner, with what's known so far).

**Out of the server (`/ws/state`, ~20 Hz)**
```json
{"type":"state","t_ms":..., "mode":"human",
 "patient":{"card":"B","age":45,"lesion":{"lM1":0.5},"affected_arm":"right"},
 "day":23,"attempts_today":7,"arm_function":0.47,"arm_output":0.31,
 "effort":{"last":0.82,"rolling":0.74,"session_start":0.86,"fatigue":false},
 "activity":[76 floats 0..1], "health":[76 floats],
 "stim":{"region":"lPMCDL","kind":"excite","active":true} | null,
 "timing":{"offset_ms":-80,"verdict":"good"} | null,
 "forecast":{"p10":0.69,"p50":0.78,"p90":0.87,"n_alive":143} | null,
 "edges":[[src_idx,dst_idx,delta],...]   (top 150 by |delta|, sent at most 1×/s),
 "curve":{"days":[1..23],"arm":[...]} (sent on change),
 "plan":{...} | null, "plan_curves":{...} | null,
 "narration":{"text":"...","audio_url":"/audio/abc.mp3"} | null,
 "calibrating":false}
{"type":"event","kind":"attempt"|"stim"|"day_end"|"patient"|"fatigue"|"plan_ready","data":{...}}
{"type":"audio","url":"/audio/squeeze_now.mp3"}      (the dashboard plays it)
{"type":"report","markdown":"..."}
```

**`GET /api/device` → Uno Q (B consumes; C produces)**
```json
{"seq":1234,
 "leds":"0000000000000...104 chars, row-major, row 0 first, '0'=off..'9'=brightest",
 "lcd1":"DAY 23  ARM 47% ", "lcd2":"EFFORT 82%      ",
 "rgb":[255,160,0],
 "servo":64,
 "lcd3":"DAY90 FORECAST  ","lcd4":"78% +/- 9       "}
```
- `leds`: exactly 104 chars. Row-major: index = row*13 + col. Row 0 = front of the brain. Cols 0–5 = left hemisphere, col 6 = divider (always '1' or '0'), cols 7–12 = right hemisphere. **Brightness may change at most ~3×/s per LED (no fast flashing).**
- `lcd1`–`lcd4`: exactly 16 chars, padded. ASCII only (no ✓; use `OK` / `+`).
- `rgb`: Grove backlight. **Arm-function colour scale:** <0.35 red `[255,40,40]`, 0.35–0.7 amber `[255,160,0]`, ≥0.7 green `[40,220,80]`; calibrating = white `[200,200,200]`; idle/no patient = blue `[40,80,255]`.
- `servo`: **0–90** degrees target (0 = arm down). The sketch smooths movement. Per attempt, the server sends a rise to `round(90*min(arm_output,1))`, then a decay back to 0 over ~800 ms.
- `seq` increments on every change; the device can skip duplicates.
- `stim` (optional): `"excite"` | `"calm"` | `null`. Set while a stimulation is active (~150–300 ms so it's visible). The Uno Q shows it on onboard RGB **LED3** (blue = excite, yellow = calm). Onboard **LED4** mirrors `rgb` (each channel on if ≥128): red/amber/green status.
- Colours: the 8×13 LED matrix is single-colour (brightness only). Colour lives on the Grove LCD backlight and the onboard RGB LEDs 3/4.

### 7.6 LED layout rule (A implements `brain/layout.py`; B and C rely on it)
Data axes (verified): in `centres.txt`, **x = front(+)/back(−)**, **y = left(+)/right(−)**, **z = up(+)/down(−)**. Region names start with `l` (left) or `r` (right).
- For each hemisphere: rank its 38 regions by x (front first) into 8 rows (≈5 per row), and within a row by |y| from the midline (col nearest 6 = most medial). Resolve collisions by assigning to the nearest free cell (greedy or Hungarian). Left hemisphere uses cols 0–5 (col 5 medial), right uses cols 7–12 (col 7 medial).
- Brightness: `round(9 * health_scaled_activity)`; lesioned regions (h<0.5) show '0' or '1'; the stimulated region pulses between 5 and 9 at ≤1.5 Hz.

### 7.7 Tiger Data schema (`storage/schema.sql`, C owns; A writes simulation population rows via `storage/tiger.py`)
```sql
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, started TIMESTAMPTZ DEFAULT now(),
  card TEXT, age INT, lesion JSONB, traits JSONB, kind TEXT);   -- kind: 'live' | 'population'

CREATE TABLE IF NOT EXISTS brain_state (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  activity REAL[]);                                    -- 76 values, sampled ~5 Hz live
CREATE TABLE IF NOT EXISTS attempts (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  effort REAL, depth REAL, speed REAL, hold_ms REAL, arm_output REAL, arm_function REAL,
  stim_offset_ms REAL, stim_verdict TEXT, gate REAL, source TEXT);
CREATE TABLE IF NOT EXISTS stimulation (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  actor TEXT, region TEXT, kind TEXT, intensity REAL, offset_ms REAL);
CREATE TABLE IF NOT EXISTS daily (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  arm_function REAL, mean_effort REAL, attempts INT, window_level REAL, stim_count INT, plan JSONB);
CREATE TABLE IF NOT EXISTS weights (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  src SMALLINT, dst SMALLINT, strength REAL);          -- only changed edges (|delta| > 1e-3)
CREATE TABLE IF NOT EXISTS forecasts (time TIMESTAMPTZ NOT NULL, session_id TEXT, sim_day INT,
  p10 REAL, p50 REAL, p90 REAL, n_alive INT, method TEXT);   -- method: 'twins' | 'ml' | 'proportional'
-- Make each time-series table a hypertable (try the first form; if unsupported, use the second):
--   SELECT create_hypertable('attempts', by_range('time'), if_not_exists => TRUE);
--   SELECT create_hypertable('attempts', 'time', if_not_exists => TRUE);
```
For **population** (simulated) patients, `time` = a synthetic timestamp: `2026-01-01 + sim_day days` per session, so continuous aggregates and time queries still make sense.

### 7.8 Run commands (Laptop 1)
```bash
python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
uvicorn server.app:app --host 0.0.0.0 --port 8000
# separate terminal, separate venv allowed:
python -m venv .venv-hands && .venv-hands\Scripts\activate && pip install -r requirements-hands.txt
python inputs/hands.py --server ws://127.0.0.1:8000/ws/input --camera 0
```
Dashboard: `http://localhost:8000/dashboard/` · Phones: `http://<laptop-ip>:8000/phone/` (find the IP with `ipconfig`) · **Allow Python through Windows Firewall (private networks)** when prompted.

`requirements.txt`: `numpy scipy scikit-learn lightgbm fastapi uvicorn[standard] websockets python-dotenv psycopg[binary] google-genai elevenlabs qrcode`
`requirements-hands.txt`: `mediapipe opencv-python websockets numpy` (MediaPipe may pin an older numpy, which is why it gets its own venv. Don't install `opencv-python-headless` alongside `opencv-python`: the headless build breaks `cv2.imshow`. Laptop 1 currently has both installed globally, so use the venv.)

---

## 8. Team, ownership, timeline

> **Ownership update (Sat ~18:00):** B's hardware is done, so **B now also owns Tiger Data storage (`storage/`: schema.sql, tiger.py with `TigerWriter` + `bulk_write_population` exactly as specified in PERSON_C_EXPERIENCE.md §7) and the big-screen dashboard (`web/dashboard/`, spec in PERSON_C_EXPERIENCE.md §5).** C keeps the server hub, webcam hands + effort, keyboard backup, phone page, Gemini/ElevenLabs narration, Devpost and presenting. C's server imports `storage.tiger.TigerWriter` and serves `web/dashboard` statically. Interfaces are unchanged.


| | A: Brain + AI | B: Hardware | C: Experience |
|---|---|---|---|
| Folders | `brain/`, `ai/` | `unoq/`, **`storage/`**, **`web/dashboard/`** | `inputs/`, `server/`, `web/phone/` |
| Ships first (hour 1) | `brain/fake.py` + `brain/layout.py` | Uno Q on WiFi + LED example | server skeleton serving fake state + hands openness print |

**Checkpoints**
- **CP1 (~17:00):** a stroke visibly lowers arm output (A). The Uno Q lights an arbitrary LED string from code (B). Squeeze + effort prints reliably (C).
- **CP2 (~21:00):** good stimulation timing clearly beats bad over a simulated week (A). The Uno Q shows `/api/device` from the laptop (B). The dashboard animates fake/real brain state (C).
- **CP3 (~00:00, midnight pizza check-in):** full loop: squeeze at webcam → brain animates on dashboard + LEDs → arm % changes → LCD updates. **Demo it to each other.**
- **CP4 (~06:00):** population in Tiger Data; twins forecast band shrinks; forecaster trained (A). Physical build done (B). Tiger logging + curves + voice (C).
- **CP5 (~10:00):** planner + cards A/B differ (A); rehearsal #1.
- **11:00 feature freeze → 11:45 Devpost submitted → 12:00 end.**

**Sleep shifts:** B 00:30–03:30 · C 02:30–05:30 · A 04:00–06:30 (population sim runs while A sleeps).

### Git workflow
- `main` must always run. Each person works on a branch (`a/...`, `b/...`, `c/...`) and merges small PRs or fast-forwards at checkpoints.
- Commit often. Never commit `.env`, model binaries > 50 MB, or venvs.
- Record any assumption or deviation in `DECISIONS.md` (one line each, with a timestamp and initials).

### Coding conventions
- Python 3.11, type hints, `dataclasses`, `numpy` vectorised. No global mutable state except in the server's session holder.
- Log with `logging` (INFO default). Every external call (Tiger, Gemini, ElevenLabs, Uno Q) is wrapped in try/except, logs a warning and **never crashes the server**.
- Deterministic with `seed`. Units are in variable names (`_ms`, `_days`).
- Each module has a `if __name__ == "__main__":` quick demo/self-test, plus tests in `tests/`.

---

## 9. Demo (3 minutes; practise ≥3×)
| Time | Who | What |
|---|---|---|
| 0:00–0:20 | C | Problem: every stroke is different, and the recovery window is short |
| 0:20–0:50 | B | A judge picks card A or B → the LED brain dims at left M1, the arm goes limp, knock-on dimming shows on the dashboard |
| 0:50–1:50 | C + judge | Calibration (open/close ×3), the judge squeezes, the effort gauge moves, B taps stimulation on the phone, the LCD says `STIM 80ms EARLY OK`, edges thicken, the servo lifts higher |
| 1:50–2:30 | A | The forecast band shrinks (twins). The planner: AI vs standard vs none curves; card A gets "calm rM1", card B gets "excite lPMCDL −80 ms" |
| 2:30–3:00 | C | Vision + honesty + credits (The Virtual Brain, Tiger Data, Gemini, ElevenLabs) |

**Backup video** recorded at 11:00. Pre-generated audio + report files in case the APIs fail.

---

## 10. Failure fallbacks
| Failure | Fallback |
|---|---|
| Venue WiFi isolates devices | **Venue WiFi is eduroam (WPA2-Enterprise + client isolation), so the Uno Q can't use it.** Use the demo laptop's **Windows Mobile Hotspot** (Settings → Network → Mobile hotspot, share eduroam over Wi-Fi, 2.4 GHz, name `NeuroNest`): Uno Q + visitor phones join it; the laptop's IP is then usually `192.168.137.1`. Fallback: phone hotspot for laptop + Uno Q + phones |
| Uno Q unreachable | Dashboard-only demo; the Uno Q runs a scripted offline animation (B builds a `--demo` mode into the sketch) |
| Hand tracking flaky | Spacebar (keyboard.py) / phone "SQUEEZE" button |
| Servo resets the board | Power the servo from a separate 5V USB, sharing GND; or remove it |
| Grove LCD dead | Status on the dashboard |
| Tiger / internet down | `storage/tiger.py` buffers to local CSV in `storage/offline/`; precomputed population results saved as `ai/models/*.json` |
| Gemini / ElevenLabs down | Cached audio in `server/audio_cache/`, canned narration lines, saved example report |
| Planner too slow | Precompute plans for cards A and B; cache to `ai/models/plans_{card}.json` |
