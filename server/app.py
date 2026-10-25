"""NeuroNest hub: FastAPI + WebSockets (Person C).

Run: uvicorn server.app:app --host 0.0.0.0 --port 8000
Clock: time.monotonic()*1000 everywhere; UTC only for DB timestamps.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import socket
import statistics
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from server.device import DeviceBuilder
from server.narrator import CLIPS, Narrator

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hub")

ROOT = Path(__file__).resolve().parent.parent
ATTEMPT_WAIT_MS = 400.0
STIM_ACTIVE_MS = 150.0
FF_CHUNK_DAYS = 5


def now_ms() -> float:
    return time.monotonic() * 1000.0


def _json_default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def dumps(o) -> str:
    return json.dumps(o, default=_json_default, separators=(",", ":"))


# --------------------------------------------------------------------------
# Brain backend selection (real A modules, else inline fake)
# --------------------------------------------------------------------------
def load_backend():
    if os.getenv("USE_FAKE", "0") != "1":
        try:
            from brain.session import Session  # type: ignore
            from brain.layout import led_string  # type: ignore
            try:
                from ai.planner import plan_for  # type: ignore
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "ai.planner unavailable (%s); using fallback planner", e)
                from server.fallback_brain import plan_for  # type: ignore
            log.info("using REAL brain.session")
            return Session, led_string, plan_for, False
        except Exception as e:  # noqa: BLE001
            log.warning(
                "brain.session not importable (%s); using fallback fake", e)
    from server.fallback_brain import FakeSession, led_string, plan_for
    return FakeSession, led_string, plan_for, True


class NullStorage:
    """Stand-in until storage/tiger.py (TigerWriter) exists. Every method is a no-op."""

    def __getattr__(self, name):
        return lambda *a, **k: None


def load_storage():
    try:
        from storage.tiger import TigerWriter  # type: ignore
        return TigerWriter(os.getenv("TIGER_DATABASE_URL") or None)
    except Exception as e:  # noqa: BLE001
        log.info("storage.tiger not available (%s); logging disabled", e)
        return NullStorage()


# --------------------------------------------------------------------------
# Effort stats + fatigue
# --------------------------------------------------------------------------
class EffortStats:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.recent: deque[float] = deque(maxlen=30)
        self.first: list[float] = []
        self.count = 0
        self.fatigue = False
        self.fatigue_attempts = 0
        self.last: float | None = None
        self.break_said = False

    @property
    def session_start(self) -> float | None:
        return float(np.mean(self.first)) if self.first else None

    @property
    def rolling(self) -> float | None:
        r = list(self.recent)[-8:]
        return float(np.mean(r)) if r else None

    def add(self, e: float) -> list[str]:
        """Returns actions: 'fatigue_start', 'fatigue_1', 'fatigue_2'."""
        acts: list[str] = []
        self.last = e
        self.recent.append(e)
        self.count += 1
        if len(self.first) < 5:
            self.first.append(e)
        ss, ro = self.session_start, self.rolling
        now_f = self.count >= 10 and ss is not None and ro is not None and ro < 0.8 * ss
        if now_f and not self.fatigue:
            self.fatigue, self.fatigue_attempts, self.break_said = True, 0, False
            acts += ["fatigue_start", "fatigue_1"]
        elif now_f:
            self.fatigue_attempts += 1
            if self.fatigue_attempts >= 8 and not self.break_said:
                self.break_said = True
                acts.append("fatigue_2")
        elif not now_f and self.fatigue:
            self.fatigue = False
        return acts

    def mean_last(self, n: int = 20) -> float:
        r = list(self.recent)[-n:]
        return float(np.mean(r)) if r else 0.0

    def as_dict(self) -> dict:
        def r3(v): return None if v is None else round(v, 3)  # noqa: E731
        return {"last": r3(self.last), "rolling": r3(self.rolling), "session_start": r3(self.session_start),
                "fatigue": self.fatigue}


class LedLimiter:
    """Cap each LED's brightness changes at ~3/s (photosensitivity)."""
    MIN_MS = 333.0

    def __init__(self) -> None:
        self.cur = ["0"] * 104
        self.t = [0.0] * 104

    def apply(self, target: str, t_ms: float) -> str:
        if len(target) != 104:
            return "".join(self.cur)
        for i, ch in enumerate(target):
            if ch != self.cur[i] and t_ms - self.t[i] >= self.MIN_MS:
                self.cur[i], self.t[i] = ch, t_ms
        return "".join(self.cur)


