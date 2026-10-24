# PERSON B: Hardware brief (Arduino UNO Q, LED brain map, Grove LCD, servo arm, misc LCD)

> **For Claude:** your human is **Person B**. They own `unoq/` and the physical setup. They're a CS student who may be new to Arduino, so give **exact click-by-click and wire-by-wire steps**, ask them to report what they see (or paste console output), and debug from there. **First read `handoff/SHARED_CONTEXT.md` fully**, especially Section 5 (hardware inventory) and Section 7.5 (the `/api/device` JSON you consume). This brief covers everything B must build, test and present.
>
> ⚠️ **Verify APIs on the device.** The UNO Q is new (2025) and runs a Zephyr-based Arduino core plus App Lab. The Bridge and LED-matrix code below is the expected shape; **check it against the examples bundled in App Lab** (the source of truth) and adapt. When something fails to compile or run, read the exact error, check the matching built-in example, then fix.

---

## 0. Context recap
- **Project:** *Lightswitch: Rewire*, a stroke-recovery flight simulator (OxHack 2026, theme TIME). A virtual brain gets a stroke; visitors rehab it with webcam hand squeezes and timed stimulation; the AI forecasts and plans recovery.
- **Your job:** make the virtual brain **physical and visible**. The UNO Q's LED matrix is a **map of the brain seen from above** (the stroke area goes dark, activity glows, the stimulated region pulses). The Grove LCD shows the status with a coloured backlight. The servo is the **patient's arm** (barely moves after the stroke, lifts more as they recover). Optionally, the misc LCD shows the forecast.
- **How you get data:** the laptop server (Person C) exposes `GET http://<laptop-ip>:8000/api/device` returning JSON (SHARED_CONTEXT §7.5). The UNO Q's **Linux side** polls it 10×/s and passes values to the **microcontroller side** through **Bridge**, which drives the hardware.
- **Prizes you carry:** Citadel "Best Handmade Project", and the whole "wow, it's physical" factor at judging.
- **Demo role:** at 0:20–0:50 you have a judge pick a patient card and point out the LED brain going dark and the arm going limp; during rehab you time the stimulation on the phone.

## 1. Deliverables and timeline
| # | Deliverable | Done when | Target |
|---|---|---|---|
| 1 | UNO Q set up in App Lab, on WiFi, built-in LED example runs | LEDs light | **first 45 min** |
| 2 | Wiring: Grove LCD + servo via breadboard | I2C scanner sees 0x3E; servo sweeps | +1.5 h |
| 3 | `unoq/sketch/sketch.ino` with Bridge functions `setLeds`, `setLcd`, `setServo` (+ `ping`) | Each callable from Python with test values | **CP1 ~17:00** |
| 4 | `unoq/python/main.py` polling the laptop `/api/device` | Laptop sends a fake JSON → hardware reacts | **CP2 ~21:00** |
| 5 | Offline `--demo` animation mode | Unplugged from WiFi, it still loops a convincing stroke→recovery story | ~23:00 |
| 6 | Full loop with the real server | Squeeze at webcam → LEDs + LCD + servo react | **CP3 00:00** |
| 7 | Physical presentation: paper brain overlay, labels, servo arm, cable tidy, patient cards, QR sign | Looks intentional and readable from 1 m | before sleep (00:30) + morning |
| 8 | Misc LCD (optional) | Shows `lcd3/lcd4` | morning, if time |
| 9 | Reliability: 5 consecutive full demos without touching hardware | ✓ | by 10:00 |
| 10 | Photos + a short clip of the hardware for Devpost | uploaded to the shared folder | by 10:30 |

Sleep: **00:30–03:30**. Before sleeping, make sure everything committed works unattended.

**If you finish early**, help C with: the phone stim page (`web/phone/`), printing/drawing the QR sign, the spacebar backup (`inputs/keyboard.py`), and testing on several phones.

---

## 2. Hardware facts (read once)

### 2.1 Arduino UNO Q
- **Two computers on one board:**
  - **MPU (Qualcomm Dragonwing) running Debian Linux:** runs Python, has WiFi. We run `main.py` here.
  - **MCU (STM32U585):** runs the Arduino sketch (Zephyr-based core). Drives the pins, the LED matrix and I2C.
  - **Bridge:** RPC between them. Python calls `Bridge.call("setLeds", s)`; the sketch registers `Bridge.provide("setLeds", fn)`.
