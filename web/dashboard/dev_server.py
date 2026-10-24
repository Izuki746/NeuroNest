"""Dashboard dev server (Person B): runs Person A's brain and speaks the exact protocol of
C's real server (handoff/SHARED_CONTEXT.md §7.5), so the dashboard (and the Uno Q) can be
built and demoed before server/app.py exists. When C's server is ready, nothing in the
dashboard changes: it just connects to C's server instead.

    python web/dashboard/dev_server.py            → http://localhost:8000/dashboard/
    python web/dashboard/dev_server.py --port 8001

Provides: GET /dashboard/ (static) · WS /ws/state (20 Hz) · WS /ws/input · POST /api/control
          GET /api/device (Uno Q) · GET /api/health
Dev-only controls (C's server may ignore them): dev_auto (auto rehab attempts on/off),
dev_seizure (simulated post-stroke seizure for the seizure display), dev_auto_treat.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brain.layout import led_string  # noqa: E402

try:
    from brain.session import Session  # A's real brain
    BRAIN = "real"
except Exception:
    from brain.fake import FakeSession as Session  # noqa: N813
    BRAIN = "fake"
try:
    from ai.planner import plan_for
except Exception:
    from brain.fake import plan_for

log = logging.getLogger("dev_server")
TICK_S = 0.05
RESOLVE_MS = 400.0
AUTO_EVERY_S = 3.5
SEIZURE_PULSE_HZ = 1.2          # slow pulse: never above 3 Hz (photosensitivity)


def now_ms() -> float:
    return time.monotonic() * 1000.0


def pad16(s) -> str:
    return str(s)[:16].ljust(16)


def arm_rgb(arm: float) -> list[int]:
    if arm < 0.35:
        return [255, 40, 40]
    if arm < 0.7:
        return [255, 160, 0]
    return [40, 220, 80]


def load_weights(names: list[str]) -> np.ndarray:
    W = np.loadtxt(ROOT / "data" / "connectivity_76" / "weights.txt")
    np.fill_diagonal(W, 0)
    W = W + W.T
    return W / W.max()


class Hub:
    def __init__(self, card: str = "B"):
        self.clients: set[WebSocket] = set()
        self.inputs: set[WebSocket] = set()
        self.auto = True
        self.auto_treat = True
        self.mode = "human"
        self.new_patient(card)

    # ---------- patient ----------
    def new_patient(self, card: str):
        self.session = Session(card=card, seed=random.randint(0, 99999))
        info = self.session.info()
        self.info = info
        self.names = info["regions"]
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.W = load_weights(self.names)
        self.frames, self.anim_start = None, 0.0
        self.servo_peak, self.servo_t = 0, -1e9
        self.stim_vis = None                     # {"region","kind","until","actor"}
        self.timing, self.timing_until = None, 0.0
        self.efforts: deque[float] = deque(maxlen=30)
        self.last_effort = 0.0
        self.fatigue = False
        self.plan, self.plan_curves, self.plan_expl = None, None, None
        self.seizure = None
        self.narration = None
        self.edges, self.edges_sent = [], 0.0
        self.last_auto = now_ms()
        self.last_day = self.session.state()["day"]
        self.intro_until = now_ms() + 4000
        self.busy = None
        self.device_seq, self.device_last = 0, None
        self.event("patient", {"card": card, "age": info["age"], "lesion": info["lesion"],
                               "regions": self.names, "centres": info["centres"]})
        self.narrate(f"Patient {card}: {info['age']} years old, stroke in the left motor cortex. "
                     f"Their right arm is weak.")

    # ---------- messaging ----------
    def event(self, kind: str, data: dict):
        msg = {"type": "event", "kind": kind, "t_ms": now_ms(), "data": data}
        asyncio.get_event_loop().create_task(self.broadcast(msg)) if self._loop_running() else None

    @staticmethod
    def _loop_running() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    def narrate(self, text: str):
        self.narration = {"text": text, "t_ms": now_ms(), "audio_url": None}

    async def broadcast(self, msg: dict):
        data = json.dumps(msg)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    # ---------- inputs ----------
    def submit_stim(self, t_ms: float, region: str, kind: str, actor: str = "human", intensity: float = 1.0):
        self.session.submit_stim(t_ms, region, kind, intensity, actor)
        self.stim_vis = {"region": region, "kind": kind, "actor": actor, "until": now_ms() + 600}
        # stopping a seizure: calming a seizing region (human or AI)
        sz = self.seizure
        if sz and sz["phase"] == "ictal" and kind == "calm" and region in sz["regions"] and not sz["treated"]:
            sz["treated"], sz["t_treat"], sz["treated_by"] = True, now_ms(), actor
            sz["treatment"] = f"Calm {region}"
            self.event("treatment", {"actor": actor, "region": region, "kind": "calm", "reason": "seizure",
                                     "label": f"{'AI' if actor == 'ai' else 'Care team'} calmed {region} to stop the seizure"})
            self.narrate(f"{'The AI' if actor == 'ai' else 'The care team'} is calming {region} "
                         f"with yellow light to stop the seizure.")
        self.event("stim", {"region": region, "kind": kind, "actor": actor, "t_ms": t_ms})

    async def submit_attempt(self, t_onset: float, effort: float, depth: float, speed: float,
                             hold_ms: float, source: str):
        await asyncio.sleep(max(0.0, (t_onset + RESOLVE_MS - now_ms()) / 1000.0))
        res = await asyncio.to_thread(self.session.submit_attempt, t_onset, effort, depth, speed, hold_ms)
        self.frames, self.anim_start = np.asarray(res.frames, dtype=float), now_ms()
        self.servo_peak, self.servo_t = round(90 * min(res.arm_output, 1.0)), now_ms()
        self.last_effort = res.effort
        self.efforts.append(res.effort)
        if res.stim_offset_ms is not None:
            self.timing = {"offset_ms": round(res.stim_offset_ms), "verdict": res.stim_verdict}
            self.timing_until = now_ms() + 2500
        dw = np.asarray(res.weight_change, dtype=float)
        flat = np.argsort(np.abs(dw), axis=None)[::-1][:150]
        self.edges = [[int(i // 76), int(i % 76), round(float(dw.flat[i]), 5)] for i in flat if abs(dw.flat[i]) > 1e-6]
        self.event("attempt", {"effort": round(res.effort, 3), "arm_output": round(res.arm_output, 3),
                               "arm_function": round(res.arm_function, 3), "offset_ms": res.stim_offset_ms,
                               "verdict": res.stim_verdict, "gate": round(res.gate, 2), "source": source,
                               "day": res.day, "attempts_today": res.attempts_today})
        self._check_fatigue()
        st_day = self.session.state()["day"]
        if st_day != self.last_day:
            self.event("day_end", {"day": self.last_day, "arm_function": round(res.arm_function, 3)})
            self.last_day = st_day

    def _check_fatigue(self):
        e = list(self.efforts)
        if len(e) < 10:
            return
        start, rolling = float(np.mean(e[:5])), float(np.mean(e[-8:]))
        fat = rolling < 0.8 * start
        if fat and not self.fatigue:
            self.event("fatigue", {"rolling": rolling, "start": start})
            self.narrate("Effort is dropping. Two more, really squeeze!")
        self.fatigue = fat

    # ---------- controls ----------
    async def control(self, msg: dict):
        a = msg.get("action")
        if a == "new_patient":
            self.new_patient(str(msg.get("card", "B")).upper())
        elif a == "end_day":
            dr = await asyncio.to_thread(self.session.end_day)
            self.event("day_end", {"day": dr.day, "arm_function": round(dr.arm_function, 3)})
            self.last_day = self.session.state()["day"]
        elif a == "fast_forward":
            asyncio.get_running_loop().create_task(self._fast_forward(int(msg.get("days", 90)), msg.get("plan", "standard")))
        elif a == "run_planner":
            asyncio.get_running_loop().create_task(self._run_planner())
        elif a == "set_mode":
            self.mode = msg.get("mode", "human")
            if self.mode == "ai" and not self.plan:
                asyncio.get_running_loop().create_task(self._run_planner())
        elif a == "reset":
            self.new_patient(self.info["card"])
        elif a == "dev_auto":
            self.auto = bool(msg.get("on", not self.auto))
        elif a == "dev_auto_treat":
            self.auto_treat = bool(msg.get("on", not self.auto_treat))
        elif a == "dev_seizure":
            self.start_seizure()
        elif a == "dev_attempt":
            t = now_ms()
            asyncio.get_running_loop().create_task(self.submit_attempt(t, float(msg.get("effort", 0.85)), 0.9, 0.8, 300, "dashboard"))

    async def _fast_forward(self, days: int, plan):
        self.busy = f"Fast-forwarding {days} days ({plan})"
        self.narrate(f"Fast-forwarding {days} days with {plan if plan != 'ai' else 'the AI plan'}.")
        done = 0
        while done < days:
            n = min(3, days - done)
            await asyncio.to_thread(self.session.fast_forward, n, plan)
            done += n
            await asyncio.sleep(0.06)
        self.last_day = self.session.state()["day"]
        self.busy = None
        arm = self.session.state()["arm_function"]
        self.event("fast_forward_done", {"days": days, "plan": plan, "arm_function": arm})
        self.narrate(f"Day {self.last_day}: arm function is {arm:.0%}.")

    async def _run_planner(self):
        self.busy = "AI planner testing treatment plans on copies of this brain"
        res = await asyncio.to_thread(plan_for, self.session, 10.0)
        self.plan, self.plan_curves, self.plan_expl = res["best"], res["curves"], res["explanation"]
        self.busy = None
        self.event("plan_ready", {"plan": self.plan, "explanation": self.plan_expl,
                                  "candidates_tested": res.get("candidates_tested")})
        self.narrate(self.plan_expl)

    # ---------- simulated seizure (dev) ----------
    def start_seizure(self):
        if self.seizure:
            return
        health = np.asarray(self.session.state()["health"])
        lesioned = [i for i, h in enumerate(health) if h < 0.95]
        cand = [i for i, n in enumerate(self.names) if n.startswith("l") and i not in lesioned]
        # post-stroke seizures start next to the damage: close in space AND wired to the lesion
        centres = np.asarray(self.info["centres"], dtype=float)
        def score(i):
            if not lesioned:
                return self.W[i].sum()
            dist = min(np.linalg.norm(centres[i] - centres[j]) for j in lesioned)
            return (0.2 + self.W[i, lesioned].sum()) * math.exp(-dist / 25.0)
        onset = max(cand, key=score)
        self.seizure = {"phase": "pre", "t0": now_ms(), "onset": self.names[onset], "regions": {},
                        "risk": 0.15, "treated": False, "t_treat": None, "treated_by": None,
                        "treatment": None, "t_ictal": None, "last_spread": 0.0}
        self.narrate(f"Seizure risk rising near the damaged area ({self.names[onset]}).")

    def tick_seizure(self):
        sz = self.seizure
        if not sz:
            return
        t = now_ms()
        if sz["phase"] == "pre":
            sz["risk"] = 0.15 + 0.7 * min(1.0, (t - sz["t0"]) / 3000.0)
            if t - sz["t0"] > 3000:
                sz["phase"], sz["t_ictal"], sz["last_spread"] = "ictal", t, t
                sz["regions"] = {sz["onset"]: 0.5}
                self.event("seizure", {"phase": "start", "onset": sz["onset"]})
                self.narrate(f"Seizure starting in {sz['onset']}: activity is spreading.")
        elif sz["phase"] == "ictal":
            sz["risk"] = 1.0
            if not sz["treated"]:
                for r in sz["regions"]:
                    sz["regions"][r] = min(1.0, sz["regions"][r] + 0.02)
                if t - sz["last_spread"] > 700 and len(sz["regions"]) < 8:
                    sz["last_spread"] = t
                    cur = [self.idx[r] for r in sz["regions"]]
                    score = self.W[:, cur].sum(axis=1)
                    score[cur] = -1
                    nxt = int(np.argmax(score))
                    sz["regions"][self.names[nxt]] = 0.35
                if self.auto_treat and t - sz["t_ictal"] > 2500:
                    self.submit_stim(t, sz["onset"], "calm", actor="ai")
            else:
                for r in list(sz["regions"]):
                    sz["regions"][r] *= 0.93
                if max(sz["regions"].values(), default=0) < 0.04:
                    dur = (t - sz["t_ictal"]) / 1000.0
                    self.event("seizure", {"phase": "end", "duration_s": round(dur, 1), "treated_by": sz["treated_by"],
                                           "treatment": sz["treatment"]})
                    self.narrate(f"Seizure stopped after {dur:.1f} seconds.")
                    sz["phase"], sz["t_end"], sz["regions"] = "post", t, {}
        elif sz["phase"] == "post":
            sz["risk"] = max(0.05, sz["risk"] * 0.95)
            if t - sz["t_end"] > 2500:
                self.seizure = None

    def seizure_state(self):
        sz = self.seizure
        if not sz:
            return None
        t = now_ms()
        return {"active": sz["phase"] == "ictal", "phase": sz["phase"], "risk": round(sz["risk"], 3),
                "onset": sz["onset"], "regions": {k: round(v, 3) for k, v in sz["regions"].items()},
                "eta_s": round(max(0.0, 3.0 - (t - sz["t0"]) / 1000.0), 1) if sz["phase"] == "pre" else None,
                "elapsed_s": round((t - sz["t_ictal"]) / 1000.0, 1) if sz["t_ictal"] else None,
                "treated": sz["treated"], "treated_by": sz["treated_by"], "treatment": sz["treatment"]}

    # ---------- per-tick state ----------
    def current_activity(self) -> np.ndarray:
        t = now_ms()
        act = None
        if self.frames is not None:
            i = int((t - self.anim_start) / 20.0)
            if i < len(self.frames):
                act = self.frames[i].copy()
        if act is None:
            act = np.asarray(self.session.idle_frame(t), dtype=float).copy()
        if self.seizure and self.seizure["regions"]:
            pulse = 0.5 + 0.5 * math.sin(2 * math.pi * SEIZURE_PULSE_HZ * t / 1000.0)
            for r, v in self.seizure["regions"].items():
                i = self.idx[r]
                act[i] = max(act[i], 0.25 + 0.6 * v * (0.6 + 0.4 * pulse))
        return act

    def state(self) -> dict:
        t = now_ms()
        st = self.session.state()
        stim = None
        if self.stim_vis and t < self.stim_vis["until"]:
            stim = {k: self.stim_vis[k] for k in ("region", "kind", "actor")} | {"active": True}
        e = list(self.efforts)
        msg = {
            "type": "state", "t_ms": t, "mode": self.mode, "brain": BRAIN,
            "patient": st["patient"], "day": st["day"], "attempts_today": st["attempts_today"],
            "arm_function": st["arm_function"], "arm_output": st["arm_output"],
            "servo": self.servo_angle(),
            "effort": {"last": self.last_effort, "rolling": float(np.mean(e[-8:])) if e else 0.0,
                       "session_start": float(np.mean(e[:5])) if e else 0.0, "fatigue": self.fatigue},
            "activity": [round(float(x), 3) for x in self.current_activity()],
            "health": [round(float(x), 3) for x in st["health"]],
            "stim": stim,
            "timing": self.timing if t < self.timing_until else None,
            "forecast": st.get("forecast"),
            "curve": st.get("curve"),
            "plan": self.plan, "plan_curves": self.plan_curves, "plan_explanation": self.plan_expl,
            "seizure": self.seizure_state(),
            "narration": self.narration,
            "busy": self.busy,
            "dev": {"auto": self.auto, "auto_treat": self.auto_treat},
            "calibrating": False,
        }
        if t - self.edges_sent > 1000:
            msg["edges"] = self.edges
            self.edges_sent = t
        return msg

    def servo_angle(self) -> int:
        dt = now_ms() - self.servo_t
        if dt < 300:
            return int(self.servo_peak)
        if dt < 1100:
            return int(round(self.servo_peak * (1 - (dt - 300) / 800)))
        return 0

    def led_activity(self, health: np.ndarray) -> np.ndarray:
        """Activity for the 8x13 LED matrix: smoothed over time, with a steady glow for healthy
        regions so resting noise doesn't flicker LEDs on/off; only real activity (squeezes,
        stimulation, seizures) rises above the baseline. Lesions stay dark via led_string's health cap."""
        act = self.current_activity()
        prev = getattr(self, "_led_act", None)
        self._led_act = act if prev is None else prev + 0.35 * (act - prev)
        rest = 0.07
        return 0.22 * health + 1.4 * np.clip(self._led_act - rest, 0.0, None)

    def device(self) -> dict:
        t = now_ms()
        st = self.session.state()
        arm = float(st["arm_function"])
        stim_region = self.stim_vis["region"] if (self.stim_vis and t < self.stim_vis["until"]) else None
        health = np.asarray(st["health"], dtype=float)
        leds = led_string(self.led_activity(health), health, stim_region, t)
        p = st["patient"]
        sz = self.seizure
        if t < self.intro_until:
            l1, l2 = f"PATIENT {p['card']}  {p['age']}y", "STROKE: L MOTOR"
        elif sz and sz["phase"] == "pre":
            l1, l2 = "SEIZURE RISK", f"{round(sz['risk'] * 100)}%  IN ~{max(0, round(3 - (t - sz['t0']) / 1000))}s"
        elif sz and sz["phase"] == "ictal":
            l1, l2 = "SEIZURE!", ("CALMING " + sz["onset"]) if sz["treated"] else ("IN " + sz["onset"])
        elif self.busy:
            l1, l2 = f">> DAY {st['day']}", f"ARM {round(arm * 100)}%"
        else:
            l1 = f"DAY {st['day']}  ARM {round(arm * 100)}%"
            if self.timing and t < self.timing_until:
                off, v = self.timing["offset_ms"], (self.timing["verdict"] or "")
                l2 = "TIMING GOOD +" if v == "good" else (f"STIM {abs(off)}ms {'EARLY' if v == 'early' else 'LATE'}"
                                                         if v in ("early", "late") else "WRONG REGION")
            elif self.fatigue:
                l2 = "TIRED? BREATHE"
            else:
                l2 = f"EFFORT {round(self.last_effort * 100)}%"
        rgb = [255, 0, 60] if (sz and sz["phase"] == "ictal") else arm_rgb(arm)
        fc = st.get("forecast") or {}
        payload = {"leds": leds, "lcd1": pad16(l1), "lcd2": pad16(l2), "rgb": rgb, "servo": self.servo_angle(),
                   "stim": self.stim_vis["kind"] if stim_region else None,
                   "lcd3": pad16("DAY90 FORECAST"),
                   "lcd4": pad16(f"{round(fc.get('p50', 0) * 100)}% +/- {round((fc.get('p90', 0) - fc.get('p10', 0)) * 50)}")}
        if payload != self.device_last:
            self.device_seq += 1
            self.device_last = payload
        return {"seq": self.device_seq, **payload}

    # ---------- loops ----------
    async def run(self):
        while True:
            try:
                self.tick_seizure()
                if self.auto and not self.busy and now_ms() - self.last_auto > AUTO_EVERY_S * 1000:
                    self.last_auto = now_ms()
                    self._auto_attempt()
                if self.clients:
                    await self.broadcast(self.state())
            except Exception:
                log.exception("tick failed")
            await asyncio.sleep(TICK_S)

    def _auto_attempt(self):
        t = now_ms()
        effort = random.uniform(0.55, 1.0)
        if self.mode == "ai" and self.plan and self.plan.get("target"):
            p = self.plan
            self.submit_stim(t + p["offset_ms"], p["target"], p["kind"], actor="ai", intensity=p.get("intensity", 1.0))
        elif random.random() < 0.6:
            region, kind = random.choice([("lPMCDL", "excite"), ("lPMCM", "excite"), ("rM1", "calm")])
            self.submit_stim(t + random.choice([-120, -80, -40, 0, 150, 300]), region, kind, actor="human")
        asyncio.get_running_loop().create_task(self.submit_attempt(t, effort, effort, effort, 300, "auto"))


