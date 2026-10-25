"""Inline fake brain Session + LED layout used until Person A's brain/ lands.

Implements the SHARED_CONTEXT §7.3 API with plausible, cheap data. Not science.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_BASE = ["PFCDL", "PFCVL", "PFCORB", "PFCM", "PMCDL", "PMCVL", "PMCM", "M1", "S1", "PCI", "PCS",
         "PCC", "ACC", "CCP", "IA", "TCI", "TCC", "TCS", "A1", "A2", "V1", "V2", "HC", "PHC", "TCPOL",
         "FEF", "CCR", "CCA", "AMYG", "PL", "RCA", "TCV", "GU", "SSA", "SSV", "PFCCL", "PFCPOL", "IN"]
REGIONS = ["l" + n for n in _BASE] + ["r" + n for n in _BASE]
IDX = {n: i for i, n in enumerate(REGIONS)}
MOTOR = ["lM1", "lPMCDL", "lPMCVL", "lPMCM"]


def _centres() -> np.ndarray:
    rng = np.random.default_rng(7)
    c = np.zeros((76, 3))
    for i, n in enumerate(_BASE):
        x = 60 - 120 * i / (len(_BASE) - 1) + \
            rng.normal(0, 4)  # front(+) .. back(-)
        y = 25 + rng.uniform(0, 30)
        z = rng.uniform(-30, 60)
        c[i] = (x, y, z)  # left (+y)
        c[i + 38] = (x, -y, z)  # right (-y)
    return c


CENTRES = _centres()


def led_layout() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for side, cols in (("l", list(range(5, -1, -1))), ("r", list(range(7, 13)))):
        names = [n for n in REGIONS if n[0] == side]
        names.sort(key=lambda n: -CENTRES[IDX[n], 0])
        free = {(r, c) for r in range(8) for c in cols}
        for k, n in enumerate(names):
            row = min(7, int(k * 8 / len(names)))
            y = abs(CENTRES[IDX[n], 1])
            want = 0.0 if False else y
            best = min(free, key=lambda rc: (
                abs(rc[0] - row) * 10 + abs((cols.index(rc[1])) - min(5, int(want / 10))), rc))
            free.discard(best)
            out[n] = best
    return out


_LAYOUT = led_layout()


def led_string(activity: np.ndarray, health: np.ndarray, stim_region: str | None, t_ms: float) -> str:
    cells = ["0"] * 104
    for r in range(8):
        cells[r * 13 + 6] = "1"
    for n, (r, c) in _LAYOUT.items():
        i = IDX[n]
        v = float(np.clip(activity[i], 0, 1)) * \
            (0.2 if health[i] < 0.5 else 1.0)
        lvl = round(9 * v)
        if health[i] < 0.5:
            lvl = min(lvl, 1)
        if stim_region == n:
            phase = 0.5 + 0.5 * np.sin(2 * np.pi * 1.0 * t_ms / 1000.0)  # 1 Hz
            lvl = round(5 + 4 * phase)
        cells[r * 13 + c] = str(int(lvl))
    return "".join(cells)


@dataclass
class AttemptResult:
    t_ms: float
    effort: float
    frames: np.ndarray
    frame_dt_ms: float
    arm_output: float
    arm_function: float
    stim_offset_ms: float | None
    stim_verdict: str | None
    gate: float
    attempts_today: int
    day: int
    weight_change: np.ndarray


@dataclass
class DayResult:
    day: int
    arm_function: float
    mean_effort: float
    attempts: int
    window_level: float
    stim_count: int


class _Twins:
    def __init__(self, s: "FakeSession") -> None:
        self.s = s
        self.n = 200

    def update(self, dr: DayResult) -> None:
        self.n = max(40, int(self.n * 0.93))

    def forecast(self) -> dict:
        f = self.s.arm
        spread = 0.25 * self.n / 200 + 0.04
        days = list(range(self.s.day, 91, 5))
        p50 = [min(1.1, f + (0.8 - f) * (1 - np.exp(-(d - self.s.day) / 25)))
               for d in days]
        return {"day": 90, "p10": max(0, p50[-1] - spread), "p50": p50[-1], "p90": p50[-1] + spread,
                "curve": {"days": days, "p10": [p - spread for p in p50], "p50": p50,
                          "p90": [p + spread for p in p50]}, "n_alive": self.n}


class FakeSession:
    CARDS = {"A": (74, {"lM1": 0.3, "lPMCDL": 0.8}), "B": (
        45, {"lM1": 0.5}), "R": (60, {"lM1": 0.4})}

    def __init__(self, card: str = "B", seed: int = 0) -> None:
        self.card = card if card in self.CARDS else "B"
        self.rng = np.random.default_rng(seed)
        self.age, self.lesion = self.CARDS[self.card]
        self.health = np.ones(76)
        for k, v in self.lesion.items():
            self.health[IDX[k]] = v
        self.arm = 0.15 + 0.3 * float(self.health[IDX["lM1"]])
        self.day, self.attempts_today = 1, 0
        self.efforts: list[float] = []
        self.stims: list[tuple[float, str, str]] = []
        self.W = np.zeros((76, 76), dtype=np.float32)
        self.twins = _Twins(self)
        self.last_arm_output = 0.0

    def info(self) -> dict:
        return {"card": self.card, "age": self.age, "lesion": dict(self.lesion), "affected_arm": "right",
                "regions": REGIONS, "centres": CENTRES.tolist()}

    def submit_stim(self, t_ms, region, kind, intensity=1.0, actor="human") -> None:
        self.stims.append((t_ms, region, kind))

    def submit_attempt(self, t_ms, effort, depth, speed, hold_ms) -> AttemptResult:
        near = [(s[0] - t_ms, s)
                for s in self.stims if abs(s[0] - t_ms) <= 400]
        off, verdict, gate = None, None, 1.0
        if near:
            off, s = min(near, key=lambda x: abs(x[0]))
            if s[1] not in ("lPMCDL", "lM1", "lPMCVL", "lPMCM", "lS1", "rM1", "rPMCDL"):
                verdict = "wrong_region"
            elif -120 <= off <= -20:
                verdict, gate = "good", 2.0
            elif off < -120:
                verdict, gate = "early", 1.2
            else:
                verdict, gate = "late", 0.8
        self.stims = [s for s in self.stims if s[0] > t_ms]
        base = self.arm * (0.5 + 0.7 * effort)
        arm_output = float(
            np.clip(base * (1.1 if verdict == "good" else 1.0), 0, 1.2))
        self.arm = float(np.clip(self.arm + 0.004 * gate * effort, 0, 1.2))
        self.attempts_today += 1
        self.efforts.append(effort)
        self.last_arm_output = arm_output
        F = 35
        frames = np.zeros((F, 76))
        for k, name in enumerate(["lPFCDL", "lPMCM", "lPMCDL", "lPMCVL", "lM1"]):
            i = IDX[name]
            t0 = 2 + k * 4
            tt = np.arange(F)
            frames[:, i] = effort * self.health[i] * \
                np.exp(-0.5 * ((tt - t0 - 4) / 3.0) ** 2)
        self.W[IDX["lPMCDL"], IDX["lM1"]] += 0.002 * gate * effort
        res = AttemptResult(t_ms, effort, frames.astype(np.float32), 20.0, arm_output, self.arm, off,
                            verdict, gate, self.attempts_today, self.day, self.W.copy())
        if self.attempts_today >= 20:
            self.end_day()
        return res

    def end_day(self) -> DayResult:
        dr = DayResult(self.day, self.arm, float(np.mean(self.efforts[-20:])) if self.efforts else 0.0,
                       self.attempts_today, float(np.exp(-self.day / 30)), 0)
        self.day += 1
        self.attempts_today = 0
        self.twins.update(dr)
        return dr

    def fast_forward(self, days: int, plan: dict | None = None) -> list[DayResult]:
        out = []
        boost = 1.8 if plan and plan.get("target") else (
            0.0 if plan and plan.get("sessions_per_day") == 0 else 1.0)
        for _ in range(days):
            self.arm = float(np.clip(self.arm + boost * 0.006 *
                             (0.9 - self.arm) * np.exp(-self.day / 50), 0, 1.2))
            self.efforts.append(0.7)
            self.attempts_today = 20
            out.append(self.end_day())
        return out

    def state(self) -> dict:
        return {"patient": {"card": self.card, "age": self.age, "lesion": dict(self.lesion),
                            "affected_arm": "right"},
                "day": self.day, "attempts_today": self.attempts_today,
                "arm_function": round(self.arm, 3), "arm_output": round(self.last_arm_output, 3),
                "health": self.health.round(3).tolist()}

    def idle_frame(self, t_ms: float) -> np.ndarray:
        ph = np.arange(76) * 0.7 + t_ms / 1500.0
        return (0.08 + 0.04 * np.sin(ph)) * self.health

    def planned_stim(self) -> dict:
        if self.card == "A":
            return {"target": "rM1", "kind": "calm", "offset_ms": -40, "intensity": 1.0}
        return {"target": "lPMCDL", "kind": "excite", "offset_ms": -80, "intensity": 1.0}


def plan_for(s: FakeSession, budget_s: float = 10.0) -> dict:
    best = s.planned_stim()
    days = list(range(s.day, 91, 3))

    def curve(boost):
        p50 = [float(min(1.1, s.arm + (0.45 + 0.4 * boost) *
                     (1 - np.exp(-(d - s.day) / 30)))) for d in days]
        return {"days": days, "p50": p50, "p10": [p - 0.08 for p in p50], "p90": [p + 0.08 for p in p50]}

    return {"best": {**best, "sessions_per_day": 2, "attempts_per_session": 20, "start_day": s.day},
            "explanation": f"Fallback fake planner: {best['kind']} {best['target']} {best['offset_ms']} ms.",
            "curves": {"ai": curve(0.9), "standard": curve(0.4), "none": curve(-0.3)},
            "candidates_tested": 0}
