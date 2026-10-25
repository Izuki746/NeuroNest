"""NeuroNest production server: runs Person A's brain and drives the dashboard, phone page and Uno Q.

    python web/dashboard/dev_server.py --card A    → http://localhost:8000/dashboard/  (phones: /phone/)

Speaks the contract in handoff/SHARED_CONTEXT.md §7.5. Integrates Person C's modules (all optional,
each fails soft): server/narrator.py (ElevenLabs voice coach + Gemini narration), storage/tiger.py
(Tiger Data logging, CSV buffer offline), and the inputs/hands.py, inputs/keyboard.py and web/phone
clients over /ws/input.

Provides: GET /dashboard/ /phone/ /audio/ (static) · WS /ws/state (20 Hz) · WS /ws/input
          POST /api/control · GET /api/device (Uno Q) · /api/health · /api/report · /api/stats · /qr
Demo controls: dev_attempt, dev_auto (auto squeezes), dev_seizure, dev_auto_treat; also calibrate, report.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import math
import os
import random
import socket
import statistics
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass
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
AUDIO_DIR = ROOT / "server" / "audio_cache"
AI_CUE_EVERY_S = 3.0


class _NullNarrator:
    """Used if server/narrator.py can't be imported: every call is a silent no-op."""
    now_offset_ms = 900.0
    el_key = ""
    voice = ""

    def pregenerate(self):
        return None

    def coach(self, key, force=False):
        return None

    def clip_url(self, key):
        return None

    def narrate(self, kind, data, force=False):
        return None

    def report(self, info):
        return "# Report unavailable\n\nSimulation only; not medical advice."


