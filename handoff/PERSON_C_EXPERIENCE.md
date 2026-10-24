# PERSON C: Experience brief (webcam effort tracking, server, dashboard, phone app, Tiger Data, Gemini, ElevenLabs, Devpost, presenting)

> **For Claude:** your human is **Person C**. They own `inputs/`, `storage/`, `server/`, `web/`, the Devpost submission, and they're the **main presenter**. **First read `handoff/SHARED_CONTEXT.md` fully.** Section 7 is the contract: you implement the server side of every message, so match it exactly. Person A provides the brain via `brain.session.Session` (use `brain.fake.FakeSession` until the real one lands; same API). Person B's Uno Q polls your `GET /api/device`. Build in the order below and show your human something working at every step.

---

> **⚠️ OWNERSHIP UPDATE:** Person **B** now builds the **dashboard (§5)**. C: skip §5, but keep it as the spec your server must integrate with (serve `web/dashboard/` at `/dashboard`; broadcast `/ws/state` exactly per SHARED_CONTEXT §7.5; accept `POST /api/control`). **Tiger Data storage (§7) stays with C.** Everything else in this brief is still C's.

## 0. Context recap
- **Project:** *NeuroNest*, a stroke-recovery flight simulator for OxHack 2026 (theme **TIME**). A virtual brain gets a stroke; visitors rehab it by squeezing their hand at a webcam (each squeeze = a movement attempt, with an **effort** score); someone times 🔵 excite / 🟡 calm stimulation from a phone; connections rewire; the arm recovers over simulated days; the AI forecasts (digital twins) and plans therapy.
- **You build everything people see and touch** except the LED board: the webcam effort tracker, the server hub, the 3D dashboard (the big screen), the phone stimulation app, Tiger Data storage, the narrator and voice coach, and the story (Devpost + pitch).
- **Prizes you carry:** Tiger Data, Gemini, ElevenLabs, plus the overall demo quality. You also present HRT's numbers (A gives them to you).