# --------------------------------------------------------------------------
# Hub
# --------------------------------------------------------------------------
class Hub:
    def __init__(self) -> None:
        self.SessionCls, self.led_string, self.plan_for, self.is_fake = load_backend()
        self.store = load_storage()
        self.narrator = Narrator()
        self.device = DeviceBuilder()
        self.leds = LedLimiter()
        self.session = None
        self.session_id: str | None = None
        self.sess_lock = threading.Lock()
        self.busy = False  # fast-forward / planner running
        self.mode = "human"
        self.state_clients: set[WebSocket] = set()
        self.input_clients: dict[WebSocket, dict] = {}
        self.stats = EffortStats()
        self.pending_stims: list[dict] = []
        self.stim: dict | None = None
        self.stim_until = 0.0
        self.timing: dict | None = None
        self.timing_until = 0.0
        self.anim: tuple[float, np.ndarray,
                         float] | None = None  # (t0, frames, dt)
        self.activity = np.zeros(76)
        self.health = np.ones(76)
        self.cached_state: dict = {}
        self.curve = {"days": [], "arm": []}
        self.curve_dirty = False
        self.edges: list = []
        self.edges_dirty = False
        self.last_edges_sent = 0.0
        self.forecast: dict | None = None
        self.plan: dict | None = None
        self.plan_curves: dict | None = None
        self.narration: dict | None = None
        self.narration_until = 0.0
        self.calibrating = False
        self.calib_until = 0.0
        self.intro_until = 0.0
        self.ff_active = False
        self.ff_day = 0
        self.last_day = 1
        self.counters = {"attempts": 0, "stims": 0}
        self.last_attempt_t = 0.0
        # AI mode
        self.cues: deque[float] = deque(maxlen=10)
        self.lags: deque[float] = deque(maxlen=5)
        self.ai_info: dict | None = None
        self.tasks: list[asyncio.Task] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.audio_q: asyncio.Queue = asyncio.Queue()
        self.audio_until = 0.0

    # ---------------- broadcast ----------------
    async def _safe_send(self, ws: WebSocket, data: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(data), timeout=0.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def broadcast(self, msg: dict) -> None:
        if not self.state_clients:
            return
        data = dumps(msg)
        clients = list(self.state_clients)
        res = await asyncio.gather(*[self._safe_send(c, data) for c in clients])
        for c, ok in zip(clients, res):
            if not ok:
                self.state_clients.discard(c)

    def emit(self, kind: str, data: dict | None = None) -> None:
        """Fire-and-forget event to state clients."""
        asyncio.ensure_future(self.broadcast(
            {"type": "event", "kind": kind, "data": data or {}}))

    # ---- audio: one sequential queue so clips never overlap ----
    @staticmethod
    def _clip_secs(url: str | None) -> float:
        """Estimate mp3 length from file size (mp3_44100_128 = 16000 bytes/s). No extra deps."""
        try:
            size = (ROOT / "server" / "audio_cache" /
                    Path(str(url)).name).stat().st_size
            return min(30.0, max(0.8, size / 16000.0))
        except Exception:  # noqa: BLE001
            return 2.5

    def audio_busy(self) -> bool:
        return now_ms() < self.audio_until or not self.audio_q.empty()

    async def audio(self, url: str | None) -> None:
        """Queue a coach clip; sent to /ws/state as an `audio` message when the previous one has finished."""
        if url and self.audio_q.qsize() < 4:
            self.audio_q.put_nowait(("clip", url))

    async def audio_worker(self) -> None:
        while True:
            kind, item = await self.audio_q.get()
            try:
                if kind == "clip":
                    dur = self._clip_secs(item)
                    await self.broadcast({"type": "audio", "url": item})
                # narration: delivered via state.narration (dashboard plays narration.audio_url)
                else:
                    dur = self._clip_secs(item.get("audio_url"))
                    self.narration, self.narration_until = item, now_ms() + max(12000.0,
                                                                                dur * 1000 + 2000)
                self.audio_until = now_ms() + dur * 1000 + 250
                await asyncio.sleep(dur + 0.25)
            except Exception as e:  # noqa: BLE001
                log.warning("audio worker error: %s", e)

    async def coach(self, key: str, force: bool = False) -> None:
        url = self.narrator.coach(key, force=force)
        await self.audio(url)

    # ---------------- session mgmt ----------------
    async def new_patient(self, card: str = "B") -> None:
        card = card if card in ("A", "B", "R") else "B"
        seed = int(time.time()) % 100000

        def make():
            with self.sess_lock:
                s = self.SessionCls(card, seed)
                return s, s.info(), s.state()

        s, info, st = await asyncio.to_thread(make)
        self.session, self.session_id = s, f"live-{uuid.uuid4().hex[:8]}"
        self.stats.reset()
        self.pending_stims.clear()
        self.anim, self.stim, self.timing = None, None, None
        self.plan, self.plan_curves, self.forecast = None, None, None
        self.cues.clear(), self.lags.clear()
        self.ai_info = None
        self.cached_state = st
        self.health = np.array(st.get("health", np.ones(76)), dtype=float)
        self.last_day = int(st.get("day", 1))
        self.curve = {"days": [self.last_day], "arm": [
            float(st.get("arm_function", 0.0))]}
        self.curve_dirty = True
        self.intro_until = now_ms() + 5000
        traits = getattr(s, "traits", None)
        self.safe_store("session", self.session_id, info.get("card", card), info.get("age"),
                        info.get("lesion"), traits, "live")
        self.refresh_forecast()
        self.emit("patient", {"card": info.get("card", card), "age": info.get(
            "age"), "lesion": info.get("lesion")})
        await self.coach(f"patient_{card}" if card in ("A", "B") else "patient_B", force=True)
        asyncio.create_task(self.narrate(
            "patient", {"card": card, "age": info.get("age")}))

    def safe_store(self, method: str, *a, **k) -> None:
        try:
            getattr(self.store, method)(*a, **k)
        except Exception as e:  # noqa: BLE001
            log.warning("storage.%s failed: %s", method, e)

    def refresh_forecast(self) -> None:
        try:
            fc = self.session.twins.forecast()
            self.forecast = {"p10": round(float(fc["p10"]), 3), "p50": round(float(fc["p50"]), 3),
                             "p90": round(float(fc["p90"]), 3), "n_alive": int(fc["n_alive"])}
            self.safe_store("forecast", self.session_id,
                            self.last_day, fc, "twins")
        except Exception as e:  # noqa: BLE001
            log.debug("forecast unavailable: %s", e)

    async def narrate(self, kind: str, data: dict, force: bool = False) -> None:
        try:
            n = await asyncio.to_thread(self.narrator.narrate, kind, data, force)
        except Exception as e:  # noqa: BLE001
            log.warning("narrate failed: %s", e)
            return
        if n:
            if n.get("audio_url"):
                # queued behind any playing clip; shown/played via state.narration only (no duplicate audio msg)
                self.audio_q.put_nowait(("narration", n))
            else:
                self.narration, self.narration_until = n, now_ms() + 12000

    # ---------------- input handling ----------------
    def _clean_t(self, t_ms, default: float) -> float:
        try:
            t = float(t_ms)
        except (TypeError, ValueError):
            return default
        return t if abs(t - default) < 5000 else default

    async def on_attempt_onset(self, m: dict) -> None:
        self.last_attempt_t = now_ms()
        self.emit("attempt_onset", {"t_ms": m.get(
            "t_ms"), "source": m.get("source")})

    async def on_attempt(self, m: dict, client: dict) -> None:
        if self.session is None:
            return
        now = now_ms()
        src = m.get("source", client.get("kind", "webcam"))
        if src == "phone":
            t = now - client.get("rtt_ms", 0.0) / 2.0 - ATTEMPT_WAIT_MS * 0.0
        else:
            t = self._clean_t(m.get("t_ms"), now - ATTEMPT_WAIT_MS)
        e = float(np.clip(float(m.get("effort", 0.6)), 0, 1))
        args = (t, e, float(m.get("depth", e)), float(
            m.get("speed", e)), float(m.get("hold_ms", 0.0)))
        asyncio.create_task(self.resolve_attempt(self.session, args, src))

    async def resolve_attempt(self, session, args, source: str) -> None:
        t, effort, depth, speed, hold = args
        await asyncio.sleep(max(0.0, (t + ATTEMPT_WAIT_MS - now_ms()) / 1000.0))
        if session is not self.session:
            return
        # drain stims relevant to this attempt (incl. up to onset+400 ms)
        cutoff = t + ATTEMPT_WAIT_MS
        mine = [s for s in self.pending_stims if s["t_ms"] <= cutoff]
        self.pending_stims = [
            s for s in self.pending_stims if s["t_ms"] > cutoff]

        def run():
            with self.sess_lock:
                for s in mine:
                    session.submit_stim(
                        s["t_ms"], s["region"], s["kind"], s["intensity"], s["actor"])
                return session.submit_attempt(t, effort, depth, speed, hold)

        try:
            res = await asyncio.to_thread(run)
        except Exception as e:  # noqa: BLE001
            log.exception("submit_attempt failed: %s", e)
            return
        if session is not self.session:
            return
        await self.after_attempt(res, source, t, args)

    async def after_attempt(self, res, source: str, t_onset: float, args=None) -> None:
        now = now_ms()
        frames = np.clip(np.asarray(res.frames, dtype=float), 0, 1)
        self.anim = (now, frames, float(res.frame_dt_ms))
        self.device.servo.trigger(float(res.arm_output), now)
        self.counters["attempts"] += 1
        offset, verdict = res.stim_offset_ms, res.stim_verdict
        self.timing = {"offset_ms": None if offset is None else round(float(offset), 1), "verdict": verdict} \
            if verdict is not None or offset is not None else None
        self.timing_until = now + 2000
        # weight change -> edges
        try:
            wc = np.asarray(res.weight_change, dtype=np.float32)
            flat = np.abs(wc).ravel()
            k = min(150, flat.size)
            top = np.argpartition(flat, -k)[-k:]
            top = top[np.argsort(-flat[top])]
            self.edges = [[int(i // 76), int(i % 76), round(float(wc.ravel()[i]), 4)]
                          for i in top if flat[i] > 1e-5]
            self.edges_dirty = True
            self.safe_store("weights", self.session_id, res.day, wc, 1e-3)
        except Exception as e:  # noqa: BLE001
            log.debug("edges failed: %s", e)
        if args is not None:
            self.safe_store("attempt_full", self.session_id, res.day, res.effort, args[2], args[3], args[4],
                            res.arm_output, res.arm_function, res.stim_offset_ms, res.stim_verdict, res.gate, source)
        else:
            self.safe_store("attempt", self.session_id, res.day, res, source)
        # effort + fatigue
        acts = self.stats.add(float(res.effort))
        if "fatigue_start" in acts:
            self.safe_store("stim", self.session_id, res.day,
                            "system", "-", "fatigue", 0.0, 0.0)
            self.emit("fatigue", {"rolling": self.stats.rolling,
                      "session_start": self.stats.session_start})
            asyncio.create_task(self.narrate("fatigue", self.stats.as_dict()))
        if "fatigue_1" in acts:
            await self.coach("fatigue_1", force=True)
        if "fatigue_2" in acts:
            await self.coach("fatigue_2", force=True)
        # AI mode: learn reaction lag from the last cue
        if self.mode == "ai":
            cue = next((c for c in reversed(self.cues)
                       if 0 < t_onset - c < 1500), None)
            if cue is not None:
                self.lags.append(t_onset - cue)
        # coach timing verdict ~every 3rd stimulated attempt
        if verdict in ("good", "early", "late") and self.counters["attempts"] % 3 == 0 and not acts:
            await self.coach({"good": "good_timing"}.get(verdict, verdict))
        # day rollover
        arm = float(res.arm_function)
        if int(res.day) > self.last_day:
            await self.on_day_end(self.last_day, arm)
            self.last_day = int(res.day)
        self.emit("attempt", {"t_ms": res.t_ms, "effort": round(float(res.effort), 3),
                              "arm_output": round(float(res.arm_output), 3), "arm_function": round(arm, 3),
                              "stim_offset_ms": self.timing and self.timing["offset_ms"],
                              "stim_verdict": verdict, "attempts_today": int(res.attempts_today),
                              "day": int(res.day)})

    async def on_day_end(self, day: int, arm: float, day_result=None) -> None:
        self.curve["days"].append(day)
        self.curve["arm"].append(round(arm, 4))
        self.curve_dirty = True
        if day_result is not None:
            self.safe_store("daily", self.session_id, day_result,
                            self.plan and self.plan.get("best"))
        self.refresh_forecast()
        self.emit("day_end", {"day": day, "arm_function": round(
            arm, 3), "mean_effort": round(self.stats.mean_last(), 3)})
        if day % 5 == 0:
            asyncio.create_task(self.narrate(
                "day_end", {"day": day, "arm": round(arm, 2)}))

    async def on_stim(self, m: dict, client: dict) -> None:
        if self.session is None:
            return
        now = now_ms()
        rtt = float(m.get("rtt_ms", client.get("rtt_ms", 0.0)) or 0.0)
        if client.get("kind") == "phone" or m.get("client_t") is not None and m.get("t_ms") is None:
            t = now - rtt / 2.0
            client["rtt_ms"] = rtt
        else:
            t = self._clean_t(m.get("t_ms"), now)
        region = str(m.get("region", "lPMCDL"))
        kind = "calm" if m.get("kind") == "calm" else "excite"
        actor = "ai" if m.get("actor") == "ai" else "human"
        self.add_stim(t, region, kind, float(m.get("intensity", 1.0)), actor)
        await self.send_to(client["ws"], {"type": "ack", "kind": "stim", "t_ms": t})

    def add_stim(self, t: float, region: str, kind: str, intensity: float, actor: str) -> None:
        now = now_ms()
        self.pending_stims = [
            s for s in self.pending_stims if now - s["t_ms"] < 10000]
        self.pending_stims.append({"t_ms": t, "region": region, "kind": kind, "intensity": intensity,
                                   "actor": actor})
        self.stim = {"region": region, "kind": kind}
        self.stim_until = now + STIM_ACTIVE_MS
        self.counters["stims"] += 1
        self.safe_store("stim", self.session_id, self.last_day,
                        actor, region, kind, intensity, None)
        self.emit("stim", {"region": region, "kind": kind,
                  "actor": actor, "t_ms": t})

    async def send_to(self, ws: WebSocket, msg: dict) -> None:
        await self._safe_send(ws, dumps(msg))

    # ---------------- controls ----------------
    async def control(self, m: dict) -> dict:
        a = m.get("action")
        if a == "new_patient":
            await self.new_patient(m.get("card", "B"))
        elif a == "reset":
            self.mode = "human"
            await self.new_patient(self.cached_state.get("patient", {}).get("card", "B"))
        elif a == "end_day":
            if self.session is None:
                return {"ok": False, "error": "no patient"}
            await self.end_day()
        elif a == "fast_forward":
            if self.session is None or self.busy:
                return {"ok": False, "error": "busy or no patient"}
            asyncio.create_task(self.fast_forward(
                int(m.get("days", 90)), m.get("plan", "standard")))
        elif a == "run_planner":
            if self.session is None or self.busy:
                return {"ok": False, "error": "busy or no patient"}
            asyncio.create_task(self.run_planner())
        elif a == "set_mode":
            self.mode = "ai" if m.get("mode") == "ai" else "human"
        elif a == "calibrate":
            self.calibrating, self.calib_until = True, now_ms() + 12000
            await self.send_inputs({"type": "control", "action": "calibrate"})
            await self.coach("calibrate", force=True)
        elif a == "report":
            asyncio.create_task(self.make_report())
        else:
            return {"ok": False, "error": f"unknown action {a}"}
        return {"ok": True}

    async def send_inputs(self, msg: dict) -> None:
        data = dumps(msg)
        for ws in list(self.input_clients):
            if not await self._safe_send(ws, data):
                self.input_clients.pop(ws, None)

    async def end_day(self) -> None:
        session = self.session

        def run():
            with self.sess_lock:
                return session.end_day()

        dr = await asyncio.to_thread(run)
        self.last_day = int(dr.day) + 1
        await self.on_day_end(int(dr.day), float(dr.arm_function), dr)

    async def fast_forward(self, days: int, plan_kind: str) -> None:
        session = self.session
        self.busy, self.ff_active = True, True
        try:
            plan = None
            if plan_kind == "none":
                plan = {"target": None, "sessions_per_day": 0}
            elif plan_kind == "ai":
                if self.plan is None:
                    await self.run_planner(inner=True)
                plan = (self.plan or {}).get("best")
            await self.coach("fast_forward", force=True)
            done = 0
            while done < days and session is self.session:
                n = min(FF_CHUNK_DAYS, days - done)

                def run(n=n, plan=plan):
                    with self.sess_lock:
                        return session.fast_forward(n, plan)

                results = await asyncio.to_thread(run)
                for dr in results:
                    done += 1
                    self.ff_day = int(dr.day)
                    self.last_day = int(dr.day) + 1
                    await self.on_day_end(int(dr.day), float(dr.arm_function), dr)
                if not results:
                    break
                await asyncio.sleep(0)
            await self.coach("recovery_complete", force=True)
            asyncio.create_task(self.narrate("fast_forward_done", {
                                "days": days, "plan": plan_kind}, True))
        except Exception as e:  # noqa: BLE001
            log.exception("fast_forward failed: %s", e)
        finally:
            self.busy, self.ff_active = False, False

    async def run_planner(self, inner: bool = False) -> None:
        session = self.session
        if not inner:
            self.busy = True
        try:
            def run():
                with self.sess_lock:
                    return self.plan_for(session, 10.0)

            plan = await asyncio.to_thread(run)
            self.plan, self.plan_curves = plan, plan.get("curves")
            self.emit("plan_ready", {"best": plan.get(
                "best"), "candidates_tested": plan.get("candidates_tested")})
            asyncio.create_task(self.narrate("plan_ready", {"explanation": plan.get("explanation"),
                                                            "best": plan.get("best")}, True))
        except Exception as e:  # noqa: BLE001
            log.exception("planner failed: %s", e)
        finally:
            if not inner:
                self.busy = False

    async def make_report(self) -> None:
        st = self.cached_state
        info = {"patient": {k: st.get("patient", {}).get(k) for k in ("card", "age", "lesion", "affected_arm")},
                "day": st.get("day"), "arm_function": st.get("arm_function"), "curve": self.curve,
                "effort": self.stats.as_dict(), "attempts_logged": self.counters,
                "forecast": self.forecast, "plan": (self.plan or {}).get("best"),
                "plan_explanation": (self.plan or {}).get("explanation")}
        md = await asyncio.to_thread(self.narrator.report, info)
        await self.broadcast({"type": "report", "markdown": md})

    # ---------------- AI mode ----------------
    def ai_plan(self) -> dict:
        try:
            if self.session is not None and hasattr(self.session, "planned_stim"):
                return self.session.planned_stim()
        except Exception:  # noqa: BLE001
            pass
        best = (self.plan or {}).get("best")
        if best and best.get("target"):
            return best
        return {"target": "lPMCDL", "kind": "excite", "offset_ms": -80, "intensity": 1.0}

    async def ai_loop(self) -> None:
        while True:
            await asyncio.sleep(3.0)
            if self.mode != "ai" or self.session is None or self.busy or self.audio_busy():
                continue  # skip this cue rather than talk over another clip
            send = now_ms()
            T = send + self.narrator.now_offset_ms
            self.cues.append(T)
            await self.audio(self.narrator.clip_url("squeeze_now"))
            lag = statistics.median(self.lags) if self.lags else 350.0
            sigma = statistics.pstdev(self.lags) if len(
                self.lags) >= 3 else 100.0
            plan = self.ai_plan()
            if plan.get("target"):
                t_stim = max(T + lag + float(plan.get("offset_ms", -80)), send)
                self.ai_info = {"predicted_onset_ms": round(T + lag), "pm_ms": round(max(sigma, 20)),
                                "lag_ms": round(lag), "cue_t_ms": round(T)}
                asyncio.get_running_loop().call_later(
                    max(0.0, (t_stim - now_ms()) / 1000.0), self._ai_fire, plan, t_stim)

    def _ai_fire(self, plan: dict, t_stim: float) -> None:
        if self.mode == "ai" and self.session is not None:
            self.add_stim(t_stim, plan["target"], plan.get(
                "kind", "excite"), float(plan.get("intensity", 1.0)), "ai")

    # ---------------- background loops ----------------
    def snapshot_state(self) -> dict:
        s = self.cached_state
        if self.session is None:
            return {"patient": None, "day": 0, "attempts_today": 0, "arm_function": 0.0, "arm_output": 0.0}
        return s

    async def state_loop(self) -> None:
        tick = 0
        next_t = time.monotonic()
        while True:
            next_t += 0.05
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))
            tick += 1
            try:
                await self.state_tick(tick)
            except Exception as e:  # noqa: BLE001
                log.exception("state tick failed: %s", e)

    async def state_tick(self, tick: int) -> None:
        now = now_ms()
        if self.session is not None and not self.busy and tick % 4 == 0:
            if self.sess_lock.acquire(blocking=False):
                try:
                    self.cached_state = self.session.state()
                    h = self.cached_state.get("health")
                    if h is not None:
                        self.health = np.asarray(h, dtype=float)
                except Exception as e:  # noqa: BLE001
                    log.debug("state() failed: %s", e)
                finally:
                    self.sess_lock.release()
        if self.session is not None:
            if self.anim is not None:
                t0, frames, dt = self.anim
                i = int((now - t0) / dt)
                if i < len(frames):
                    self.activity = frames[i]
                else:
                    self.anim = None
            if self.anim is None:
                if not self.busy and self.sess_lock.acquire(blocking=False):
                    try:
                        self.activity = np.clip(np.asarray(
                            self.session.idle_frame(now), dtype=float), 0, 1)
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        self.sess_lock.release()
            if tick % 4 == 0 and not self.busy:
                self.safe_store("brain_state", self.session_id,
                                self.last_day, self.activity)
        if self.calibrating and now > self.calib_until:
            self.calibrating = False
        if self.stim and now > self.stim_until:
            self.stim = None
        if self.narration and now > self.narration_until:
            self.narration = None
        if not self.state_clients:
            return
        st = self.snapshot_state()
        msg = {
            "type": "state", "t_ms": round(now, 1), "mode": self.mode,
            "patient": st.get("patient"), "day": st.get("day"), "attempts_today": st.get("attempts_today"),
            "arm_function": st.get("arm_function"), "arm_output": st.get("arm_output"),
            "effort": self.stats.as_dict(),
            "activity": np.round(self.activity, 3), "health": np.round(self.health, 3),
            "stim": ({**self.stim, "active": True} if self.stim else None),
            "timing": self.timing if now < self.timing_until + 2000 else None,
            "forecast": self.forecast, "plan": (self.plan or {}).get("best") if self.plan else None,
            "narration": self.narration, "calibrating": self.calibrating, "busy": self.busy,
            "ai": self.ai_info if self.mode == "ai" else None,
            "counters": self.counters,
        }
        if self.edges_dirty and now - self.last_edges_sent >= 1000:
            msg["edges"] = self.edges
            self.edges_dirty, self.last_edges_sent = False, now
        if self.curve_dirty:
            msg["curve"] = self.curve
            self.curve_dirty = False
        if self.plan_curves is not None and tick % 20 == 0:
            msg["plan_curves"] = self.plan_curves
        await self.broadcast(msg)

    async def device_loop(self) -> None:
        next_t = time.monotonic()
        while True:
            next_t += 0.1
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))
            try:
                self.build_device()
            except Exception as e:  # noqa: BLE001
                log.exception("device build failed: %s", e)

    def build_device(self) -> None:
        now = now_ms()
        st = self.snapshot_state()
        has = self.session is not None
        card = (st.get("patient") or {}).get("card", "-") if has else "-"
        age = (st.get("patient") or {}).get("age", 0) if has else 0
        if has:
            try:
                raw = self.led_string(
                    self.activity, self.health, (self.stim or {}).get("region"), now)
            except Exception as e:  # noqa: BLE001
                log.debug("led_string failed: %s", e)
                raw = "0" * 104
        else:
            raw = "0" * 104
        leds = self.leds.apply(raw, now)
        self.device.build(
            t_ms=now, leds=leds, has_patient=has, calibrating=self.calibrating,
            patient_intro_until=self.intro_until, card=card, age=int(age or 0), day=int(st.get("day") or 0),
            arm=float(st.get("arm_function") or 0.0), effort_last=self.stats.last,
            fatigue=self.stats.fatigue, timing=self.timing, timing_until=self.timing_until,
            fast_forward=self.ff_active, ff_day=self.ff_day, forecast=self.forecast)


hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    hub.tasks = [asyncio.create_task(hub.state_loop()), asyncio.create_task(hub.device_loop()),
                 asyncio.create_task(hub.ai_loop()), asyncio.create_task(hub.audio_worker())]
    threading.Thread(target=hub.narrator.pregenerate, daemon=True).start()
    log.info("NeuroNest hub up (fake brain=%s)", hub.is_fake)
    yield
    for t in hub.tasks:
        t.cancel()


app = FastAPI(title="NeuroNest", lifespan=lifespan)


@app.get("/")
async def root():
    return RedirectResponse("/dashboard/")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Browsers always request /favicon.ico; answer with an empty 204 instead of a 404 error
    return Response(status_code=204)


@app.get("/api/health")
async def health():
    return {"ok": True, "t_ms": now_ms()}


@app.get("/api/device")
async def device():
    return JSONResponse(hub.device.payload)


@app.post("/api/control")
async def api_control(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "bad json"}, status_code=400)
    return JSONResponse(await hub.control(body))


@app.get("/api/stats")
async def stats():
    extra = {}
    try:
        extra = hub.store.stats() or {}
    except Exception:  # noqa: BLE001
        pass
    return {"attempts_logged": hub.counters["attempts"], "stims_logged": hub.counters["stims"],
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
    try:
        import qrcode
        port = request.url.port or int(os.getenv("SERVER_PORT", "8000"))
        img = qrcode.make(f"http://{lan_ip()}:{port}/phone/")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=503)


