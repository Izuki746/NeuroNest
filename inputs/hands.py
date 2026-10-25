"""Webcam hand squeeze detection + Effort Tracking (Person C).

Run:  python inputs/hands.py --server ws://127.0.0.1:8000/ws/input --camera 0

Keys: C calibrate, Q quit, SPACE manual attempt.
Time base: time.monotonic()*1000, same clock as the server (same laptop).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import queue
import statistics
import threading
import time
from collections import deque
from pathlib import Path
import mediapipe as mp
import numpy as np

log = logging.getLogger("hands")

TIPS = [8, 12, 16, 20]
CALIB_SECONDS = 6.0
ATTEMPT_WINDOW_MS = 400.0
REFRACTORY_MS = 300.0
NO_HAND_S = 1.0


def now_ms() -> float:
    return time.monotonic() * 1000.0


# --------------------------------------------------------------------------
# Pure logic (no camera needed, unit-testable)
# --------------------------------------------------------------------------
def raw_openness(lm) -> float:
    """lm: sequence of 21 (x, y) points. Ratio of mean fingertip distance to palm size."""
    def d(a, b):
        return math.hypot(lm[a][0] - lm[b][0], lm[a][1] - lm[b][1])
    palm = max(d(0, 9), 1e-6)
    return float(np.mean([d(0, t) for t in TIPS]) / palm)


class Calibrator:
    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []  # (t_s, raw)

    def add(self, t_s: float, raw: float) -> None:
        self.samples.append((t_s, raw))

    def result(self) -> dict | None:
        if len(self.samples) < 10:
            return None
        ts = np.array([s[0] for s in self.samples])
        raw = np.array([s[1] for s in self.samples])
        open_ = float(np.percentile(raw, 95))
        closed = float(np.percentile(raw, 5))
        if open_ - closed < 0.3:
            return None
        dt = np.diff(ts)
        dt[dt <= 1e-4] = 1e-4
        vel = np.diff(raw) / dt / max(open_ - closed,
                                      1e-6)  # in openness-units/s
        closing = -vel[vel < 0]
        if closing.size == 0:
            return None
        # peak closing speeds: take the top-3 local bursts, approximated by top quantile
        thresh = np.percentile(closing, 90)
        peaks = closing[closing >= thresh]
        v_ref = float(np.median(peaks)) if peaks.size else float(closing.max())
        return {"open": open_, "closed": closed, "v_ref": max(v_ref, 0.5)}


class SqueezeDetector:
    """IDLE -> ONSET -> REFRACTORY state machine. Feed calibrated o and velocity v."""

    def __init__(self, v_ref: float = 3.0) -> None:
        self.v_ref = v_ref
        self.state = "IDLE"
        self.t_onset = 0.0
        self.min_o = 1.0
        self.peak_speed = 0.0
        self.hold_ms = 0.0
        self.last_t = 0.0
        self.refractory_until = 0.0
        self.armed = True  # needs o > 0.6 before the next squeeze

    def update(self, t_ms: float, o: float, v: float):
        """Returns list of events: ("onset", t_ms) / ("attempt", dict)."""
        events = []
        dt = max(t_ms - self.last_t, 0.0) if self.last_t else 0.0
        self.last_t = t_ms
        if self.state == "IDLE":
            if o > 0.6:
                self.armed = True
            if self.armed and o < 0.5 and v < -0.5 * self.v_ref:
                self.state = "ONSET"
                self.armed = False
                self.t_onset = t_ms
                self.min_o = o
                self.peak_speed = -v
                self.hold_ms = 0.0
                events.append(("onset", t_ms))
        elif self.state == "ONSET":
            self.min_o = min(self.min_o, o)
            self.peak_speed = max(self.peak_speed, -v)
            if o < 0.35:
                self.hold_ms += dt
            if t_ms >= self.t_onset + ATTEMPT_WINDOW_MS or o > 0.6:
                events.append(("attempt", self.finish()))
                self.state = "REFRACTORY"
                self.refractory_until = t_ms + REFRACTORY_MS
        elif self.state == "REFRACTORY":
            if t_ms >= self.refractory_until:
                self.state = "IDLE"
        return events

    def finish(self) -> dict:
        depth = 1.0 - self.min_o
        depth_s = float(np.clip(depth / 0.9, 0, 1))
        speed_s = float(np.clip(self.peak_speed / max(self.v_ref, 1e-6), 0, 1))
        hold_s = float(np.clip(self.hold_ms / 400.0, 0, 1))
        effort = 0.5 * depth_s + 0.3 * speed_s + 0.2 * hold_s
        return {
            "type": "attempt", "t_ms": self.t_onset, "effort": round(effort, 3),
            "depth": round(depth_s, 3), "speed": round(speed_s, 3),
            "hold_ms": round(self.hold_ms, 1), "source": "webcam",
        }


# --------------------------------------------------------------------------
# WebSocket client thread with reconnect + outbound queue
# --------------------------------------------------------------------------
class WSClient(threading.Thread):
    def __init__(self, url: str) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.out: "queue.Queue[dict]" = queue.Queue(maxsize=500)
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.connected = False
        self._halt = False

    def send(self, msg: dict) -> None:
        try:
            self.out.put_nowait(msg)
        except queue.Full:
            pass

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        import websockets
        while not self._halt:
            try:
                async with websockets.connect(self.url, ping_interval=10) as ws:
                    self.connected = True
                    log.info("connected to %s", self.url)
                    await ws.send(json.dumps({"type": "hello", "client": "hands", "id": "hands-1"}))
                    await asyncio.gather(self._tx(ws), self._rx(ws))
            except Exception as e:  # noqa: BLE001
                log.warning("ws error: %s (retry in 1s)", e)
            self.connected = False
            await asyncio.sleep(1.0)

    async def _tx(self, ws) -> None:
        while True:
            try:
                msg = self.out.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
            try:
                await ws.send(json.dumps(msg))
            except Exception:
                self.out.put(msg)  # keep it for after reconnect
                raise

    async def _rx(self, ws) -> None:
        async for raw in ws:
            try:
                self.inbox.put(json.loads(raw))
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# MediaPipe wrapper (solutions API first, Tasks API fallback)
# --------------------------------------------------------------------------
class HandTracker:
    def __init__(self) -> None:

        self.mp = mp
        self.mode = None
        if hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
            self.hands = mp.solutions.hands.Hands(
                max_num_hands=1, min_detection_confidence=0.6, min_tracking_confidence=0.5)
            self.mode = "solutions"
        else:
            from mediapipe.tasks import python as mpp
            from mediapipe.tasks.python import vision
            model = Path(__file__).parent / "models" / "hand_landmarker.task"
            if not model.exists():
                raise RuntimeError(
                    f"Download hand_landmarker.task into {model.parent}")
            opts = vision.HandLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=str(model)),
                num_hands=1, min_hand_detection_confidence=0.6, min_tracking_confidence=0.5,
                running_mode=vision.RunningMode.VIDEO)
            self.hands = vision.HandLandmarker.create_from_options(opts)
            self.mode = "tasks"
            self._ts = 0

    def process(self, rgb):
        """Returns list of 21 (x, y) or None."""
        if self.mode == "solutions":
            res = self.hands.process(rgb)
            if res.multi_hand_landmarks:
                return [(p.x, p.y) for p in res.multi_hand_landmarks[0].landmark]
            return None
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        self._ts += 33
        res = self.hands.detect_for_video(img, self._ts)
        if res.hand_landmarks:
            return [(p.x, p.y) for p in res.hand_landmarks[0]]
        return None


CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
               (10, 11), (11, 12), (9, 13), (13, 14), (14,
                                                       15), (15, 16), (13, 17), (17, 18),
               (18, 19), (19, 20), (0, 17)]


def main() -> None:
    import cv2

    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://127.0.0.1:8000/ws/input")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--no-window", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    ws = WSClient(args.server)
    ws.start()
    # DirectShow opens webcams in ~1 s on Windows (the default MSMF backend can take 10+ s)
    import sys
    cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        cap = cv2.VideoCapture(args.camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    tracker = HandTracker()

    calib = {"open": 1.8, "closed": 0.8, "v_ref": 3.0}
    detector = SqueezeDetector(calib["v_ref"])
    calibrator: Calibrator | None = Calibrator()
    calib_start = time.monotonic()
    calib_msg = ""
    calibrating = True
    ws.send({"type": "calibration_state", "calibrating": True})

    ema_o = None
    raw_hist: deque[float] = deque(maxlen=3)
    prev_o, prev_t = None, None
    last_hand_t = 0.0
    last_effort = None
    flash_until = 0.0
    fps_t, fps_n, fps = time.monotonic(), 0, 0.0

    def start_calibration():
        nonlocal calibrator, calib_start, calibrating, calib_msg
        calibrator, calib_start, calibrating, calib_msg = Calibrator(), time.monotonic(), True, ""

    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        frame = cv2.flip(frame, 1)
        t = now_ms()
        ts = time.monotonic()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        lm = tracker.process(rgb)

        # server-initiated control
        while not ws.inbox.empty():
            m = ws.inbox.get()
            if m.get("type") == "control" and m.get("action") == "calibrate":
                start_calibration()

        o = None
        if lm is not None:
            last_hand_t = ts
            raw = raw_openness(lm)
            raw_hist.append(raw)
            raw_med = statistics.median(raw_hist)
            if calibrating and calibrator is not None:
                calibrator.add(ts, raw)
                if ts - calib_start >= CALIB_SECONDS:
                    res = calibrator.result()
                    if res is None:
                        calib_msg = "Try again: open wider / move closer"
                        start_calibration()
                    else:
                        calib = res
                        detector = SqueezeDetector(res["v_ref"])
                        ws.send({"type": "calibration", **res})
                        calibrating = False
                        calibrator = None
                        calib_msg = ""
                        ws.send({"type": "calibration_state",
                                "calibrating": False})
            span = max(calib["open"] - calib["closed"], 1e-6)
            o_raw = float(np.clip((raw_med - calib["closed"]) / span, 0, 1))
            ema_o = o_raw if ema_o is None else 0.5 * o_raw + 0.5 * ema_o
            o = ema_o
            v = 0.0
            if prev_o is not None and ts > prev_t:
                v = (o_raw - prev_o) / (ts - prev_t)
            prev_o, prev_t = o_raw, ts
            if not calibrating:
                for kind, payload in detector.update(t, o_raw, v):
                    if kind == "onset":
                        ws.send({"type": "attempt_onset",
                                "t_ms": payload, "source": "webcam"})
                    else:
                        ws.send(payload)
                        last_effort = payload["effort"]
                        flash_until = ts + 0.6
        else:
            prev_o = prev_t = None
            if detector.state == "ONSET":
                detector.state = "IDLE"  # lost hand mid-squeeze: send nothing

        if not args.no_window:
            h, w = frame.shape[:2]
            if lm is not None:
                pts = [(int(x * w), int(y * h)) for x, y in lm]
                for a, b in CONNECTIONS:
                    cv2.line(frame, pts[a], pts[b], (255, 200, 0), 2)
                for p in pts:
                    cv2.circle(frame, p, 4, (0, 255, 255), -1)
            if calibrating:
                state_txt = "CALIBRATING"
                left = max(0, CALIB_SECONDS - (ts - calib_start))
                cv2.putText(frame, "OPEN and CLOSE your hand 3 times", (20, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                cv2.putText(frame, f"{left:0.0f}s", (w - 100, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 200, 255), 3)
                if calib_msg:
                    cv2.putText(frame, calib_msg, (20, 100),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            elif ts - last_hand_t > NO_HAND_S:
                state_txt = "Show your hand"
            elif ts < flash_until or detector.state == "ONSET":
                state_txt = "SQUEEZE!"
            else:
                state_txt = "READY"
            cv2.putText(frame, state_txt, (20, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
            # openness bar
            bar = int((o if o is not None else 0) * 300)
            cv2.rectangle(frame, (w - 50, h - 320),
                          (w - 20, h - 20), (80, 80, 80), 2)
            cv2.rectangle(frame, (w - 50, h - 20 - bar),
                          (w - 20, h - 20), (0, 255, 0), -1)
            if last_effort is not None:
                cv2.putText(frame, f"{int(last_effort * 100)}%", (20, 140),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.4, (0, 255, 255), 5)
            fps_n += 1
            if ts - fps_t > 1:
                fps, fps_n, fps_t = fps_n / (ts - fps_t), 0, ts
            link = "ws OK" if ws.connected else "ws down"
            cv2.putText(frame, f"{fps:0.0f} FPS  {link}", (w - 220, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (200, 200, 200), 1)
            cv2.imshow("NeuroNest hands", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q")):
                break
            if key in (ord("c"), ord("C")):
                start_calibration()
            if key == 32:  # SPACE manual attempt
                ws.send({"type": "attempt", "t_ms": now_ms() - 400, "effort": 0.7, "depth": 0.7,
                         "speed": 0.7, "hold_ms": 200, "source": "keyboard"})
        else:
            time.sleep(0.001)

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