## 1. Deliverables and timeline
| # | Deliverable | Done when | Target |
|---|---|---|---|
| 1 | Repo scaffold: `requirements*.txt`, `.env.example`, `.gitignore`, `DECISIONS.md`, `CLAUDE.md` exists | teammates can clone and run | first 30 min |
| 2 | `inputs/hands.py` v1: live openness 0–1 overlay | number tracks your hand | +45 min |
| 3 | `server/app.py` skeleton with `FakeSession` (or an inline fake until A pushes): `/ws/state` at 20 Hz, `/api/device`, static dashboard placeholder | B can poll `/api/device`; a browser sees state | **+1.5 h (unblocks B)** |
| 4 | hands.py v2: calibration, squeeze detection, **effort** score, sends `attempt` | "SQUEEZE effort 82%" reliable 20/20 | **CP1 ~17:00** |
| 5 | `inputs/keyboard.py` (spacebar backup) | press/hold = attempt | CP1 |
| 6 | Dashboard v1: 3D brain (76 nodes), arm %, effort gauge, day, timing feedback, controls | animates from state | **CP2 ~21:00** |
| 7 | Phone page: region picker + 🔵/🟡 buttons + timing feedback, RTT compensation | taps show up as stims | CP2 |
| 8 | Wire the real `Session`; attempt resolution (400 ms); device message builder | full loop | **CP3 00:00** |
| 9 | Tiger Data: schema, live logging, population bulk-writer for A, offline CSV buffer | rows visible in the Tiger console | by 02:30 |
| 10 | Dashboard v2: recovery curve + forecast band, edges thickening, plan comparison curves, fatigue | | by 02:30 → after sleep |
| 11 | ElevenLabs voice coach (cached clips) + narrator; Gemini narration + clinician report | voice plays on cues; report renders | morning |
| 12 | AI mode: coach cue "Squeeze... now!" + auto stim at plan offset | | morning |
| 13 | Metrics panel (A's `metrics.json`) + "N simulated patients" from Tiger | | by 10:00 |
| 14 | **09:00 Devpost workshop (C1)**, Devpost draft, backup video at 11:00, submit by 11:45 | submitted | 11:45 |

Sleep: **02:30–05:30** (leave the server running for A's overnight jobs if needed).

---

## 2. Setup (Laptop 1 = demo laptop, Windows 11, Python 3.11.8)
```bash
cd neuronest
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python -m venv .venv-hands && .venv-hands\Scripts\activate
pip install -r requirements-hands.txt
```
- Globally, Laptop 1 has **both `opencv-python` and `opencv-python-headless`**. Headless can break `cv2.imshow`, which is why the hands venv only gets `opencv-python`.
- **MediaPipe:** try `mp.solutions.hands` first. If it's missing in the installed version, use the **Tasks API** `HandLandmarker` (download `hand_landmarker.task` from the MediaPipe model page into `inputs/models/`). The 21 landmarks are the same either way.
- Get API keys: **Tiger Data** (mlh.link/tigerdata → create a service → copy the connection string), **Gemini** (mlh.link/aistudio), **ElevenLabs** (mlh.link/elevenlabs; pick a calm voice and copy its voice ID). Put them in `.env`; share with the team privately (not in git).
- When uvicorn first runs, **Windows Firewall asks: allow on Private networks**. Find the IP with `ipconfig` (Wireless LAN adapter → IPv4) and send it to B.

---

## 3. `inputs/hands.py`: webcam squeeze + Effort Tracking

### 3.1 Pipeline
1. `cv2.VideoCapture(args.camera)`, 640×480, mirror the image for a natural view.
2. MediaPipe Hands, `max_num_hands=1`, `min_detection_confidence=0.6`, `min_tracking_confidence=0.5`.
3. **Raw openness:** with landmarks (0 = wrist, 9 = middle-finger MCP, tips 4, 8, 12, 16, 20):
   `palm = dist(0, 9)`; `raw = mean(dist(0, tip) for tip in [8, 12, 16, 20]) / palm` (the thumb is unreliable, so exclude it or weight it at 0.5).
4. Smooth with an EMA (α = 0.5) for display; use the unsmoothed value for velocity, with a 3-frame median.
5. **Calibrated openness:** `o = clip((raw − closed) / (open − closed), 0, 1)`.
6. **Velocity:** `v = do/dt` in units/s (use frame timestamps from `time.monotonic()`).

### 3.2 Calibration (on start, on the `C` key, or when the server sends `control: calibrate`)
- The screen shows "OPEN and CLOSE your hand 3 times" plus a countdown (~6 s).
- Record raw over that period: `open = percentile 95`, `closed = percentile 5`, `v_ref = median of peak closing speeds` (abs). If `open − closed < 0.3` → "Try again: open wider / move closer".
- Send `{"type":"calibration","open":..,"closed":..,"v_ref":..}` to the server.

### 3.3 Squeeze state machine
```
IDLE (o > 0.6) --(o crosses below 0.5 while v < −0.5·v_ref)--> ONSET: t_onset = now
   immediately send {"type":"attempt_onset","t_ms":t_onset}
ONSET → track: depth = 1 − min(o), peak closing speed, hold_ms = time with o < 0.35
   at t_onset + 400 ms OR if o rises back above 0.6 first → send the attempt message (below), → REFRACTORY
REFRACTORY 300 ms → IDLE (requires o > 0.6 again: re-open before the next squeeze)
```
**Effort score (0–1):**
```
depth_s = clip(depth / 0.9, 0, 1)
speed_s = clip(peak_closing_speed / v_ref, 0, 1)
hold_s  = clip(hold_ms / 400, 0, 1)
effort  = 0.5·depth_s + 0.3·speed_s + 0.2·hold_s
```
Send: `{"type":"attempt","t_ms":t_onset,"effort":..,"depth":depth_s,"speed":speed_s,"hold_ms":..,"source":"webcam"}`.
**Time base:** hands.py runs on the same laptop, so send `time.monotonic()*1000`. The server uses the same clock (`time.monotonic()`), so they line up exactly.

### 3.4 On-screen overlay (the window is visible in the demo; make it look good)
Landmarks drawn, a big openness bar, effort of the last squeeze (big %), state text (`READY` / `SQUEEZE!` / `CALIBRATING`), a FPS counter. Keys: `C` calibrate, `Q` quit, `SPACE` manual attempt.

### 3.5 Robustness
- No hand for > 1 s → show "Show your hand", and send no attempts.
- WebSocket reconnect loop (every 1 s), plus a queue so attempts aren't lost during a blip.
- Accept `--server`, `--camera`, `--no-window`.
- **Acceptance:** 20 deliberate squeezes → 20 attempts, 0 false positives in 30 s of the hand resting/moving sideways; a gentle half-squeeze scores clearly lower effort (< 0.5) than a full fast fist (> 0.8).

### 3.6 `inputs/keyboard.py` (backup + accessibility)
A small window or terminal: **SPACE down** = onset; **up** (or 400 ms) = attempt with `effort = 0.4 + 0.6·clip(hold/400)`. **S** = send stim (excite on the selected region, so a teammate can do precise-timing stimulation at the laptop). Use the `keyboard` lib or a tiny Tk window (Tk avoids admin issues on Windows).

---

## 4. `server/app.py`: the hub (FastAPI + uvicorn)

### 4.1 Structure
- A single `Hub` object: holds the current `Session` (real or fake: `USE_FAKE=1` env var or automatic fallback if `brain.session` fails to import), the mode (`human`/`ai`), connected state clients, effort stats, the last device payload, the narration queue, the Tiger writer.
- **Clock:** `now_ms() = time.monotonic()*1000`.
- **Background tasks (asyncio):**
  1. **State broadcaster** 20 Hz: if an attempt animation is playing, send the next frame of `AttemptResult.frames`; otherwise `session.idle_frame(now)`. Merge `session.state()` + C's fields (effort, mode, narration, calibrating). Send `edges` at most 1 Hz (top 150 by |delta| from `weight_change`).
  2. **Device builder** 10 Hz: builds the `/api/device` JSON (SHARED_CONTEXT §7.5) using `brain.layout.led_string(activity, health, stim_region, now)`. LCD text by phase (calibrating / patient intro / rehab / fast-forward), colour from the arm-function scale, servo animation: on each attempt result rise to `round(90·min(arm_output,1))`, hold 300 ms, decay to 0 over ~800 ms. Increment `seq` on change.
  3. **Attempt resolver:** when an `attempt` arrives with onset t, schedule `await asyncio.sleep(max(0, t + 400 − now))`, then `res = await asyncio.to_thread(session.submit_attempt, ...)`. Start the animation, log to Tiger, emit `event: attempt`, update the effort stats, trigger the coach/narrator.
- **Inputs (`/ws/input`):** handle every message in §7.5. For phone `stim`: `t_ms = receive_ms − rtt_ms/2` (the phone measures RTT with ping/pong every 2 s and sends its latest `rtt_ms`). Call `session.submit_stim(...)`, broadcast `stim` (`active` for 150 ms), log it.
- **Controls:** `new_patient` (create a Session, reset effort stats, log a `sessions` row, narrate the intro), `end_day`, `fast_forward` (run in a thread; stream progress events; update the curve), `run_planner` (thread; `plan_for`; broadcast `plan` + `plan_curves`; narrate the explanation), `set_mode`, `calibrate` (forward to hands via `/ws/input` broadcast), `report`, `reset`.
- **Static:** mount `web/dashboard` at `/dashboard`, `web/phone` at `/phone`, `server/audio_cache` at `/audio`. `/` → redirect. `GET /qr` → PNG QR of `http://<lan-ip>:8000/phone/` (use the `qrcode` lib; detect the LAN IP via a UDP socket trick).
- **Fail-soft:** wrap Tiger/Gemini/ElevenLabs in try/except with timeouts; never block the event loop (use `asyncio.to_thread` or async clients).

### 4.2 Effort stats + fatigue (part of the Effort Tracking feature)
- Keep the last 30 efforts this session. `session_start` = mean of the first 5; `rolling` = mean of the last 8.
- **Fatigue** = at least 10 attempts AND `rolling < 0.8 · session_start`. On entering fatigue: event `fatigue`, voice clip "Two more, really squeeze!" (first time) or "Let's take a short break" (if it persists for 8+ more attempts), dashboard banner. Log it. Include `mean_effort` in the day results (A uses it).

### 4.3 AI mode (live timing coach)
- In `ai` mode, the server gives a rhythm: every ~3 s it plays "Squeeze... now!", where **"now"** is time T (the cached clip's "now" offset is known).
- Learn the visitor's reaction lag: `lag = median(onset − T)` over the last 5 attempts (start at 350 ms).
- Schedule the AI stim at `T + lag + plan.offset_ms` using `session.planned_stim()`; `actor = "ai"`.
- This is honest: the AI predicts the onset from the cue + the person's learned lag. Show "AI predicted onset ±xx ms" on the dashboard.

---

## 5. Dashboard (`web/dashboard/`): the big screen
Plain HTML + JS modules. **three.js** from a pinned CDN (`https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js` via an import map). Charts: plain `<canvas>` or **Chart.js 4.4.1** pinned from cdnjs. Dark theme, large text (readable from 3 m), 16:9.

**Layout:**
```
┌──────────────────────────── NEURONEST ───────────────── Patient B · 45y · L motor ─┐
│                                   │  ARM FUNCTION   47%  (big)  ▲ +3 today          │
│      3D BRAIN (76 nodes)          │  EFFORT gauge 82%  · rolling 74% · [FATIGUE]    │
│   glow = activity, grey = lesion  │  DAY 23 · attempts 7/20                         │
│   edges thicken green as rewire   │  TIMING  80 ms EARLY ✓ (good)                   │
│   pulse ring on stimulated region │  FORECAST day 90: 78% (69–87) · twins 143       │
├───────────────────────────────────┴─────────────────────────────────────────────────┤
│ Recovery curve: observed arm % by day + forecast band (p10–p90) + AI/standard/none  │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ Narrator text · [Card A][Card B][Random] [Calibrate] [End day] [Fast-forward 90]    │
│ [Run AI planner] [Mode: Human/AI] [Report] · QR to join · metrics: MAE vs 70% rule  │
└─────────────────────────────────────────────────────────────────────────────────────┘
```
**3D brain details:**
- Node positions: `info().centres` (x front/back, y left/right, z up/down) → three.js coordinates (front pointing away from the viewer or to the right). Scale to fit. Slow auto-rotation, with orbit controls.
- Node colour/size by activity (dark blue → cyan → white); lesioned nodes (health < 0.5) dark grey with a red outline; label key regions (M1, PMC, PFC) on hover/always for the motor ones.
- Edges: from `edges` (top 150 by |delta|): green and thicker when strengthened, red when weakened; opacity by |delta|. Show baseline structural edges among motor regions faintly.
- Stimulated region: an expanding ring (blue = excite, yellow = calm). **No strobe:** smooth animations ≤ 3 Hz.
- On an attempt: show the travelling wave (frames play at 20 ms steps, i.e. real time).
**Charts:** recovery curve (days on x, arm % on y) with the observed line, the forecast band (twins) as a shaded area, and the plan comparison (AI vs standard vs none as three lines with p10–p90 shading) when available.
**Audio:** play `audio` messages and `narration.audio_url` through an `<audio>` element. The browser needs one click to unlock audio, so add a "Start demo" overlay button.
**Report modal:** renders Gemini's markdown (use a tiny markdown renderer or `marked@12.0.2` pinned).

---

## 6. Phone page (`web/phone/`)
- Mobile-first, big buttons. On load: connect `ws://<host>/ws/input` (for sending) and `/ws/state` (for feedback). Send `hello` with a random id.
- **Region picker:** chips for the valid targets, `lPMCDL, lPMCVL, lPMCM, lM1, lS1` (excite) and `rM1, rPMCDL` (calm), plus "Other". Default `lPMCDL`.
- Two giant buttons: **🔵 EXCITE** / **🟡 CALM**. On `touchstart` (not click, which adds ~100 ms), send `stim` immediately with the current `rtt_ms`. Vibrate 30 ms.
- RTT: ping every 2 s, keep an EMA.
- **Feedback:** from state `timing` → big text "80 ms EARLY ✓ good" / "300 ms LATE ✗"; current arm %; a "squeeze detected" flash on `attempt_onset` events so the stimulator can learn the rhythm.
- Optional "SQUEEZE" button (accessibility/backup) sending `attempt` with source `phone`.
- Must work on iOS Safari + Android Chrome over plain `http` on the LAN (no APIs that require HTTPS).

---

## 7. Tiger Data (`storage/`)
- `schema.sql` = SHARED_CONTEXT §7.7 exactly. `python -m storage.tiger --init` creates the tables and converts them to hypertables (try `by_range` syntax, fall back to the legacy one). Add a **continuous aggregate** for the dashboard, e.g. `attempts_per_minute` (count, avg effort, avg arm_output); refresh policy every minute. It shows off TimescaleDB features for the prize.
- `storage/tiger.py`:
  ```python
  class TigerWriter:      # thread-safe, batched, fail-soft
      def __init__(self, url: str | None)        # None → offline mode
      def session(self, session_id, card, age, lesion, traits, kind)
      def attempt(self, session_id, sim_day, result: AttemptResult, source)
      def stim(self, ...); def daily(self, session_id, day_result, plan=None)
      def brain_state(self, session_id, sim_day, activity)   # sampled ~5 Hz
      def weights(self, session_id, sim_day, W_delta, threshold=1e-3)
      def forecast(self, session_id, sim_day, fc: dict, method)
      def flush(self)                             # background thread flushes every 1 s via executemany/COPY
  def bulk_write_population(sessions: list[dict], daily_rows: list[tuple])   # for A's population.py; uses COPY
  ```
- **Offline buffer:** if the connection fails, append rows to `storage/offline/*.csv`; `python -m storage.tiger --replay` uploads them later.
- **Never** let DB latency touch the 20 Hz loop: queue + background thread.
- Dashboard numbers from Tiger (for the prize narrative): total attempts logged, simulated patients count, "brain-state samples stored". Expose `GET /api/stats` (cached 10 s).
- **Hidden traits** of live patients go into `sessions.traits` (it's a simulator), but the dashboard must not show them before the "reveal" (optional reveal button at the end: "the twins guessed X, the truth was Y").

---

## 8. Voice + narration (`server/narrator.py`)

### 8.1 ElevenLabs (voice coach + narrator voice)
- SDK: `from elevenlabs.client import ElevenLabs`; `client.text_to_speech.convert(voice_id=..., model_id=ELEVENLABS_MODEL, text=..., output_format="mp3_44100_128")` returns audio bytes chunks; write them to `server/audio_cache/<hash>.mp3`. (Check the current SDK docs if the signature differs.)
- **Pre-generate at startup (cached on disk, generate only if missing):** "Squeeze... now!", "Great timing!", "A little early, try waiting a beat.", "A bit late, stimulate just before you squeeze.", "Two more, really squeeze!", "Let's take a short break.", "Calibration: open and close your hand three times.", "Patient A: seventy-four years old, stroke in the left motor cortex.", "Patient B: forty-five years old, a smaller stroke in the left motor cortex.", "Fast-forwarding ninety days.", "Recovery complete." For "Squeeze... now!", record the offset of "now" in the clip (measure once; default 900 ms) for AI mode.
- **Coach rules (rate-limited, ≥ 4 s between clips):** timing verdict after stimulated attempts (good/early/late, not every time, ~every 3rd); fatigue clips; patient intro on new_patient.
- **Committed fallback:** commit the generated mp3s once they exist, so the demo works offline.

### 8.2 Gemini (narrator lines + clinician report)
- SDK `google-genai`: `from google import genai; client = genai.Client(api_key=...)`; `client.models.generate_content(model=GEMINI_MODEL, contents=prompt)`. Run in a thread, 8 s timeout.
- **Narration** at key events (patient created, day end every 5 days, plan ready, fatigue, fast-forward done), at most 1 per 10 s. Prompt: system-style instructions ("You are the calm narrator of a stroke-recovery simulator demo. One or two short sentences, plain English, no medical advice, refer to it as a simulation.") + a compact JSON of recent events. Then TTS via ElevenLabs (not cached; fine if it's 1–2 s late). Fallback: template strings.
- **Clinician report** (`control: report`): send the patient info (age, lesion, NOT hidden traits), the daily curve, effort stats, stim log summary, the forecast and the plan + explanation → ask for a structured markdown report: Summary · Recovery so far · Effort & engagement · Recommended plan & rationale · Forecast with uncertainty · Limitations ("simulation only; not medical advice"). Show it in the modal; keep a saved example in `server/sample_report.md` as a fallback.

---

## 9. `/api/device` builder (what B's hardware shows)
Exactly SHARED_CONTEXT §7.5. LCD text by phase:
| Phase | lcd1 | lcd2 | rgb |
|---|---|---|---|
| No patient | `NEURONEST       ` | `STROKE SIM READY` | blue |
| Calibrating | `CALIBRATING...  ` | `OPEN+CLOSE x3   ` | white |
| Patient intro (5 s) | `PATIENT A  74y  ` | `STROKE: L MOTOR ` | by arm |
| Rehab | `DAY 23  ARM 47% ` | `EFFORT 82%      ` | by arm |
| ≤ 2 s after a stimmed attempt | `DAY 23  ARM 47% ` | `STIM 80ms EARLY ` / `TIMING GOOD +   ` / `STIM 300ms LATE ` | by arm |
| Fatigue | `DAY 23  ARM 47% ` | `TIRED? BREATHE  ` | by arm |
| Fast-forward | `>> DAY 61       ` | `FORECAST 78%    ` | by arm |
`lcd3/lcd4` (misc LCD): `DAY90 FORECAST  ` / `78% +/- 9       `. Pad/truncate to exactly 16 ASCII chars.
Also set `stim` to `"excite"`/`"calm"` for ~300 ms after each stimulation (else `null`). B's board shows it on an onboard RGB LED (blue/yellow).

---

## 10. Devpost + pitch (C leads, everyone contributes)
- **09:00 attend the Devpost workshop (C1).**
- Devpost sections: Inspiration (stroke facts, the TIME theme) · What it does · How we built it (architecture diagram, The Virtual Brain data credit, simulator, effort tracking, twins/forecaster/planner, Uno Q, Tiger Data/Gemini/ElevenLabs) · Challenges · Accomplishments (A's metrics) · What we learned · What's next (real patient MRI, clinical validation, post-stroke epilepsy) · **Honesty note** (simulator, not a medical device).
- Select prize categories: HRT, Citadel handmade, Tiger Data, Gemini, ElevenLabs (check the Devpost list for exact names).
- Media: screenshots of the dashboard, B's hardware photos, a **2-minute backup video recorded at 11:00** (screen capture + phone footage of the hardware).
- Built-with tags: Python, NumPy, LightGBM, FastAPI, WebSockets, three.js, MediaPipe, OpenCV, Arduino UNO Q, Arduino App Lab, TimescaleDB/Tiger Data, PostgreSQL, Gemini API, ElevenLabs.
- **Submit by 11:45.**
- **Pitch (C's lines):** opening problem (20 s), rehab walkthrough with the judge (60 s), closing vision + honesty + credits (30 s). See SHARED_CONTEXT §9. Rehearse 3×.

## 11. Testing checklist
- [ ] hands: 20/20 squeezes detected; effort differs between gentle and full; no false positives; recalibration works
- [ ] Spacebar backup works with the hands process stopped
- [ ] Phone works on 2 different phones simultaneously; timing feedback shows; RTT < 100 ms on the LAN
- [ ] Dashboard runs 30 min without memory growth or slowdown; audio unlocked
- [ ] `/api/device` valid JSON, exactly 104 LED chars, 16-char LCD lines, seq increments
- [ ] Unplug the internet: server keeps running (Tiger → CSV, cached audio, template narration)
- [ ] Kill/restart hands.py: the server doesn't crash; reconnects
- [ ] Fast-forward + planner run without freezing the dashboard (they run in threads)
- [ ] Card A and Card B flows end to end

## 12. Gotchas
- DST tonight: never use local time; `time.monotonic()` for events, UTC `datetime.now(timezone.utc)` for DB timestamps.
- Browsers block autoplay audio until a user gesture.
- The phone must hit the laptop's **LAN IP**, not `localhost`.
- FastAPI WebSocket broadcast: track clients in a set and drop dead ones; never `await` slow work inside the broadcast loop.
- `asyncio.to_thread` for everything A provides that might take > 20 ms (`submit_attempt` ~40 ms, fast-forward, planner, twins).
- Don't show the patient's hidden traits on screen (the twins are supposed to infer them).
- No strobe effects anywhere (photosensitivity).