@app.websocket("/ws/state")
async def ws_state(ws: WebSocket):
    await ws.accept()
    hub.state_clients.add(ws)
    hub.curve_dirty = True
    hub.edges_dirty = bool(hub.edges)
    try:
        while True:
            await ws.receive_text()  # ignore; keeps connection open
    except (WebSocketDisconnect, Exception):  # noqa: BLE001
        hub.state_clients.discard(ws)


@app.websocket("/ws/input")
async def ws_input(ws: WebSocket):
    await ws.accept()
    client = {"ws": ws, "kind": "unknown", "id": None, "rtt_ms": 0.0}
    hub.input_clients[ws] = client
    try:
        while True:
            raw = await ws.receive_text()
            try:
                m = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            try:
                await handle_input(m, client)
            except Exception as e:  # noqa: BLE001
                log.exception("input handler error: %s", e)
    except (WebSocketDisconnect, Exception):  # noqa: BLE001
        pass
    finally:
        hub.input_clients.pop(ws, None)


async def handle_input(m: dict, client: dict) -> None:
    t = m.get("type")
    if t == "hello":
        client["kind"], client["id"] = m.get("client", "unknown"), m.get("id")
    elif t == "ping":
        await hub.send_to(client["ws"], {"type": "pong", "client_t": m.get("client_t"), "server_t_ms": now_ms()})
        if m.get("rtt_ms") is not None:
            client["rtt_ms"] = float(m["rtt_ms"])
    elif t == "attempt_onset":
        await hub.on_attempt_onset(m)
    elif t == "attempt":
        await hub.on_attempt(m, client)
    elif t == "stim":
        await hub.on_stim(m, client)
    elif t == "calibration":
        hub.calibrating = False
        log.info("calibration: %s", {k: m.get(k)
                 for k in ("open", "closed", "v_ref")})
    elif t == "calibration_state":
        hub.calibrating = bool(m.get("calibrating"))
        hub.calib_until = now_ms() + 15000
    elif t == "control":
        await hub.control(m)


# static mounts last (so API routes win)
for _name, _dir, _html in (("dashboard", ROOT / "web" / "dashboard", True), ("phone", ROOT / "web" / "phone", True),
                           ("audio", ROOT / "server" / "audio_cache", False)):
    _dir.mkdir(parents=True, exist_ok=True)
    app.mount(f"/{_name}", StaticFiles(directory=str(_dir),
              html=_html), name=_name)
