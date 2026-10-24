"""Fake /api/device server for testing the Uno Q before Person C's real server exists.

Run on any laptop on the same WiFi as the Uno Q:
    python unoq/tools/fake_device_server.py            (port 8000)
Then set SERVER in unoq/python/main.py to http://<this-laptop-ip>:8000

It animates a stroke -> rehab -> recovery story using the exact /api/device JSON
format from handoff/SHARED_CONTEXT.md §7.5. Standard library only.
"""
import json
import math
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

START = time.monotonic()
LEFT_MOTOR = {(r, c) for r in range(2, 5) for c in range(1, 5)}


def pad(s):
    return s[:16].ljust(16)


def device_state():
    t = (time.monotonic() - START) % 40.0
    recovery = min(max((t - 8) / 27.0, 0.0), 1.0)
    phase = (t % 2.0) / 2.0
    wave = phase * 8 if t > 8 else -10
    leds = []
    for r in range(8):
        for c in range(13):
            if c == 6:
                leds.append("1")
                continue
            v = 2 + math.sin(t * 0.8 + r * 0.6 + c * 0.4)
            if t > 5 and (r, c) in LEFT_MOTOR:
                dim = (t - 5) / 3 if t < 8 else 1 - recovery * 0.7
                v *= 1 - dim
            if c < 6 and abs(r - wave) < 1:
                gain = (0.3 + 0.7 * recovery) if (r, c) in LEFT_MOTOR else 1
                v += 4 * (1 - abs(r - wave)) * gain
            leds.append(str(max(0, min(9, round(v)))))
    arm = int(25 + recovery * 55)
    day = int(recovery * 90)
    if t < 5:
        l1, l2, rgb = "NEURONEST", "STROKE SIM READY", [40, 80, 255]
    elif t < 8:
        l1, l2, rgb = "STROKE: L MOTOR", "ARM 25%", [255, 40, 40]
    else:
        l1, l2 = f"DAY {day}  ARM {arm}%", f"EFFORT {70 + int(20 * math.sin(t))}%"
        rgb = [255, 40, 40] if arm < 35 else [255, 160, 0] if arm < 70 else [40, 220, 80]
    servo = int(90 * arm / 100) if (t > 8 and phase < 0.4) else 0
    return {
        "seq": int(t * 10),
        "leds": "".join(leds),
        "lcd1": pad(l1), "lcd2": pad(l2), "rgb": rgb, "servo": servo,
        "lcd3": pad("DAY90 FORECAST"), "lcd4": pad("78% +/- 9"),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/device"):
            body = json.dumps(device_state()).encode()
        elif self.path.startswith("/api/health"):
            body = json.dumps({"ok": True, "t_ms": time.monotonic() * 1000}).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Fake device server on http://{lan_ip()}:{port}/api/device  (Ctrl+C to stop)")
    print("Put that address (without /api/device) into SERVER in unoq/python/main.py")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