- **8×13 LED matrix** built in (104 LEDs, single colour). It may support a few grey levels (check the example; grayscale is often 3-bit, levels 0–7).
- **Headers:** UNO form factor. MCU pins are **3.3 V logic**. There are **5V** and **GND** pins for powering parts. SDA/SCL are on the header (labelled, near AREF), and there's a Qwiic connector (a second I2C bus).
- **Power:** USB-C. The board takes ~1 minute to boot.
- **Programmed with Arduino App Lab** (desktop app). An App = `python/` + `sketch/` + an app config file. Our `unoq/` folder mirrors that.

### 2.2 Grove LCD RGB Backlight
- 16×2 text + RGB backlight, **I2C**. LCD controller at **0x3E**; backlight chip at **0x62 (V4.x)** or **0x30 (V5.x)**. **Read the version on the back and tell Claude.**
- Power: V4 is specified for 5V; V5 works at 3.3–5V. Plan: **power from 5V**; data lines from the UNO Q's 3.3V SDA/SCL usually work (the I2C pull-ups on the module are what matter). If text is garbled or the scanner can't see it, try powering it from **3V3** instead.

### 2.3 Micro servo (SG90-type)
- 3 wires: **brown/black = GND**, **red = 5V**, **orange/yellow = signal**.
- Signal = a 50 Hz pulse: ~1.0 ms = 0°, ~1.5 ms = 90°, ~2.0 ms = 180° (exact range varies; we only use **0–90°**).
- Can draw up to ~500 mA when it moves or stalls, which can **brown out the board**. Fix: power the servo from a separate 5V USB supply (a phone charger with a cut cable, or a USB breakout) with **GND shared** with the UNO Q.

### 2.4 Misc LCD (unidentified)
- **4 pins (GND/VCC/SDA/SCL)** or a small soldered board on the back (PCF8574 backpack) → **I2C LCD**, address 0x27 or 0x3F. Library `LiquidCrystal_I2C` (or a compatible fork). Shares SDA/SCL with the Grove LCD (different address, so fine).
- **16 pins** → parallel HD44780. Needs ~6 GPIOs + a contrast potentiometer. **Skip it** unless everything else is done.
- **Anything else:** photograph it, describe the pins and the markings, and Claude identifies it.