HUB: Hub | None = None
app = FastAPI(title="NeuroNest dashboard dev server")
app.mount("/dashboard", StaticFiles(directory=Path(__file__).parent, html=True), name="dashboard")


@app.on_event("startup")
async def _startup():
    asyncio.get_running_loop().create_task(HUB.run())


@app.get("/")
async def root():
    return RedirectResponse("/dashboard/")


@app.get("/api/health")
async def health():
    return {"ok": True, "t_ms": now_ms(), "brain": BRAIN}


@app.get("/api/device")
async def device():
    return JSONResponse(HUB.device())


@app.post("/api/control")
async def control(req: Request):
    msg = await req.json()
    await HUB.control(msg)
    return {"ok": True}


@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    await ws.accept()
    HUB.clients.add(ws)
    info = HUB.info
    await ws.send_text(json.dumps({"type": "event", "kind": "patient", "t_ms": now_ms(),
                                   "data": {"card": info["card"], "age": info["age"], "lesion": info["lesion"],
                                            "regions": HUB.names, "centres": info["centres"]}}))
    try:
        while True:
            txt = await ws.receive_text()           # dashboard may also send control/stim here
            await handle_input(json.loads(txt))
    except (WebSocketDisconnect, Exception):
        HUB.clients.discard(ws)


@app.websocket("/ws/input")
async def ws_input(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong", "client_t": msg.get("client_t"), "server_t_ms": now_ms()}))
            else:
                await handle_input(msg)
    except (WebSocketDisconnect, Exception):
        pass


async def handle_input(msg: dict):
    t = msg.get("type")
    if t == "attempt":
        asyncio.get_running_loop().create_task(HUB.submit_attempt(
            float(msg.get("t_ms", now_ms())), float(msg.get("effort", 0.7)), float(msg.get("depth", 0.8)),
            float(msg.get("speed", 0.7)), float(msg.get("hold_ms", 300)), msg.get("source", "input")))
    elif t == "stim":
        tt = msg.get("t_ms")
        if tt is None:
            tt = now_ms() - float(msg.get("rtt_ms", 0)) / 2
        HUB.submit_stim(float(tt), msg["region"], msg["kind"], actor=msg.get("actor", "human"),
                        intensity=float(msg.get("intensity", 1.0)))
    elif t == "control":
        await HUB.control(msg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--card", default="B")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    HUB = Hub(args.card.upper())
    print(f"NeuroNest dashboard dev server ({BRAIN} brain): http://localhost:{args.port}/dashboard/")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
