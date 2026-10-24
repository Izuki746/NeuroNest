// NeuroNest: UNO Q microcontroller sketch (Person B)
//
// Linux side (python/main.py) polls the laptop server and calls these Bridge functions:
//   ping()                     -> "pong"
//   setLeds(String s)          s = 104 chars '0'..'9', row-major, row 0 = front of brain
//   setLcd(String payload)     "line1|line2|r|g|b"   (16-char lines, rgb 0..255)
//   setServo(int angle)        0..90 target; loop() moves there smoothly
//   demo(int on)               1 = offline animation, 0 = follow the server
//   setRgbLeds(String s)       6 chars '0'/'1': LED3 r,g,b then LED4 r,g,b
//                              LED3 = stimulation (blue excite / yellow calm), LED4 = patient status (red/amber/green)
//
// Handlers only copy data into buffers; all hardware I/O happens in loop() so Bridge calls stay fast.
// Optional libraries are detected with __has_include, so the sketch still compiles without them.

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <Wire.h>

#if __has_include(<rgb_lcd.h>)
  #include <rgb_lcd.h>
  #define HAVE_LCD 1
  rgb_lcd lcd;
#else
  #define HAVE_LCD 0
#endif

#if __has_include(<Servo.h>)
  #include <Servo.h>
  #define HAVE_SERVO_LIB 1
  Servo arm;
#else
  #define HAVE_SERVO_LIB 0
#endif

const int SERVO_PIN = 9;
const int N_LEDS = 104;

Arduino_LED_Matrix matrix;
uint8_t frame[N_LEDS];

// Shared state written by Bridge handlers, consumed in loop()
String pendingLeds = "";
bool ledsDirty = false;
String lcdLine1 = "NEURONEST", lcdLine2 = "STARTING...";
int lcdR = 40, lcdG = 80, lcdB = 255;
bool lcdDirty = true;
volatile int servoTarget = 0;
volatile bool demoMode = false;
String pendingRgb = "000000";
bool rgbDirty = true;

// Onboard MCU RGB LEDs are active LOW (LOW = on)
const int RGB_PINS[6] = {LED3_R, LED3_G, LED3_B, LED4_R, LED4_G, LED4_B};

float servoPos = 0;
unsigned long lastServoMs = 0;
unsigned long lastPulseMs = 0;

// ---------- Bridge handlers (keep fast) ----------
String ping() { return String("pong"); }

void setLeds(String s) {
  pendingLeds = s;
  ledsDirty = true;
}

void setLcd(String payload) {
  // "line1|line2|r|g|b"
  int p1 = payload.indexOf('|');
  int p2 = payload.indexOf('|', p1 + 1);
  int p3 = payload.indexOf('|', p2 + 1);
  int p4 = payload.indexOf('|', p3 + 1);
  if (p1 < 0 || p2 < 0 || p3 < 0 || p4 < 0) return;
  lcdLine1 = payload.substring(0, p1);
  lcdLine2 = payload.substring(p1 + 1, p2);
  lcdR = payload.substring(p2 + 1, p3).toInt();
  lcdG = payload.substring(p3 + 1, p4).toInt();
  lcdB = payload.substring(p4 + 1).toInt();
  lcdDirty = true;
}

void setServo(int angle) { servoTarget = constrain(angle, 0, 90); }

void demo(int on) { demoMode = (on != 0); }

void setRgbLeds(String s) {
  pendingRgb = s;
  rgbDirty = true;
}

void applyRgb(const String &s) {
  for (int i = 0; i < 6; i++) {
    bool on = (i < (int)s.length()) && s[i] == '1';
    digitalWrite(RGB_PINS[i], on ? LOW : HIGH);
  }
}

// ---------- Hardware helpers ----------
void drawLedString(const String &s) {
  for (int i = 0; i < N_LEDS; i++) {
    int v = (i < (int)s.length()) ? s[i] - '0' : 0;
    v = constrain(v, 0, 9);
    frame[i] = (uint8_t)((v * 7 + 4) / 9);   // 0..9 -> 0..7 (3-bit grayscale)
  }
  matrix.draw(frame);
}

String pad16(const String &s) {
  String out = s.substring(0, 16);
  while (out.length() < 16) out += ' ';
  return out;
}

String lastShown1 = "", lastShown2 = "";
int lastR = -1, lastG = -1, lastB = -1;

void updateLcd() {
#if HAVE_LCD
  String l1 = pad16(lcdLine1), l2 = pad16(lcdLine2);
  if (l1 != lastShown1) { lcd.setCursor(0, 0); lcd.print(l1); lastShown1 = l1; }
  if (l2 != lastShown2) { lcd.setCursor(0, 1); lcd.print(l2); lastShown2 = l2; }
  if (lcdR != lastR || lcdG != lastG || lcdB != lastB) {
    lcd.setRGB(lcdR, lcdG, lcdB);
    lastR = lcdR; lastG = lcdG; lastB = lcdB;
  }
#endif
}

