"""Drive the Uno Q from Person A's brain code, before Person C's real server exists.

    python unoq/tools/brain_device_server.py            (port 8000, card B)
    python unoq/tools/brain_device_server.py --card A

Uses brain.session.Session if A has shipped it, otherwise brain.fake.FakeSession (same API).
Every ~3 s it plays an automatic rehab attempt (random effort, sometimes with stimulation),
animates the 76 brain regions through brain.layout.led_string(), and serves the exact
/api/device JSON from handoff/SHARED_CONTEXT.md §7.5, so the board code doesn't change
when C's server replaces this.

Browser: http://<ip>:8000/  status · /new?card=A  new patient · /api/device  board feed
"""
import argparse
import json
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from brain.layout import led_string  # noqa: E402

try:
    from brain.session import Session  # real brain, once A ships it
    BRAIN = "real"
except Exception:
    from brain.fake import FakeSession as Session  # noqa: N813
    BRAIN = "fake"

ATTEMPT_EVERY_S = 3.0


def now_ms():
    return time.monotonic() * 1000


def pad(s):
    return str(s)[:16].ljust(16)


def arm_rgb(arm):
    if arm < 0.35:
        return [255, 40, 40]
    if arm < 0.7:
        return [255, 160, 0]
    return [40, 220, 80]


class Driver:
    def __init__(self, card):
        self.lock = threading.Lock()
        self.new_patient(card)

    def new_patient(self, card):
        with self.lock:
            self.card = card
            self.session = Session(card=card, seed=random.randint(0, 9999))
            st = self.session.state()
            self.health = np.array(st["health"], dtype=float)
            self.frames = None            # current attempt animation [F,76]
            self.anim_start = 0.0
            self.last_attempt = now_ms()
            self.servo_peak, self.servo_t = 0, -1e9
            self.stim, self.stim_until, self.stim_region = None, 0.0, None
            self.timing_text, self.timing_until = "", 0.0
            self.last_effort = 0.0
            self.intro_until = now_ms() + 4000
            self.seq = 0
            self.last_payload = None
            print(f"new patient {card} ({BRAIN} brain): {st['patient']}")

    def maybe_attempt(self):
        t = now_ms()
        if t - self.last_attempt < ATTEMPT_EVERY_S * 1000:
            return
        self.last_attempt = t
        effort = random.uniform(0.5, 1.0)
        stim = None
        if random.random() < 0.6:
            stim = random.choice([("lPMCDL", "excite"), ("rM1", "calm")])
            offset = random.choice([-80, -40, 0, 200, 350])
            self.session.submit_stim(t + offset, stim[0], stim[1])
        res = self.session.submit_attempt(t, effort, effort, effort, 300)
        self.frames = np.asarray(res.frames, dtype=float)
        self.anim_start = t
        self.last_effort = effort
        self.servo_peak, self.servo_t = round(90 * min(res.arm_output, 1.0)), t
        if stim:
            self.stim, self.stim_region, self.stim_until = stim[1], stim[0], t + 300
            if res.stim_offset_ms is not None:
                off = int(res.stim_offset_ms)
                verdict = (res.stim_verdict or "").upper()
                self.timing_text = f"STIM {abs(off)}ms {'EARLY' if off < 0 else 'LATE'}" if verdict in ("EARLY", "LATE") \
                    else ("TIMING GOOD +" if verdict == "GOOD" else f"STIM {verdict}")
                self.timing_until = t + 2000
        print(f"attempt effort={effort:.2f} arm_out={res.arm_output:.2f} arm_fn={res.arm_function:.2f} "
              f"day={res.day} stim={stim} verdict={res.stim_verdict}")

    def device(self):
        with self.lock:
            self.maybe_attempt()
            t = now_ms()
            st = self.session.state()
            self.health = np.array(st["health"], dtype=float)
            arm = float(st["arm_function"])
            # activity: play the attempt frames in real time, otherwise idle
            activity = None
            if self.frames is not None:
                i = int((t - self.anim_start) / 20.0)
                if i < len(self.frames):
                    activity = self.frames[i]
            if activity is None:
                activity = np.asarray(self.session.idle_frame(t), dtype=float)
            stim_active = t < self.stim_until
            leds = led_string(activity, self.health, self.stim_region if stim_active else None, t)
            # servo: rise to peak, hold 300 ms, decay over 800 ms
            dt = t - self.servo_t
            if dt < 300:
                servo = self.servo_peak
            elif dt < 1100:
                servo = round(self.servo_peak * (1 - (dt - 300) / 800))
            else:
                servo = 0
            p = st["patient"]
            if t < self.intro_until:
                l1, l2 = f"PATIENT {p.get('card', self.card)}  {p.get('age', '')}y", "STROKE: L MOTOR"
            else:
                l1 = f"DAY {st['day']}  ARM {round(arm * 100)}%"
                l2 = self.timing_text if t < self.timing_until else f"EFFORT {round(self.last_effort * 100)}%"
            fc = st.get("forecast") or {}
            payload = {
                "leds": leds, "lcd1": pad(l1), "lcd2": pad(l2), "rgb": arm_rgb(arm), "servo": int(servo),
                "stim": self.stim if stim_active else None,
                "lcd3": pad("DAY90 FORECAST"),
                "lcd4": pad(f"{round(fc.get('p50', 0) * 100)}% +/- {round((fc.get('p90', 0) - fc.get('p10', 0)) * 50)}") if fc else pad(""),
            }
            if payload != self.last_payload:
                self.seq += 1
                self.last_payload = payload
            return {"seq": self.seq, **payload}


DRIVER: Driver | None = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/device":
            self._send(json.dumps(DRIVER.device()).encode())
        elif url.path == "/api/health":
            self._send(json.dumps({"ok": True, "t_ms": now_ms(), "brain": BRAIN}).encode())
        elif url.path == "/new":
            card = parse_qs(url.query).get("card", ["B"])[0].upper()
            DRIVER.new_patient(card if card in ("A", "B", "R") else "B")
            self._send(f"<p>New patient {card}. <a href='/'>back</a></p>".encode(), "text/html")
        elif url.path == "/":
            d = DRIVER.device()
            self._send((f"<h2>NeuroNest brain → Uno Q test server ({BRAIN} brain)</h2>"
                        f"<p>New patient: <a href='/new?card=A'>A</a> · <a href='/new?card=B'>B</a> · "
                        f"<a href='/new?card=R'>random</a> · board feed: <a href='/api/device'>/api/device</a></p>"
                        f"<pre>{d['lcd1']}\n{d['lcd2']}\nservo={d['servo']} stim={d['stim']}</pre>"
                        f"<meta http-equiv='refresh' content='1'>").encode(), "text/html")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def lan_ips():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--card", default="B")
    args = ap.parse_args()
    DRIVER = Driver(args.card.upper())
    print(f"Brain device server ({BRAIN} brain) on port {args.port}; laptop IPs: {', '.join(lan_ips())}")
    print("Uno Q SERVER should be http://<hotspot-ip>:%d  (hotspot is usually 192.168.137.1)" % args.port)
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
