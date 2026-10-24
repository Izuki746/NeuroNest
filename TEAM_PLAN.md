# Lightswitch: Rewire · Team Plan + Hardware Guide

OxHack 2026 · Hacking ends **12:00 Sunday** · Judging 13:00–15:45
Clocks go back tonight (02:00 → 01:00), so we get **25 hours**.
Full project description: `PROJECT.md`

---

## Part 1: Roles

| | **A: Brain + AI** | **B: Hardware** | **C: Experience** |
|---|---|---|---|
| **Owns** | Virtual brain, stroke, rewiring, patients, digital twins, forecaster, planner | Uno Q setup, LED brain map, Grove LCD, servo arm, misc LCD, physical presentation | Webcam hand tracking + effort, server, dashboard (3D brain), phone app, Tiger Data, Gemini + ElevenLabs, Devpost |
| **Main files** | `brain/`, `ai/` | `unoq/` | `inputs/`, `storage/`, `server/`, `web/` |
| **Skills used** | Python, numpy, maths | Arduino, wiring, debugging | Python, web (JS/HTML), APIs |
| **Demo role** | Explains the AI to judges | Runs the hardware, hands judges the "patient" | Main presenter |

Claude writes the first version of most code. Each person **runs it, tests it, tunes it and owns it**. When something breaks, paste the error into the chat.

### Ground rules
1. **Everything in one GitHub repo.** Commit small and often. Pull before you start working.
2. **Agree the message formats in Part 3 first.** Then all three can build in parallel without waiting on each other.
3. **Every checkpoint has a fake-data fallback.** E.g. C can build the dashboard against fake brain data before A's brain exists.
4. **Hard stop for new features at 11:00 Sunday.**
5. **Sleep in shifts** (see the timeline). One rested person beats three zombies at the demo.

---

## Part 2: Timeline

Times are approximate. Adjust to when you actually start.

### Phase 0: Setup (first 45 min, everyone)
| Who | Task | Done when |
|---|---|---|
| All | Create GitHub repo, clone, copy in `PROJECT.md` / `TEAM_PLAN.md` / `data/` | All three can push |
| All | Claim API keys: Tiger Data (mlh.link/tigerdata), Gemini (mlh.link/aistudio), ElevenLabs (mlh.link/elevenlabs) | Keys in a shared `.env` (**not committed**) |
| A | `pip install numpy scipy scikit-learn lightgbm` | Imports work |
| B | Install **Arduino App Lab**, plug in the Uno Q, connect to WiFi (Part 4.1) | Built-in LED example runs |
| C | `pip install mediapipe opencv-python fastapi uvicorn websockets psycopg[binary]`; plug in the webcam | Webcam test shows video (Part 4.6) |

### Phase 1: Core pieces (until ~18:00, dinner)
| Who | Task | Checkpoint |
|---|---|---|
| A | Load brain map; activity simulation with delays + hemispheres holding each other in check; movement attempt → arm output; stroke damage + knock-on dimming | **CP1: a stroke visibly lowers arm output** |
| B | LED matrix lights individual dots from code; Grove LCD shows text + colour; servo sweeps | Each part works on its own |
| C | `hands.py`: live openness 0–1, squeeze detection, calibration, **effort score**; FastAPI server broadcasting fake brain state | "SQUEEZE! effort 82%" prints reliably |

### Phase 2: Join it up (18:00–00:00)
| Who | Task | Checkpoint |
|---|---|---|
| A | Rewiring rule (timing + effort + closing window); stimulation (excite/calm); days passing; fast-forward | **CP2: well-timed stimulation clearly beats badly-timed** |
| B | Uno Q receives messages over WiFi (Part 3) and shows them: brain map on LEDs, status on LCD, arm angle on servo | Laptop sends a fake message → hardware reacts |
| C | Plug the real brain into the server; webcam squeezes drive attempts; dashboard v1 (76 glowing nodes in 3D + arm % + effort gauge); phone page with 🔵/🟡 buttons | **CP3: squeeze at webcam → brain lights up on dashboard AND LEDs** |

**00:00 midnight pizza (HRT): full team check-in. Demo whatever works end to end.**

### Phase 3: AI + data (00:00–06:00, staggered sleep)
| Who | Task | Sleep |
|---|---|---|
| A | Patient generator (traits + cards); simulate thousands of patients → Tiger Data; forecaster; digital twins | Sleep 04:00–06:30 (let the patient simulation run while asleep) |
| B | Physical layout (LED brain labelled with paper/pen, servo arm with a paper strip, cable tidy); help C with the phone page | Sleep 00:30–03:30 |
| C | Tiger Data tables + logging; dashboard: recovery curve, forecast band, rewiring connections thickening | Sleep 02:30–05:30 |

**CP4: forecast band shrinks over simulated days on the dashboard.**