class _NullStorage:
    """Used if storage/tiger.py can't be imported: every method is a no-op."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def load_narrator():
    try:
        from server.narrator import Narrator
        return Narrator()
    except Exception as e:  # noqa: BLE001
        log.warning("voice/narration unavailable (%s)", e)
        return _NullNarrator()


def load_storage():
    try:
        from storage.tiger import TigerWriter
        return TigerWriter(os.getenv("TIGER_DATABASE_URL") or None)
    except Exception as e:  # noqa: BLE001
        log.warning("Tiger Data logging unavailable (%s)", e)
        return _NullStorage()


class LedLimiter:
    """Cap each LED's brightness changes at ~3 per second (photosensitivity safety)."""
    MIN_MS = 333.0

    def __init__(self) -> None:
        self.cur = ["0"] * 104
        self.t = [-1e12] * 104

    def apply(self, target: str, t_ms: float) -> str:
        if len(target) != 104:
            return "".join(self.cur)
        for i, ch in enumerate(target):
            if ch != self.cur[i] and t_ms - self.t[i] >= self.MIN_MS:
                self.cur[i], self.t[i] = ch, t_ms
        return "".join(self.cur)


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
        self.input_clients: dict[WebSocket, dict] = {}   # hands / keyboard / phone, with kind + measured rtt
        self.auto = False          # nothing happens until someone squeezes (turn on in ⚙ Demo → Auto squeeze)
        self.auto_treat = True
        self.mode = "human"
        self.narrator = load_narrator()
        self.store = load_storage()
        self.audio_q: asyncio.Queue = asyncio.Queue()
        self.audio_until = 0.0
        self.calibrating, self.calib_until = False, 0.0
        self.counters = {"attempts": 0, "stims": 0}
        self.leds = LedLimiter()
        self.new_patient(card)

    # ---------- Person C integrations: storage, voice coach, narration ----------
    def safe_store(self, method: str, *a, **k) -> None:
        try:
            getattr(self.store, method)(*a, **k)
        except Exception as e:  # noqa: BLE001
            log.warning("storage.%s failed: %s", method, e)

    @staticmethod
    def _clip_secs(url) -> float:
        """Clip length from file size (mp3_44100_128 = 16000 bytes/s), so clips never overlap."""
        try:
            size = (AUDIO_DIR / Path(str(url)).name).stat().st_size
            return min(30.0, max(0.8, size / 16000.0))
        except Exception:  # noqa: BLE001
            return 2.5

    def audio_busy(self) -> bool:
        return now_ms() < self.audio_until or not self.audio_q.empty()

    def coach(self, key: str, force: bool = False) -> None:
        """Queue a pre-recorded ElevenLabs coach clip (rate-limited inside the narrator)."""
        try:
            url = self.narrator.coach(key, force=force)
        except Exception as e:  # noqa: BLE001
            log.warning("coach(%s) failed: %s", key, e)
            url = None
        if url and self.audio_q.qsize() < 4:
            self.audio_q.put_nowait(("clip", url))

    async def voice(self, kind: str, data: dict, force: bool = False) -> None:
        """Gemini-written, ElevenLabs-spoken narration line (only when TTS is configured)."""
        if not (getattr(self.narrator, "el_key", "") and getattr(self.narrator, "voice", "")):
            return
        try:
            n = await asyncio.to_thread(self.narrator.narrate, kind, data, force)
        except Exception as e:  # noqa: BLE001
            log.warning("narration failed: %s", e)
            return
        if n and n.get("audio_url") and self.audio_q.qsize() < 4:
            self.audio_q.put_nowait(("narration", n))

    def voice_soon(self, kind: str, data: dict, force: bool = False) -> None:
        if self._loop_running():
            asyncio.get_running_loop().create_task(self.voice(kind, data, force))

    async def audio_worker(self) -> None:
        """One clip at a time: `audio` messages for coach clips, state.narration for spoken narration."""
        while True:
            kind, item = await self.audio_q.get()
            try:
                if kind == "clip":
                    dur = self._clip_secs(item)
                    await self.broadcast({"type": "audio", "url": item})
                else:
                    dur = self._clip_secs(item.get("audio_url"))
                    self.narration = {"text": item.get("text", ""), "audio_url": item.get("audio_url"), "t_ms": now_ms()}
                self.audio_until = now_ms() + dur * 1000 + 250
                await asyncio.sleep(dur + 0.25)
            except Exception as e:  # noqa: BLE001
                log.warning("audio worker error: %s", e)

    async def send_inputs(self, msg: dict) -> None:
        data = json.dumps(msg)
        for ws in list(self.input_clients):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=0.5)
            except Exception:  # noqa: BLE001
                self.input_clients.pop(ws, None)

    # ---------- AI mode: spoken "Squeeze... now!" cue, learned reaction lag, auto-timed stimulation ----------
    async def ai_loop(self) -> None:
        while True:
            await asyncio.sleep(AI_CUE_EVERY_S)
            try:
                if (self.mode != "ai" or self.auto or self.busy or self.audio_busy()
                        or not self.plan or not self.plan.get("target")):
                    continue
                send = now_ms()
                cue_t = send + float(getattr(self.narrator, "now_offset_ms", 900.0))
                self.cues.append(cue_t)
                self.coach("squeeze_now", force=True)
                lag = statistics.median(self.lags) if self.lags else 350.0
                sigma = statistics.pstdev(self.lags) if len(self.lags) >= 3 else 100.0
                p = self.plan
                t_stim = max(cue_t + lag + float(p.get("offset_ms", -80)), send)
                self.ai_info = {"predicted_onset_ms": round(cue_t + lag), "pm_ms": round(max(sigma, 20)),
                                "lag_ms": round(lag), "cue_t_ms": round(cue_t)}
                asyncio.get_running_loop().call_later(max(0.0, (t_stim - now_ms()) / 1000.0), self._ai_fire, dict(p), t_stim)
            except Exception:  # noqa: BLE001
                log.exception("ai loop failed")

    def _ai_fire(self, plan: dict, t_stim: float) -> None:
        if self.mode == "ai" and plan.get("target"):
            self.submit_stim(t_stim, plan["target"], plan.get("kind", "excite"), actor="ai",
                             intensity=float(plan.get("intensity", 1.0)))

    # ---------- patient ----------
    def new_patient(self, card: str):
        self.session = Session(card=card, seed=random.randint(0, 99999))
        self._snap = None
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
        self.last_day = self.snapshot()["day"]
        self.intro_until = now_ms() + 4000
        self.busy = None
        self.device_seq, self.device_last = 0, None
        self._led_act = None                                  # clear LED smoothing from the previous patient
        self.reset_id = getattr(self, "reset_id", 0) + 1      # tells the Uno Q to wipe its displays and start fresh
        self.days_log: dict[int, dict] = {}                   # per-day record for the progress report
        self._new_acc()
        self.report_cache = None
        self.fatigue_attempts, self.break_said = 0, False
        self.cues: deque[float] = deque(maxlen=10)            # AI-mode cue times, to learn reaction lag
        self.lags: deque[float] = deque(maxlen=5)
        self.ai_info = None
        self.leds = LedLimiter()                              # fresh LED limiter so the reset wipe isn't delayed
        self.session_id = f"live-{uuid.uuid4().hex[:8]}"
        self.safe_store("session", self.session_id, info["card"], info.get("age"), info.get("lesion"),
                        getattr(getattr(self.session, "pt", None), "traits", None), "live")
        self.event("patient", {"card": card, "age": info["age"], "lesion": info["lesion"],
                               "regions": self.names, "centres": info["centres"]})
        self.narrate(f"Patient {card}: {info['age']} years old, stroke in the left motor cortex. "
                     f"Their right arm is weak.")
        if card in ("A", "B"):
            self.coach(f"patient_{card}", force=True)
        self.voice_soon("patient", {"card": card, "age": info.get("age")})

    # ---------- non-blocking brain snapshot ----------
    def snapshot(self) -> dict:
        """Session.state() without ever blocking the event loop: while the brain is busy in a worker
        thread (planner, fast-forward, an attempt) it holds its lock, so return the last snapshot
        instead of waiting. This keeps /api/device and the 20 Hz dashboard feed responsive."""
        lock = getattr(self.session, "_lock", None)
        if lock is None:
            return self.session.state()
        if lock.acquire(blocking=False):
            try:
                self._snap = self.session.state()
            finally:
                lock.release()
        elif getattr(self, "_snap", None) is None:
            self._snap = self.session.state()     # first call ever: nothing cached yet, wait once
        return self._snap

    # ---------- per-day record (progress report) ----------
    def _new_acc(self):
        self.acc = {"attempts": 0, "effort_sum": 0.0, "stims": 0, "good": 0, "early": 0, "late": 0, "wrong": 0, "seizures": 0}

    def _finalize_day(self, day: int, arm: float, attempts=None, effort=None, stims=None, planned_good=False):
        a = self.acc
        att = a["attempts"] if attempts is None else int(attempts)
        eff = (a["effort_sum"] / a["attempts"] if a["attempts"] else 0.0) if effort is None else float(effort)
        st = a["stims"] if stims is None else int(stims)
        self.days_log[int(day)] = {
            "day": int(day), "arm": round(float(arm), 4), "attempts": att, "effort": round(eff, 3), "stims": st,
            "good": st if planned_good else a["good"], "early": 0 if planned_good else a["early"],
            "late": 0 if planned_good else a["late"], "wrong": 0 if planned_good else a["wrong"],
            "seizures": a["seizures"], "plan": (self.plan or {}).get("target") if planned_good else None}
        self._new_acc()
        d = self.days_log[int(day)]
        self.safe_store("daily", self.session_id,
                        SimpleNamespace(day=d["day"], arm_function=d["arm"], mean_effort=d["effort"],
                                        attempts=d["attempts"], window_level=None, stim_count=d["stims"]),
                        self.plan if planned_good else None)
        fc = self.snapshot().get("forecast")
        if fc:
            self.safe_store("forecast", self.session_id, int(day), fc, "twins")
        if int(day) % 5 == 0:
            self.voice_soon("day_end", {"day": int(day), "arm": round(float(arm), 2)})

    def _record_attempt(self, res):
        a = self.acc
        a["attempts"] += 1
        a["effort_sum"] += float(res.effort)
        if res.stim_verdict:
            a["stims"] += 1
            key = {"good": "good", "early": "early", "late": "late", "wrong_region": "wrong"}.get(res.stim_verdict)
            if key:
                a[key] += 1

    def report_days(self) -> list[dict]:
        days = [self.days_log[d] for d in sorted(self.days_log)]
        st = self.snapshot()
        if self.acc["attempts"] or self.acc["seizures"]:          # today, in progress
            a = self.acc
            days.append({"day": st["day"], "arm": round(float(st["arm_function"]), 4), "attempts": a["attempts"],
                         "effort": round(a["effort_sum"] / a["attempts"], 3) if a["attempts"] else 0.0, "stims": a["stims"],
                         "good": a["good"], "early": a["early"], "late": a["late"], "wrong": a["wrong"],
                         "seizures": a["seizures"], "partial": True})
        return days

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
        if kind not in ("excite", "calm") or region not in self.idx:
            log.warning("ignored stim region=%r kind=%r", region, kind)
            return
        self.session.submit_stim(t_ms, region, kind, intensity, actor)
        self.stim_vis = {"region": region, "kind": kind, "actor": actor, "until": now_ms() + 600}
        self.counters["stims"] += 1
        self.safe_store("stim", self.session_id, self.last_day, actor, region, kind, intensity, None)
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
        session = self.session
        try:
            res = await asyncio.to_thread(session.submit_attempt, t_onset, effort, depth, speed, hold_ms)
        except Exception:  # noqa: BLE001
            log.exception("submit_attempt failed")
            return
        if session is not self.session:          # patient was reset/changed while this squeeze was resolving
            return
        self.counters["attempts"] += 1
        self.safe_store("attempt_full", self.session_id, res.day, res.effort, depth, speed, hold_ms,
                        res.arm_output, res.arm_function, res.stim_offset_ms, res.stim_verdict, res.gate, source)
        self.safe_store("weights", self.session_id, res.day, res.weight_change, 1e-3)
        if self.mode == "ai":                     # learn this visitor's reaction lag from the last spoken cue
            cue = next((c for c in reversed(self.cues) if 0 < t_onset - c < 1500), None)
            if cue is not None:
                self.lags.append(t_onset - cue)
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
        fatigue_spoke = self._check_fatigue()
        # voice coach on ~every 3rd stimulated attempt (not on top of a fatigue line)
        if res.stim_verdict in ("good", "early", "late") and self.counters["attempts"] % 3 == 0 and not fatigue_spoke:
            self.coach({"good": "good_timing"}.get(res.stim_verdict, res.stim_verdict))
        self._record_attempt(res)
        st_day = self.snapshot()["day"]
        if st_day != self.last_day:
            self._finalize_day(self.last_day, res.arm_function)
            self.event("day_end", {"day": self.last_day, "arm_function": round(res.arm_function, 3)})
            self.last_day = st_day

    def _check_fatigue(self) -> bool:
        """Returns True if a fatigue voice line was queued."""
        e = list(self.efforts)
        if len(e) < 10:
            return False
        start, rolling = float(np.mean(e[:5])), float(np.mean(e[-8:]))
        fat = rolling < 0.8 * start
        spoke = False
        if fat and not self.fatigue:
            self.fatigue_attempts, self.break_said = 0, False
            self.event("fatigue", {"rolling": rolling, "start": start})
            self.narrate("Effort is dropping. Two more, really squeeze!")
            self.coach("fatigue_1", force=True)
            self.voice_soon("fatigue", {"rolling": round(rolling, 2), "session_start": round(start, 2)})
            spoke = True
        elif fat:
            self.fatigue_attempts += 1
            if self.fatigue_attempts >= 8 and not self.break_said:      # still tired 8 squeezes later: suggest a break
                self.break_said = True
                self.coach("fatigue_2", force=True)
                spoke = True
        self.fatigue = fat
        return spoke

    # ---------- controls ----------
    async def control(self, msg: dict):
        a = msg.get("action")
        if a == "new_patient":
            self.new_patient(str(msg.get("card", "B")).upper())
        elif a == "end_day":
            dr = await asyncio.to_thread(self.session.end_day)
            self._finalize_day(dr.day, dr.arm_function)
            self.event("day_end", {"day": dr.day, "arm_function": round(dr.arm_function, 3)})
            self.last_day = self.snapshot()["day"]
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
        elif a == "calibrate":                         # ask the webcam hand tracker to recalibrate
            self.calibrating, self.calib_until = True, now_ms() + 12000
            await self.send_inputs({"type": "control", "action": "calibrate"})
            self.coach("calibrate", force=True)
        elif a == "report":                            # clinician-style markdown report (Gemini, sample fallback)
            asyncio.get_running_loop().create_task(self._make_report())

    async def _make_report(self):
        st = self.snapshot()
        info = {"patient": {k: (st.get("patient") or {}).get(k) for k in ("card", "age", "lesion", "affected_arm")},
                "day": st.get("day"), "arm_function": st.get("arm_function"), "curve": st.get("curve"),
                "effort": {"last": self.last_effort, "fatigue": self.fatigue}, "attempts_logged": self.counters,
                "forecast": st.get("forecast"), "plan": self.plan, "plan_explanation": self.plan_expl}
        try:
            md = await asyncio.to_thread(self.narrator.report, info)
        except Exception as e:  # noqa: BLE001
            log.warning("report failed: %s", e)
            md = "# Report unavailable\n\nSimulation only; not medical advice."
        await self.broadcast({"type": "report", "markdown": md})

    async def _fast_forward(self, days: int, plan):
        if self.busy:
            return
        day_now = self.snapshot()["day"]
        days = min(days, 91 - day_now)          # the story is the first 3 months; don't run past day 90
        if days <= 0:
            self.narrate("This patient is already 3 months in. Pick patient A or B to start again.")
            self.event("info", {"text": "Already at 3 months: pick a patient to restart"})
            return
        session = self.session
        plan_name = plan
        self.busy = f"Fast-forwarding {days} days ({plan_name})"
        try:
            # contract §7.4: Session.fast_forward takes a plan dict (None = standard rehab), not a name
            if plan_name == "ai" and not self.plan:
                await self._run_planner(inner=True)
                self.busy = f"Fast-forwarding {days} days ({plan_name})"
            plan = {"standard": None, "none": {"target": None, "sessions_per_day": 0},
                    "ai": self.plan}.get(plan_name, plan_name) if isinstance(plan_name, str) else plan_name
            self.narrate(f"Fast-forwarding {days} days with {({'ai': 'the AI plan', 'standard': 'normal rehab', 'none': 'no rehab'}).get(plan_name, plan_name)}.")
            if days >= 80:
                self.coach("fast_forward", force=True)          # clip says "Fast-forwarding ninety days"
            done = 0
            while done < days and session is self.session:
                n = min(1, days - done)
                results = await asyncio.to_thread(session.fast_forward, n, plan)
                if session is not self.session:
                    break
                for dr in results or []:
                    self._finalize_day(dr.day, dr.arm_function, attempts=dr.attempts, effort=dr.mean_effort,
                                       stims=dr.stim_count, planned_good=(plan_name == "ai"))
                done += n
                await asyncio.sleep(0.06)
            if session is not self.session:
                return
            self.last_day = self.snapshot()["day"]
            arm = self.snapshot()["arm_function"]
            self.event("fast_forward_done", {"days": days, "plan": plan_name, "arm_function": arm})
            self.narrate(f"Day {self.last_day}: arm function is {arm:.0%}.")
            self.coach("recovery_complete", force=True)
            self.voice_soon("fast_forward_done", {"days": days, "plan": plan_name}, True)
        except Exception:  # noqa: BLE001
            log.exception("fast-forward failed")
            self.event("info", {"text": "Skipping ahead failed. Please try again."})
        finally:
            if session is self.session:
                self.busy = None

    async def _run_planner(self, inner: bool = False):
        if self.busy and not inner:
            return
        session = self.session
        self.busy = "AI planner testing treatment plans on copies of this brain"
        try:
            res = await asyncio.to_thread(plan_for, session, 10.0)
            if session is not self.session:
                return
            self.plan, self.plan_curves, self.plan_expl = res["best"], res["curves"], res["explanation"]
            self.event("plan_ready", {"plan": self.plan, "explanation": self.plan_expl,
                                      "candidates_tested": res.get("candidates_tested")})
            self.narrate(self.plan_expl)
            self.voice_soon("plan_ready", {"explanation": self.plan_expl, "best": self.plan}, True)
        except Exception:  # noqa: BLE001
            log.exception("planner failed")
            self.event("info", {"text": "The treatment planner hit a problem. Please try again."})
        finally:
            if not inner and session is self.session:
                self.busy = None

    # ---------- simulated seizure (dev) ----------
    def start_seizure(self):
        if self.seizure:
            return
        health = np.asarray(self.snapshot()["health"])
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
                self.acc["seizures"] += 1
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
        st = self.snapshot()
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
            "device_online": (t - getattr(self, "device_seen", -1e12)) < 1500,   # Uno Q polled /api/device recently
            "calibrating": self.calibrating,
            "ai": self.ai_info if self.mode == "ai" else None,
            "counters": self.counters,
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
        rest = 0.12          # above the whole-brain background bump on each squeeze: only real pathway activity shows
        return 0.22 * health + 1.6 * np.clip(self._led_act - rest, 0.0, None)

    def device(self) -> dict:
        t = now_ms()
        self.device_seen = t
        st = self.snapshot()
        arm = float(st["arm_function"])
        stim_region = self.stim_vis["region"] if (self.stim_vis and t < self.stim_vis["until"]) else None
        health = np.asarray(st["health"], dtype=float)
        leds = self.leds.apply(led_string(self.led_activity(health), health, stim_region, t), t)
        p = st["patient"]
        sz = self.seizure
        if self.calibrating:
            l1, l2 = "CALIBRATING...", "OPEN+CLOSE x3"
        elif t < self.intro_until:
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
        rgb = ([200, 200, 200] if self.calibrating else
               [255, 0, 60] if (sz and sz["phase"] == "ictal") else arm_rgb(arm))
        fc = st.get("forecast") or {}
        payload = {"leds": leds, "lcd1": pad16(l1), "lcd2": pad16(l2), "rgb": rgb, "servo": self.servo_angle(),
                   "stim": self.stim_vis["kind"] if stim_region else None,
                   "lcd3": pad16("DAY90 FORECAST"),
                   "lcd4": pad16(f"{round(fc.get('p50', 0) * 100)}% +/- {round((fc.get('p90', 0) - fc.get('p10', 0)) * 50)}")}
        if payload != self.device_last:
            self.device_seq += 1
            self.device_last = payload
        return {"seq": self.device_seq, "reset_id": self.reset_id, **payload}

    # ---------- loops ----------
    async def run(self):
        tick = 0
        while True:
            tick += 1
            try:
                if self.calibrating and now_ms() > self.calib_until:
                    self.calibrating = False
                if tick % 4 == 0 and not self.busy:         # ~5 Hz brain-state rows for Tiger Data
                    self.safe_store("brain_state", self.session_id, self.last_day, self.current_activity())
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    tasks = [asyncio.create_task(HUB.run()), asyncio.create_task(HUB.audio_worker()),
             asyncio.create_task(HUB.ai_loop())]
    # generate any missing coach clips in the background (committed clips already cover all of them)
    threading.Thread(target=HUB.narrator.pregenerate, daemon=True).start()
    yield
    for t in tasks:
        t.cancel()
    try:
        HUB.store.close()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="NeuroNest", lifespan=lifespan)