void writeServo(float angle) {
#if HAVE_SERVO_LIB
  arm.write((int)angle);
#else
  // Software 50 Hz pulse: 1000 us = 0 deg, 2000 us = 180 deg
  unsigned long now = millis();
  if (now - lastPulseMs >= 20) {
    lastPulseMs = now;
    int us = 1000 + (int)(angle * 1000.0f / 180.0f);
    digitalWrite(SERVO_PIN, HIGH);
    delayMicroseconds(us);
    digitalWrite(SERVO_PIN, LOW);
  }
#endif
}

// Software pulses jitter on this core, so the servo is only driven while moving
// (+ a short settle time) and left unpowered-signal at rest: no buzzing when idle.
unsigned long lastMoveMs = 0;
const unsigned long SETTLE_MS = 400;

void updateServo(int target) {
  unsigned long now = millis();
  if (now - lastServoMs >= 20) {            // max 3 deg per 20 ms -> smooth movement
    lastServoMs = now;
    float diff = target - servoPos;
    if (diff > 3) diff = 3;
    if (diff < -3) diff = -3;
    if (diff != 0) lastMoveMs = now;
    servoPos += diff;
  }
  if (now - lastMoveMs < SETTLE_MS) {
#if HAVE_SERVO_LIB
    if (!arm.attached()) arm.attach(SERVO_PIN);
#endif
    writeServo(servoPos);
  } else {
#if HAVE_SERVO_LIB
    if (arm.attached()) arm.detach();
#else
    digitalWrite(SERVO_PIN, LOW);
#endif
  }
}

// ---------- Offline demo (no network) ----------
// ~40 s loop: healthy brain -> stroke in left motor area -> rehab attempts -> recovery.
// Slow changes only (no fast flashing).
// Left motor network cells from brain/layout.py: lM1 (3,1), lPMCVL (2,0), lPMCDL (2,4), lPMCM (3,4)
bool isLeftMotor(int r, int c) {
  return (r == 3 && c == 1) || (r == 2 && c == 0) || (r == 2 && c == 4) || (r == 3 && c == 4);
}

// The Zephyr toolchain on the UNO Q doesn't link libm, so no sinf/fmod/fabsf: tiny replacements.
float absf(float x) { return x < 0 ? -x : x; }
float wrapf(float x, float m) { return x - m * (float)((long)(x / m)); }   // x mod m for x >= 0
float sinApprox(float x) {
  // Bhaskara-style parabola approximation of sin, good enough for a glow animation
  const float PI_F = 3.14159265f;
  x = wrapf(x, 2 * PI_F);
  float sign = 1;
  if (x > PI_F) { x -= PI_F; sign = -1; }
  return sign * (16 * x * (PI_F - x)) / (5 * PI_F * PI_F - 4 * x * (PI_F - x));
}

// Offline mode: the laptop is unreachable. Show that honestly (never made-up metrics, so the
// board can't disagree with the website): last real brain picture breathing dimly, arm at rest,
// LCD "NO LAPTOP LINK", status LED slow amber.
void runOffline() {
  static unsigned long lastFrame = 0;
  if (millis() - lastFrame < 100) return;      // 10 fps, slow breathing (no flashing)
  lastFrame = millis();
  float breathe = 0.35f + 0.25f * (0.5f + 0.5f * sinApprox(millis() / 1000.0f * 1.5f));
  for (int i = 0; i < N_LEDS; i++) {
    int v = (i < (int)pendingLeds.length()) ? pendingLeds[i] - '0' : (i % 13 == 6 ? 1 : 2);
    v = constrain(v, 0, 9);
    frame[i] = (uint8_t)constrain((int)(v * 7.0f / 9.0f * breathe + 0.5f), 0, 7);
  }
  matrix.draw(frame);
  lcdLine1 = "NO LAPTOP LINK";
  lcdLine2 = "RECONNECTING...";
  lcdR = 255; lcdG = 160; lcdB = 0;
  updateLcd();
  servoTarget = 0;
  bool on = ((millis() / 1000) % 2) == 0;      // 0.5 Hz amber status LED
  applyRgb(on ? "000110" : "000000");
}