### Phase 4: Wow features + polish (06:00–11:00)
| Who | Task |
|---|---|
| A | Therapy planner (AI plan vs standard vs no rehab curves); tune patient cards A and B so they get different plans |
| B | Final hardware build; test everything 5+ times in a row; backup plan if WiFi drops (Part 5) |
| C | ElevenLabs voice coach + narrator; Gemini report; **09:00 Devpost workshop (C1)**; start the Devpost write-up |

**08:00–09:00 breakfast · 10:00 full rehearsal #1**

### Phase 5: Lock + submit (11:00–12:00)
- **11:00 feature freeze.** Bug fixes only.
- Record a 2-minute backup demo video in case the live demo fails.
- Submit Devpost (screenshots, video, tech list, credit The Virtual Brain data). **Submit by 11:45, not 11:59.**
- Rehearsal #2 with roles from Part 6.

---

## Part 3: How the pieces talk (agree this first)

The **server (laptop)** is the hub. Everything connects to it with WebSockets on the venue WiFi.

```
hands.py  ──attempt──►                 ┌──state──► dashboard (browser)
phone     ──stim─────►  SERVER (laptop)├──state──► phone (browser)
dashboard ──control──►  brain + AI     └──device─► Uno Q (Python app → Bridge → sketch)
```

### Messages INTO the server
```json
{"type": "attempt", "t": 1730.120, "effort": 0.82, "depth": 0.9, "speed": 0.7, "hold_ms": 400}
{"type": "stim", "t": 1730.040, "region": "lPMCDL", "kind": "excite", "who": "phone-3"}
{"type": "control", "action": "new_patient", "card": "A"}
{"type": "control", "action": "fast_forward", "days": 90}
```

### Messages OUT of the server (~20 per second)
To the dashboard and phones:
```json
{"type": "state", "day": 23, "arm": 0.47, "effort": 0.82,
 "activity": [0.1, 0.0, ...76 numbers],
 "lesion": ["lM1"], "stim": {"region": "lPMCDL", "kind": "excite"},
 "timing_ms": -80, "forecast": {"p10": 0.69, "p50": 0.78, "p90": 0.87}}
```

To the Uno Q (simpler, ~10 per second):
```json
{"type": "device",
 "leds": "104 characters, one per LED: 0 = off ... 9 = brightest",
 "lcd1": "DAY 23  ARM 47%", "lcd2": "STIM 80ms EARLY",
 "rgb": [255, 160, 0], "servo": 64}
```

---

## Part 4: Hardware guide

### 4.1 Arduino Uno Q (B owns)
**What it is:** two computers on one board.
- **Linux side** (Qualcomm processor running Debian): runs **Python**, has **WiFi**. Our job here: receive `device` messages from the laptop.
- **Microcontroller side** (STM32): runs a normal **Arduino sketch**. Our job here: drive the LEDs, LCD and servo with precise timing.
- They talk through **Bridge**: Python calls a function, the sketch runs it.

**Setup steps:**
1. Install **Arduino App Lab** on a laptop (arduino.cc → App Lab).
2. Plug the Uno Q into the laptop with **USB-C**. Wait about a minute for it to boot.
3. Open App Lab, select the board, follow the setup to **connect it to the venue WiFi**. Note its IP address.
4. Open a built-in **LED matrix example** and run it. LEDs light up = working ✅
5. Create a new App called `lightswitch`. It has two parts:
   - **Python** (`main.py`): connects to the laptop server's WebSocket, receives `device` messages, and calls the sketch through Bridge.
   - **Sketch** (`sketch.ino`): provides functions `setLeds(string)`, `setLcd(line1, line2, r, g, b)`, `setServo(angle)`.
6. Claude writes both files. Copy them in, press Run, and report what happens.

**Tips:**
- If the board stops responding: unplug, wait 10 s, replug.
- Always check the **App Lab console** (both the Python and sketch logs) when something doesn't work. Paste errors into the chat.
- Its pins use **3.3V logic**; the **5V pin** is for powering parts like the servo and LCD.
- Compare with App Lab's own examples if Bridge syntax errors appear. The examples are the source of truth.

### 4.2 LED matrix (built into the Uno Q, B owns)
**What it is:** 8 rows × 13 columns = 104 small LEDs on the board.