@app.get("/")
async def root():
    return RedirectResponse("/dashboard/")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)


@app.get("/api/health")
async def health():
    return {"ok": True, "t_ms": now_ms(), "brain": BRAIN}


@app.get("/api/stats")
async def stats():
    extra = {}
    try:
        extra = HUB.store.stats() or {}
    except Exception:  # noqa: BLE001
        pass
    return {"attempts_logged": HUB.counters["attempts"], "stims_logged": HUB.counters["stims"],
            "tiger": bool(extra), **extra}


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


@app.get("/qr")
async def qr(request: Request):
    """QR code for visitors' phones: the stimulation page on this server's LAN address."""
    try:
        import qrcode
        host = request.url.hostname
        if not host or host in ("localhost", "127.0.0.1"):
            host = lan_ip()
        port = request.url.port or 8000
        img = qrcode.make(f"http://{host}:{port}/phone/")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)


@app.get("/api/device")
async def device():
    return JSONResponse(HUB.device())


@app.get("/api/report")
async def report(force: int = 0):
    """Daily progress report (Gemini if GEMINI_API_KEY is set, else an automatic template). Cached per state."""
    from report_gen import build_report
    days = HUB.report_days()
    key = (HUB.reset_id, len(days), days[-1]["day"] if days else 0, days[-1].get("attempts") if days else 0)
    if not force and HUB.report_cache and HUB.report_cache[0] == key:
        return JSONResponse(HUB.report_cache[1])
    info = HUB.info
    patient = {"card": info["card"], "age": info["age"], "lesion": info["lesion"], "affected_arm": info.get("affected_arm", "right")}
    rep = await asyncio.to_thread(build_report, patient, [dict(d) for d in days], HUB.plan, HUB.plan_expl)
    rep["generated_t_ms"] = now_ms()
    HUB.report_cache = (key, rep)
    return JSONResponse(rep)


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
    """hands.py / keyboard.py / phone page / dashboard: squeezes, stimulation, calibration, controls."""
    await ws.accept()
    client = {"kind": "unknown", "id": None, "rtt_ms": 0.0}
    HUB.input_clients[ws] = client
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            try:
                if msg.get("type") == "ping":
                    if msg.get("rtt_ms") is not None:
                        client["rtt_ms"] = float(msg["rtt_ms"])
                    await ws.send_text(json.dumps({"type": "pong", "client_t": msg.get("client_t"), "server_t_ms": now_ms()}))
                else:
                    await handle_input(msg, client)
            except Exception:  # noqa: BLE001
                log.exception("bad input message ignored: %s", raw[:200])
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        HUB.input_clients.pop(ws, None)