// (kept for reference; no longer used: it showed its own numbers, which could disagree with the website)
void runDemo() {
  unsigned long t = millis() % 40000UL;
  float sec = t / 1000.0f;
  static unsigned long lastFrame = 0;
  if (millis() - lastFrame < 100) return;   // 10 fps
  lastFrame = millis();

  float recovery = 0;                          // 0..1 over the rehab phase
  if (sec > 8) recovery = constrain((sec - 8) / 27.0f, 0.0f, 1.0f);
  float attemptPhase = wrapf(sec, 2.0f) / 2.0f; // a "squeeze" every 2 s
  float wave = (sec > 8) ? attemptPhase * 8.0f : -10;   // row position of travelling bump

  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 13; c++) {
      int i = r * 13 + c;
      float v;
      if (c == 6) { v = 1; }
      else {
        float base = 2.0f + 1.0f * sinApprox(sec * 0.8f + r * 0.6f + c * 0.4f);
        v = base;
        if (sec > 5 && isLeftMotor(r, c)) {
          float dim = (sec < 8) ? (sec - 5) / 3.0f : 1.0f - recovery * 0.7f;
          v = base * (1.0f - dim);
        }
        if (c < 6 && absf(r - wave) < 1.0f) v += 4.0f * (1.0f - absf(r - wave)) * (isLeftMotor(r, c) ? (0.3f + 0.7f * recovery) : 1.0f);
      }
      frame[i] = (uint8_t)constrain((int)(v * 7.0f / 9.0f + 0.5f), 0, 7);
    }
  }
  matrix.draw(frame);

  int day = (int)(recovery * 90);
  int arm = (int)(25 + recovery * 55);
  if (sec < 5)      { lcdLine1 = "NEURONEST"; lcdLine2 = "STROKE SIM READY"; lcdR = 40; lcdG = 80; lcdB = 255; }
  else if (sec < 8) { lcdLine1 = "STROKE: L MOTOR"; lcdLine2 = "ARM 25%"; lcdR = 255; lcdG = 40; lcdB = 40; }
  else {
    lcdLine1 = "DAY " + String(day) + "  ARM " + String(arm) + "%";
    lcdLine2 = "OFFLINE DEMO";
    if (arm < 35)      { lcdR = 255; lcdG = 40;  lcdB = 40; }
    else if (arm < 70) { lcdR = 255; lcdG = 160; lcdB = 0; }
    else               { lcdR = 40;  lcdG = 220; lcdB = 80; }
  }
  updateLcd();

  int lift = (sec > 8 && attemptPhase < 0.4f) ? (int)(90.0f * arm / 100.0f) : 0;
  servoTarget = lift;

  // LED3: blue "excite" light during each attempt; LED4: patient status colour
  String rgb = (sec > 8 && attemptPhase < 0.3f) ? "001" : "000";
  if (sec < 5)       rgb += "001";   // blue = ready
  else if (arm < 35) rgb += "100";   // red
  else if (arm < 70) rgb += "110";   // amber (red + green)
  else               rgb += "010";   // green
  applyRgb(rgb);
}

// ---------- Arduino entry points ----------
void setup() {
  Bridge.begin();
  Monitor.begin();
  Bridge.provide("ping", ping);
  Bridge.provide("setLeds", setLeds);
  Bridge.provide("setLcd", setLcd);
  Bridge.provide("setServo", setServo);
  Bridge.provide("demo", demo);
  Bridge.provide("setRgbLeds", setRgbLeds);

  for (int i = 0; i < 6; i++) { pinMode(RGB_PINS[i], OUTPUT); digitalWrite(RGB_PINS[i], HIGH); }

  matrix.begin();
  matrix.setGrayscaleBits(3);              // values 0..7
  for (int i = 0; i < N_LEDS; i++) frame[i] = (i % 13 == 6) ? 1 : 0;
  matrix.draw(frame);

  Wire.begin();
#if HAVE_LCD
  delay(200);                              // LCD needs time after power-up before init
  lcd.begin(16, 2);
  delay(50);
  lcd.setRGB(40, 80, 255);
  lcd.setCursor(0, 0); lcd.print("NEURONEST       ");
  lcd.setCursor(0, 1); lcd.print("CONNECTING...   ");
#else
  Monitor.println("rgb_lcd library not found: LCD disabled (install 'Grove - LCD RGB Backlight')");
#endif

#if HAVE_SERVO_LIB
  arm.attach(SERVO_PIN);
#else
  pinMode(SERVO_PIN, OUTPUT);
  Monitor.println("Servo library not found: using software pulses on D9");
#endif
  Monitor.println("NeuroNest sketch ready");
}

void loop() {
  if (demoMode) {
    runOffline();
  } else {
    if (ledsDirty) { ledsDirty = false; drawLedString(pendingLeds); }
    if (lcdDirty)  { lcdDirty = false; updateLcd(); }
    if (rgbDirty)  { rgbDirty = false; applyRgb(pendingRgb); }
  }
  updateServo(servoTarget);
}
