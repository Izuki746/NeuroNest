"""Builds the /api/device payload (SHARED_CONTEXT §7.5, brief §9)."""
from __future__ import annotations

import time

RED, AMBER, GREEN = [255, 40, 40], [255, 160, 0], [40, 220, 80]
WHITE, BLUE = [200, 200, 200], [40, 80, 255]


def lcd(text: str) -> str:
    """Exactly 16 ASCII chars."""
    t = text.encode("ascii", "replace").decode("ascii")
    return t[:16].ljust(16)


def arm_colour(arm: float) -> list[int]:
    return list(RED if arm < 0.35 else AMBER if arm < 0.7 else GREEN)


class ServoAnimator:
    """On each attempt: rise to target, hold 300 ms, decay to 0 over ~800 ms."""
    RISE_MS, HOLD_MS, DECAY_MS = 150.0, 300.0, 800.0

    def __init__(self) -> None:
        self.target = 0
        self.t0 = -1e12

    def trigger(self, arm_output: float, t_ms: float) -> None:
        self.target = round(90 * min(max(arm_output, 0.0), 1.0))
        self.t0 = t_ms

    def angle(self, t_ms: float) -> int:
        dt = t_ms - self.t0
        if dt < 0:
            return 0
        if dt < self.RISE_MS:
            return round(self.target * dt / self.RISE_MS)
        dt -= self.RISE_MS
        if dt < self.HOLD_MS:
            return self.target
        dt -= self.HOLD_MS
        if dt < self.DECAY_MS:
            return round(self.target * (1 - dt / self.DECAY_MS))
        return 0


class DeviceBuilder:
    def __init__(self) -> None:
        self.seq = 0
        self._last_key = None
        self.payload: dict = {"seq": 0, "leds": "0" * 104, "lcd1": lcd("NEURONEST"), "lcd2": lcd("STROKE SIM READY"),
                              "rgb": list(BLUE), "servo": 0, "lcd3": lcd("DAY90 FORECAST"), "lcd4": lcd("--")}
        self.servo = ServoAnimator()

    def build(self, *, t_ms: float, leds: str, has_patient: bool, calibrating: bool, patient_intro_until: float,
              card: str, age: int, day: int, arm: float, effort_last: float | None, fatigue: bool,
              timing: dict | None, timing_until: float, fast_forward: bool, ff_day: int, forecast: dict | None) -> dict:
        armpct = int(round(arm * 100))
        if not has_patient:
            l1, l2, rgb = "NEURONEST", "STROKE SIM READY", list(BLUE)
        elif calibrating:
            l1, l2, rgb = "CALIBRATING...", "OPEN+CLOSE x3", list(WHITE)
        elif t_ms < patient_intro_until:
            l1, l2, rgb = f"PATIENT {card}  {age}y", "STROKE: L MOTOR", arm_colour(
                arm)
        elif fast_forward:
            l1 = f">> DAY {ff_day}"
            l2 = f"FORECAST {int(round(forecast['p50'] * 100))}%" if forecast else "FAST FORWARD"
            rgb = arm_colour(arm)
        else:
            l1 = f"DAY {day}  ARM {armpct}%"
            rgb = arm_colour(arm)
            if timing and t_ms < timing_until:
                off = int(round(timing.get("offset_ms") or 0))
                verdict = timing.get("verdict")
                if verdict == "good":
                    l2 = "TIMING GOOD +"
                elif off < 0:
                    l2 = f"STIM {abs(off)}ms EARLY"
                else:
                    l2 = f"STIM {abs(off)}ms LATE"
            elif fatigue:
                l2 = "TIRED? BREATHE"
            elif effort_last is not None:
                l2 = f"EFFORT {int(round(effort_last * 100))}%"
            else:
                l2 = "SQUEEZE TO START"
        if forecast:
            spread = int(round((forecast["p90"] - forecast["p10"]) * 50))
            l3, l4 = "DAY90 FORECAST", f"{int(round(forecast['p50'] * 100))}% +/- {spread}"
        else:
            l3, l4 = "DAY90 FORECAST", "COLLECTING DATA"
        body = {"leds": leds, "lcd1": lcd(l1), "lcd2": lcd(l2), "rgb": rgb,
                "servo": int(self.servo.angle(t_ms)), "lcd3": lcd(l3), "lcd4": lcd(l4)}
        key = tuple((k, tuple(v) if isinstance(v, list) else v)
                    for k, v in body.items())
        if key != self._last_key:
            self._last_key = key
            self.seq += 1
        self.payload = {"seq": self.seq, **body}
        return self.payload


def now_ms() -> float:
    return time.monotonic() * 1000.0