def _clean_t(t_ms, default: float) -> float:
    """Client timestamps are server-monotonic ms (same laptop); anything implausible falls back to default."""
    try:
        t = float(t_ms)
    except (TypeError, ValueError):
        return default
    return t if abs(t - default) < 5000 else default


async def handle_input(msg: dict, client: dict | None = None):
    client = client if client is not None else {"kind": "dashboard", "rtt_ms": 0.0}
    t = msg.get("type")
    now = now_ms()
    if t == "hello":
        client["kind"], client["id"] = msg.get("client", "unknown"), msg.get("id")
    elif t == "attempt":
        if msg.get("t_ms") is None:
            onset = now - float(msg.get("rtt_ms", client.get("rtt_ms", 0.0)) or 0.0) / 2
        else:
            onset = _clean_t(msg.get("t_ms"), now - RESOLVE_MS)
        effort = float(np.clip(float(msg.get("effort", 0.7)), 0.0, 1.0))
        asyncio.get_running_loop().create_task(HUB.submit_attempt(
            onset, effort, float(msg.get("depth", effort)), float(msg.get("speed", effort)),
            float(msg.get("hold_ms", 300)), msg.get("source", client.get("kind", "input"))))
    elif t == "attempt_onset":
        HUB.event("attempt_onset", {"t_ms": msg.get("t_ms"), "source": msg.get("source")})
    elif t == "stim":
        if msg.get("t_ms") is None:
            rtt = float(msg.get("rtt_ms", client.get("rtt_ms", 0.0)) or 0.0)
            client["rtt_ms"] = rtt
            tt = now - rtt / 2
        else:
            tt = _clean_t(msg.get("t_ms"), now)
        HUB.submit_stim(tt, str(msg.get("region", "lPMCDL")), "calm" if msg.get("kind") == "calm" else "excite",
                        actor="ai" if msg.get("actor") == "ai" else "human",
                        intensity=float(msg.get("intensity", 1.0)))
    elif t == "calibration":
        HUB.calibrating = False
        log.info("calibration: %s", {k: msg.get(k) for k in ("open", "closed", "v_ref")})
    elif t == "calibration_state":
        HUB.calibrating = bool(msg.get("calibrating"))
        HUB.calib_until = now + 15000
    elif t == "control":
        await HUB.control(msg)


# static mounts last, so the API routes above always win
for _name, _dir, _html in (("dashboard", Path(__file__).parent, True), ("phone", ROOT / "web" / "phone", True),
                           ("audio", AUDIO_DIR, False)):
    _dir.mkdir(parents=True, exist_ok=True)
    app.mount(f"/{_name}", StaticFiles(directory=str(_dir), html=_html), name=_name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--card", default="B")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    HUB = Hub(args.card.upper())
    print(f"NeuroNest dashboard dev server ({BRAIN} brain): http://localhost:{args.port}/dashboard/")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")
