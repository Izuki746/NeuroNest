# NeuroNest: UNO Q Linux side (Person B)
#
# Polls the laptop server's GET /api/device (JSON contract: handoff/SHARED_CONTEXT.md §7.5)
# and forwards it to the microcontroller sketch over Bridge.
# Standard library only, so nothing needs pip-installing on the board.
#
# Set SERVER to the demo laptop's LAN IP (run `ipconfig` on that laptop).

import json
import time
import urllib.request

from arduino.app_utils import App, Bridge

SERVER = "http://192.168.137.1:8000"  # demo laptop on its Windows Mobile Hotspot (change if the laptop IP differs)
POLL_S = 0.1
FAILS_BEFORE_DEMO = 8          # ~8+ s of no replies before showing 'NO LAPTOP LINK' (WiFi hiccups are normal)

last_seq = None
fails = 0
in_demo = False
last_log = 0.0
latency_ms = 0.0


def call(name, *args):
    try:
        return Bridge.call(name, *args)
    except Exception as e:
        print(f"Bridge.call({name}) failed: {e}")
        return None


FULL_REFRESH_S = 2.0
last_full = 0.0


def set_demo(on):
    """Offline mode on the board shows 'no laptop link', never its own numbers, so it can't disagree with the website."""
    global in_demo, last_seq
    if on != in_demo:
        in_demo = on
        call("demo", 1 if on else 0)
        if not on:
            last_seq = None          # force a full redraw from the server the moment we reconnect
        print("OFFLINE: no laptop link" if on else "reconnected: following server")


def fetch_device():
    global latency_ms
    t0 = time.monotonic()
    with urllib.request.urlopen(SERVER + "/api/device", timeout=1.5) as r:
        data = json.loads(r.read().decode("utf-8"))
    latency_ms = (time.monotonic() - t0) * 1000
    return data


def rgb_leds(d):
    """6 chars for the board's RGB LEDs: LED3 = stimulation, LED4 = status colour (from the LCD rgb)."""
    stim = d.get("stim")
    led3 = {"excite": "001", "calm": "110"}.get(stim, "000")     # blue / yellow
    r, g, b = (list(d.get("rgb", [40, 80, 255])) + [0, 0, 0])[:3]
    led4 = "".join("1" if v >= 128 else "0" for v in (r, g, b))   # red / amber / green / blue / white
    return led3 + led4


def apply(d):
    leds = str(d.get("leds", ""))[:104].ljust(104, "0")
    l1 = str(d.get("lcd1", "")).replace("|", " ")[:16]
    l2 = str(d.get("lcd2", "")).replace("|", " ")[:16]
    r, g, b = (list(d.get("rgb", [40, 80, 255])) + [0, 0, 0])[:3]
    call("setLeds", leds)
    call("setLcd", f"{l1}|{l2}|{int(r)}|{int(g)}|{int(b)}")
    call("setServo", int(d.get("servo", 0)))
    call("setRgbLeds", rgb_leds(d))


last_reset = None


def loop():
    global last_seq, fails, last_log, last_full, last_reset
    try:
        d = fetch_device()
        fails = 0
        set_demo(False)
        now = time.monotonic()
        # website reset / new patient: wipe the board back to its initial state, then redraw from the server
        if d.get("reset_id") != last_reset:
            if last_reset is not None:
                call("resetAll")
                print("reset from website: board cleared")
            last_reset = d.get("reset_id")
            last_seq = None
        # redraw on every change, and also every 2 s regardless, so the board can never drift from the website
        if d.get("seq") != last_seq or now - last_full > FULL_REFRESH_S:
            apply(d)
            last_seq = d.get("seq")
            last_full = now
    except Exception as e:
        fails += 1
        if fails == FAILS_BEFORE_DEMO:
            print(f"server unreachable ({e}); switching to offline demo")
        if fails >= FAILS_BEFORE_DEMO:
            set_demo(True)
            time.sleep(1)          # retry every second while offline
            return

    now = time.monotonic()
    if now - last_log > 1.0:
        last_log = now
        print(f"seq={last_seq} latency={latency_ms:.0f}ms demo={in_demo}")
    time.sleep(POLL_S)


print("NeuroNest Uno Q poller starting, server =", SERVER)
print("ping ->", call("ping"))
App.run(user_loop=loop)