**Our layout: the brain seen from above.**
```
col:  0 1 2 3 4 5 | 6 | 7 8 9 10 11 12
      LEFT BRAIN    gap   RIGHT BRAIN
row 0 = front of the brain (forehead)
row 7 = back of the brain
```
- 38 regions per hemisphere fit into the 48 LEDs on each side. Each region is placed by its real front-to-back and side-to-side position (A's code does the mapping).
- **Off** = stroke damage · **dim** = quiet · **bright** = active · **slow pulse** = being stimulated.
- **Rule: no fast flashing** (nothing changing more than ~3 times per second). It's a brain-health project, and flashing light can affect photosensitive people.

**Making it readable:** draw a brain outline on paper, cut a rectangle for the matrix, and label "FRONT", "LEFT", "RIGHT" and the motor area. Put a sheet of plain paper over the LEDs to soften the glow into "regions".

**Using it in the sketch:** use the `Arduino_LED_Matrix` library. Start from App Lab's LED example; our `setLeds()` turns the 104-character string into a frame. If the Uno Q supports brightness levels, use them; if not, levels 0–4 = off and 5–9 = on.

### 4.3 Grove LCD RGB backlight (B owns)
**What it is:** a 16-character × 2-line text screen with a backlight that can be any colour. It talks over **I2C** (2 data wires + power).

**Check first:** read the label on the back.
- **V4.0** prefers 5V. **V5.0** works at 3.3V or 5V.
- If unsure, power it from 5V. It almost always works with the Uno Q's 3.3V data lines. If the text is garbled, tell Claude.

**Wiring:** a Grove cable has 4 colours.
| Grove wire | Connects to Uno Q |
|---|---|
| **Black** | GND |
| **Red** | 5V |
| **White** | SDA |
| **Yellow** | SCL |

If your Grove cables have Grove plugs at both ends, either:
- use a **Grove-to-female-jumper** cable if one's in the kit, or
- unplug one end and push **male jumper wires** into the Grove socket on the LCD, then into the Uno Q headers (SDA/SCL are labelled on the board, near AREF).

**Software:** install the **"Grove - LCD RGB Backlight"** library (`rgb_lcd.h`):
```cpp
lcd.begin(16, 2);
lcd.setRGB(255, 160, 0);   // amber
lcd.setCursor(0, 0); lcd.print("DAY 23  ARM 47%");
lcd.setCursor(0, 1); lcd.print("STIM 80ms EARLY");
```

**What it shows:**
| Moment | Line 1 | Line 2 | Backlight |
|---|---|---|---|
| Patient chosen | `PATIENT A  74y` | `STROKE: L MOTOR` | Red |
| Calibrating | `CALIBRATING...` | `OPEN+CLOSE x3` | White |
| Rehab | `DAY 23  ARM 47%` | `EFFORT 82%` | Red → amber → green as the arm recovers |
| After a stim | `DAY 23  ARM 47%` | `STIM 80ms EARLY✓` | Same |
| Fast-forward | `>> DAY 61` | `FORECAST 78%` | Same |

**Troubleshooting:**
- Backlight on, no text → adjust nothing yet; check SDA/SCL aren't swapped.
- Nothing at all → check 5V and GND.
- Run an **I2C scanner** sketch (Claude can give you one). The LCD should appear at address `0x3E`.

### 4.4 Micro servo: the patient's arm (B owns, optional)
**What it is:** a small motor that turns to an exact angle (0–180°). It has 3 wires.

| Servo wire | Connects to |
|---|---|
| **Brown** (or black) | GND |
| **Red** | 5V |
| **Orange** (or yellow) | **D9** (signal) |

Use the breadboard if you need to share 5V/GND with the LCD: put 5V and GND on the breadboard's power rails, then connect both devices to the rails.

**How we use it:**
- Fit the longest plastic arm (horn) onto the servo. Tape a **paper strip** to it as the "forearm", and draw a hand on the end.
- **Resting position = 0°** (arm down).
- On each movement attempt, it swings up to `arm_function × effort × 90°`, then eases back down.
- Right after the stroke: a small twitch. After good rehab: a big lift. That's the most visual proof of recovery.
- Stick the servo to the table or the edge of a laptop with tape so it doesn't jump around.

**Sketch:** `Servo` library, `arm.attach(9); arm.write(angle);`. Move in small steps for smooth motion.

**Troubleshooting:**
- Uno Q resets or glitches when the servo moves → the servo is drawing too much power. Power it from a separate 5V USB supply instead, **keeping GND connected** to the Uno Q's GND.
- Jitters at rest → that's normal for cheap servos. Detach the signal when idle (`arm.detach()`).

### 4.5 Misc LCD (B owns, optional)
**First, identify it:**
- **4 pins (GND, VCC, SDA, SCL)** or a small board soldered on the back → an **I2C LCD**. Easy: wire it like the Grove LCD (it shares SDA/SCL). Library `LiquidCrystal_I2C`; the address is usually `0x27` or `0x3F` (the I2C scanner will tell you).
- **16 pins along one edge** → a **parallel LCD**. It needs ~6 data wires + a contrast knob (potentiometer). Only bother if there's spare time.
- **Anything else** → photo it and send it to Claude.

**Use (if I2C):** the forecast screen: `DAY 90 FORECAST` / `78% +/- 9`.

### 4.6 USB webcam (C owns)
**Plug into the laptop** that runs the server (not the Uno Q, unless we get a USB-C hub later).

**Test it:**
```python
import cv2
cap = cv2.VideoCapture(0)   # try 1 if 0 is the laptop's built-in camera
while True:
    ok, frame = cap.read()
    cv2.imshow("cam", frame)
    if cv2.waitKey(1) == 27: break   # Esc to quit
```

**Positioning for good hand tracking:**
- At chest height, pointing at where the visitor holds their hand, **40–70 cm** away
- **Good front lighting:** don't put it facing a bright window
- **Plain background** behind the hand if possible
- Visitor holds their palm facing the camera, fingers spread, then makes a fist

**How we use it:**
- **MediaPipe Hands** → 21 hand points → **openness 0–1**
- **Calibration:** "open and close 3 times" stores this person's open and closed values
- **Squeeze** = openness drops quickly → `attempt` message with **effort** (depth + speed + hold)
- **Phone-colour detection** (stretch): a phone screen glowing blue or yellow in the centre of the frame = stimulation

**Backup:** spacebar = attempt (hold = more effort). Always keep this working.

### 4.7 Breadboard and wires (B owns)
**Breadboard basics:**
- The **long rails along the edges** (marked + and −) are connected along their whole length. Use them for **5V and GND**.
- In the middle, each **short row of 5 holes** is connected together. The gap down the centre separates the two sides.

**Our setup:**
```
Uno Q 5V  ──► breadboard + rail ──► servo red, LCD red
Uno Q GND ──► breadboard − rail ──► servo brown, LCD black
Uno Q D9  ───────────────────────► servo orange
Uno Q SDA ───────────────────────► LCD white (and misc LCD SDA)
Uno Q SCL ───────────────────────► LCD yellow (and misc LCD SCL)
```
**Wire types:**
- **Male-to-female:** Uno Q header (female socket) → breadboard (male pin goes into the breadboard) or → device pins
- **Female-to-female:** device pin → device pin (e.g. a servo connector to header pins)

**Rules:**
- **Unplug the power before rewiring.**
- **All grounds must connect together.**
- **Never connect 5V straight to GND** (that's a short circuit).
- Use the same colours consistently (red = 5V, black = GND) so it's easy to debug at 4am.

### 4.8 Laptops
| Laptop | Runs |
|---|---|
| **Laptop 1 (demo machine)** | Server + brain + AI, webcam + hand tracking, dashboard on the big screen. Keep it plugged into power. |
| **Laptop 2** | Arduino App Lab connected to the Uno Q + B's development |
| **Laptop 3** | A's patient simulations / training overnight; spare demo screen |

**Networking:** everything must be on the **same WiFi**. If the venue WiFi blocks devices from talking to each other, use a **phone hotspot** for the laptop, the Uno Q and the visitor phones.

---

## Part 5: Backup plans
| If this fails... | ...do this |
|---|---|
| Venue WiFi blocks devices | Phone hotspot for laptop + Uno Q |
| Uno Q won't connect at all | Demo on the dashboard only; show the Uno Q running offline on a scripted loop |
| Hand tracking is flaky | Spacebar / phone button for attempts |
| Servo glitches | Remove it; the LEDs and dashboard still tell the story |
| Grove LCD dead | Show status on the dashboard |
| Tiger Data / internet down | Log locally to CSV; show pre-recorded forecast results |
| Gemini / ElevenLabs down | Pre-generated audio and report saved as files |
| Live demo crashes | 2-minute backup video recorded at 11:00 |

---

## Part 6: Demo roles (3 minutes)
| Time | Who | What |
|---|---|---|
| 0:00–0:20 | **C** | The problem: every stroke is different, and the recovery window is short |
| 0:20–0:50 | **B** | Judge picks a stroke location. Point at the LED brain going dark, the arm going limp |
| 0:50–1:50 | **C + judge** | Calibration, the judge squeezes, the effort gauge responds, B times the stimulation from the phone, the arm lifts higher, connections thicken on the dashboard |
| 1:50–2:30 | **A** | The forecast band shrinks (digital twins). The planner: "AI plan 82% vs standard 61%." Patient A vs B get different plans |
| 2:30–3:00 | **C** | Vision + honesty: a simulator, not a medical device. Thanks + tech list |

**Practise this at least 3 times before judging.**

---

## Part 7: Checklist before judging (13:00)
- [ ] Devpost submitted (by 11:45)
- [ ] Backup video recorded
- [ ] Demo laptop charged + plugged in; second laptop ready
- [ ] Uno Q powered and connected; LEDs + LCD showing the "ready" screen
- [ ] Webcam positioned, lighting checked, calibration tested
- [ ] Phone page QR code printed or on screen
- [ ] Patient cards A and B tested and giving different plans
- [ ] Voice volume checked
- [ ] Everyone knows their demo part