### 2.5 Breadboard + wires
- The long **+ / − rails** run the full length (some boards split them in the middle; check with a wire or a multimeter). Use them for **5V (red)** and **GND (black/blue)**.
- The middle rows: each 5-hole row strip is connected; the centre gap separates the two sides.
- **M-F jumpers:** female end onto pins (servo connector, LCD pins), male end into the breadboard or a header socket. **F-F jumpers:** pin to pin. The UNO Q headers are female sockets, so male ends go in.
- **Grove cables:** black GND, red VCC, white SDA, yellow SCL. If they're Grove-to-Grove only: plug one end into the LCD, then push male jumper pins firmly into the free Grove plug's 4 contacts (or carefully into the LCD's Grove socket) and run them to the UNO Q.

---

## 3. Step-by-step

### Step 1: App Lab + board bring-up (first 45 min)
1. On B's laptop: download **Arduino App Lab** from arduino.cc (search "Arduino App Lab download") and install it.
2. Plug the UNO Q in with USB-C. Wait ~60 s.
3. Open App Lab → it should detect the board. Follow the first-run setup (name, password, **WiFi**). Use the **same network the demo laptop uses**. If the venue WiFi needs a web login, or blocks devices from seeing each other, use a **phone hotspot** for both the UNO Q and Laptop 1 (decide with C now).
4. Note the board's **IP / hostname**. Test from a laptop: `ping <board-ip>`.
5. Open the built-in **LED matrix example** (in App Lab's examples) → Run. **LEDs animate = pass.** Take note of the example's include lines and the draw function signature; you'll copy that pattern.
6. Also open any example that uses **Bridge** (Python ↔ sketch). Note the exact imports (Python) and include + `provide` syntax (sketch). Write them into `unoq/README.md`.
7. Optional but useful: App Lab may give a terminal/SSH into the Linux side. Check `python3 --version` and `python3 -c "import json, urllib.request; print('ok')"`.

**Report to Claude:** board detected? WiFi OK? Example ran? What were the include/import lines?

### Step 2: Wiring (unplug USB while wiring)
```
UNO Q 5V   ──red────► breadboard + rail
UNO Q GND  ──black──► breadboard − rail

Grove LCD:  black → − rail      red → + rail (5V)
            white → UNO Q SDA   yellow → UNO Q SCL

Servo:      brown → − rail      red → + rail (5V)
            orange → UNO Q D9

(Misc I2C LCD, optional): GND → − rail, VCC → + rail, SDA → SDA, SCL → SCL
   (put SDA/SCL on a breadboard row if several devices share them)
```
Rules: **unplug before rewiring**; **all GNDs connected**; **never connect 5V to GND**; red = power, black = ground, always.

**Tests:**
1. **I2C scanner sketch** (Claude writes it with `Wire`): it should print `0x3E` and `0x62` (or `0x30`), plus `0x27`/`0x3F` if the misc I2C LCD is connected. Nothing found → check SDA/SCL aren't swapped, check power, try 3V3 power for the LCD.
2. **Grove LCD hello:** `DAY 1  ARM 30%` on line 1, red backlight. Use the official **"Grove - LCD RGB Backlight"** library (`rgb_lcd.h`) first. If it won't compile on the UNO Q core, Claude writes a minimal `Wire`-based driver by porting the Seeed library's source (github.com/Seeed-Studio/Grove_LCD_RGB_Backlight: copy its exact init sequence, command/data control bytes and the backlight register writes for your version, V4 at 0x62 or V5 at 0x30). Don't guess register values; take them from that source.
3. **Servo sweep** 0→90→0 slowly. If the **Servo** library isn't supported on this core, Claude writes a non-blocking software pulse: every 20 ms set D9 HIGH, wait `1000 + angle*1000/180` µs (`delayMicroseconds`), set LOW. Or use PWM if the core supports setting 50 Hz. If the board resets when the servo moves → use separate servo power (Section 2.3).

### Step 3: The sketch (`unoq/sketch/sketch.ino`)
**Provided functions (Bridge):**
| Name | Args | Does |
|---|---|---|
| `ping` | none | returns "pong" (health check) |
| `setLeds` | `String` of 104 chars '0'–'9' | converts to a frame, draws on the matrix |
| `setLcd` | `String line1, String line2, int r, int g, int b` | writes the Grove LCD (only if changed) + backlight |
| `setLcd2` | `String line1, String line2` | misc LCD (optional) |
| `setServo` | `int angle` 0–90 | sets the **target**; `loop()` moves toward it smoothly |
| `demo` | `int on` | 1 = start offline demo animation, 0 = stop |

**Expected shape (adapt to the App Lab examples):**
```cpp
#include <Arduino_RouterBridge.h>   // check the exact header name in App Lab's Bridge example
#include <Arduino_LED_Matrix.h>      // check the exact class name/API in the LED example
#include <Wire.h>
#include "rgb_lcd.h"

Arduino_LED_Matrix matrix;           // class name per example
rgb_lcd lcd;
uint8_t frame[104];
volatile int servoTarget = 0; float servoPos = 0;
String lastL1, lastL2; int lastR=-1,lastG=-1,lastB=-1;

String ping() { return "pong"; }
void setLeds(String s) {
  for (int i = 0; i < 104 && i < (int)s.length(); i++) {
    int v = s[i] - '0';                 // 0..9
    frame[i] = map(v, 0, 9, 0, 7);      // if grayscale 3-bit; else frame[i] = v >= 5
  }
  matrix.draw(frame);                   // per example
}
void setLcd(String l1, String l2, int r, int g, int b) {
  if (l1 != lastL1) { lcd.setCursor(0,0); lcd.print(l1); lastL1 = l1; }
  if (l2 != lastL2) { lcd.setCursor(0,1); lcd.print(l2); lastL2 = l2; }
  if (r!=lastR||g!=lastG||b!=lastB) { lcd.setRGB(r,g,b); lastR=r; lastG=g; lastB=b; }
}
void setServo(int a) { servoTarget = constrain(a, 0, 90); }

void setup() {
  Bridge.begin();
  Bridge.provide("ping", ping);
  Bridge.provide("setLeds", setLeds);
  Bridge.provide("setLcd", setLcd);
  Bridge.provide("setServo", setServo);
  matrix.begin();                       // + setGrayscaleBits(3) if available
  Wire.begin(); lcd.begin(16, 2); lcd.setRGB(40,80,255); lcd.print("LIGHTSWITCH");
  // servo init (library attach(9) or pin mode for software pulses)
}
void loop() {
  // smooth servo: move ≤ 3° per 20 ms toward target, then pulse/write
  // if demo mode: advance the offline animation
}
```
Rules: Bridge handlers must be **fast** (no `delay()` in them). Do the slow work (servo pulses, animation) in `loop()`. Only rewrite the LCD when the text changes (I2C writes are slow and cause flicker).

**Bridge-only test** (before networking): a tiny Python app that calls `setLeds` with a moving bar, `setLcd("TEST","BRIDGE OK",0,255,0)` and `setServo(45)`.

### Step 4: The Linux-side poller (`unoq/python/main.py`)
- Uses **only the standard library** (`urllib.request`, `json`, `time`), so no pip on the board.
- Config at the top (or an env var): `SERVER = "http://192.168.x.x:8000"` (Laptop 1's IP; get it from C, via `ipconfig` on Laptop 1).
- Loop every **100 ms**:
  1. `GET {SERVER}/api/device` with a 0.5 s timeout.
  2. If `seq` changed: `Bridge.call("setLeds", leds)`, `Bridge.call("setLcd", lcd1, lcd2, *rgb)`, `Bridge.call("setServo", servo)`; and `setLcd2(lcd3, lcd4)` if the misc LCD is enabled.
  3. On **3 consecutive failures** (server down/WiFi lost): call `demo(1)` so the hardware keeps showing the offline story, and keep retrying every 2 s. On success: `demo(0)`.
- Log one line per second (seq, latency) to the App Lab console.
- Expected shape:
```python
from arduino.app_utils import App, Bridge   # check against App Lab's Bridge example
import json, time, urllib.request
SERVER = "http://192.168.1.50:8000"
last_seq, fails = None, 0
def loop():
    global last_seq, fails
    try:
        with urllib.request.urlopen(SERVER + "/api/device", timeout=0.5) as r:
            d = json.loads(r.read())
        fails = 0
        if d["seq"] != last_seq:
            Bridge.call("setLeds", d["leds"])
            Bridge.call("setLcd", d["lcd1"], d["lcd2"], *d["rgb"])
            Bridge.call("setServo", int(d["servo"]))
            last_seq = d["seq"]
    except Exception as e:
        fails += 1
        if fails == 3: Bridge.call("demo", 1)
    time.sleep(0.1)
App.run(user_loop=loop)
```
**Test without C's server:** run `python -m http.server 8000` in a folder containing `api/device` (a JSON file) on any laptop, edit the JSON by hand, and watch the hardware change. Or ask C for the fake server (C ships one early).

### Step 5: Offline demo mode (in the sketch)
A self-contained ~40 s loop for when the network fails:
1. 0–5 s: whole brain gently glowing (healthy), LCD `LIGHTSWITCH` / `REWIRE`, blue.
2. 5–8 s: the left motor area (where lM1/lPMC sit in the layout; hard-code their LED indices from `led_layout()`, ask A/C for the printout) fades to dark; LCD `STROKE: L MOTOR` red; servo limp.
3. 8–35 s: repeated "attempts": a bump travels front→back down the left hemisphere every 2 s; the servo lift grows 10°→60°; LCD `DAY n  ARM xx%` counting up; backlight red→amber→green.
4. Loop. **No fast flashing** (≤ 3 changes/s per LED).

### Step 6: Integration with the real server (CP3)
- Agree with C on the network (venue WiFi vs hotspot) and Laptop 1's IP. **Reserve it:** if the IP changes, update `SERVER`.
- Windows Firewall on Laptop 1 must allow inbound port 8000 (C handles it; test with a phone browser hitting `http://<ip>:8000/api/health`).
- Full test: C runs the server + hands; you watch the LEDs dim on patient creation, glow on squeezes, the LCD update, the servo lift.
- Latency check: from squeeze to LED change should be < ~0.8 s (0.4 s is by design). If it's slower, poll faster (every 50 ms) or check the WiFi.

### Step 7: Physical presentation (Citadel handmade prize)
No cardboard? Use **paper** (ask organisers or the MLH table for paper, tape and markers), a notebook back, or a takeaway box.
1. **Brain overlay:** draw a top-down brain outline (two hemispheres, front at the top) sized to the LED matrix. Cut a window. Lay a thin plain sheet over the LEDs to diffuse them so they glow as "regions". Label **FRONT**, **LEFT**, **RIGHT**, and draw an arrow to the **motor strip** (where M1 sits; get the cells from `led_layout()`).
2. **Arm:** tape a paper strip (~12 cm) to the servo horn as a forearm; draw a hand at the end; label "RIGHT ARM". Tape the servo body to the table edge or a stand so the arm swings freely from 0° (down) to 90° (up). Mark "0%" and "100%" on a paper arc behind it.
3. **LCD:** stand it up (fold paper into a stand) next to the brain; label "PATIENT MONITOR".
4. **Cables:** route them behind; tape the breadboard down. Label the 5V/GND rails.
5. **Signs:** a QR code to `http://<laptop-ip>:8000/phone/` (C's server can render one; otherwise any QR generator, done offline before WiFi issues) saying "Scan to become the stimulation team". Patient cards **A** and **B** written on paper (age, lesion, one line of story).
6. **Safety:** no loose 5V wires; nothing hot; the board is not near drinks.

### Step 8: Misc LCD (optional, morning)
If I2C: install `LiquidCrystal_I2C`, address from the scanner, `setLcd2(l1, l2)` → shows `DAY90 FORECAST` / `78% +/- 9` from `lcd3/lcd4`. If parallel: skip it, or use it only as a static sign.

---

## 4. Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| App Lab doesn't see the board | Cable is power-only; still booting | Use a data USB-C cable; wait 60 s; replug |
| Sketch won't compile: unknown header | Library/API name differs on the UNO Q core | Open the matching App Lab example and copy its includes/calls |
| `Bridge.call` fails / times out | Function name mismatch; the handler blocks | Names must match exactly; no delay() in handlers |
| I2C scanner finds nothing | SDA/SCL swapped; no power; wrong bus | Swap; check 5V/GND; try `Wire1` (Qwiic bus) if wired there |
| LCD backlight on, no text | Contrast/init issue; the library expects 5V | Try the official library; try 3V3/5V; check init |
| LCD flickers | Rewriting every poll | Only write on change (already in the sketch) |
| Board resets when the servo moves | Brownout | Separate 5V servo supply, shared GND; smaller moves |
| Servo jitters | Noisy pulses / Zephyr timing | Detach or stop pulses when at target; use the library if available |
| Poller can't reach the laptop | Different network / firewall / wrong IP | Same WiFi; `ping`; firewall allow port 8000; hotspot fallback |
| LEDs look random | Layout mismatch | Check against A's `led_layout()` printout; row 0 = front |

---

## 5. Checklist before judging
- [ ] Board boots and auto-starts the App on power (configure the App to run at startup if App Lab supports it; otherwise know how to start it in < 30 s)
- [ ] `SERVER` IP matches Laptop 1 on the demo network
- [ ] LEDs, LCD and servo respond within 1 s of a squeeze
- [ ] Offline demo kicks in if the network drops
- [ ] Overlay, labels, arm, QR sign, patient cards all in place
- [ ] Spare USB-C cable + power bank ready
- [ ] You know your demo lines (0:20–0:50) and how to time stimulation on the phone

## 6. Your demo lines (B, ~30 s)
"Pick a patient: A or B?" → (judge picks) → "Here's the brain from above: front at the top, left on the left. Watch the left motor strip." (it goes dark) "That's the stroke. And this is the patient's right arm." (servo barely twitches) "Connected areas dim too; that's called diaschisis, and you can see it spread. Now let's rehab it." Then hand over to C, and time the stimulation taps on the phone.
