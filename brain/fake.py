"""FakeSession: same API as the real Session (SHARED_CONTEXT 7.3) with plausible fake data.
C builds against this until A's real brain/session.py lands. Also exports FakeTwinFilter
and plan_for (re-exported by ai/twins.py and ai/planner.py until the real ones exist)."""
from __future__ import annotations
import logging
import math
import time
import numpy as np
from brain.types import AttemptResult, DayResult
from brain.connectome import load_names_centres
from brain.layout import led_layout, led_string  # noqa: F401  (re-export)

log = logging.getLogger(__name__)

FRAME_DT_MS, N_FRAMES, ATTEMPTS_PER_DAY, RESOLVE_MS = 20.0, 35, 20, 400.0
GAIN = 0.0004  # fake learning rate per attempt (the real model replaces this)
VALID = {"excite": {"lPMCDL", "lPMCVL", "lPMCM",
                    "lM1", "lS1"}, "calm": {"rM1", "rPMCDL"}}
CARDS = {
    "A": dict(age=74, lesion={"lM1": 0.30, "lPMCDL": 0.80}, arm0=0.30, rate=0.6, best=-40.0, tau=22.0),
    "B": dict(age=45, lesion={"lM1": 0.50}, arm0=0.45, rate=1.3, best=-80.0, tau=36.0),
}


def _window(day: float, tau: float) -> float:
    return 0.25 + 0.75 * math.exp(-day / tau)


def _gate(plan: dict, best: float) -> float:
    t, k = plan.get("target"), plan.get("kind", "excite")
    if t is None or t not in VALID.get(k, ()):
        return 1.0
    off = plan.get("offset_ms", 0.0)
    if off > 250:
        return 1.0
    return 1.0 + 2.0 * plan.get("intensity", 1.0) * math.exp(-0.5 * ((off - best) / 90.0) ** 2)


def _n_attempts(plan: dict | None, day: int) -> int:
    if plan is None:
        return ATTEMPTS_PER_DAY
    s = plan.get("sessions_per_day", 1)
    if s == 0:
        return 0
    if day < plan.get("start_day", 1):
        return ATTEMPTS_PER_DAY
    return s * plan.get("attempts_per_session", 20)


def _roll(arm: float, day: int, ndays: int, plan: dict | None, cfg: dict, effort: float = 0.7):
    """Deterministic fake recovery: list of arm_function for days day..day+ndays-1."""
    out = []
    for d in range(day, day + ndays):
        n = _n_attempts(plan, d)
        g = _gate(plan, cfg["best"]) if (
            plan and d >= plan.get("start_day", 1)) else 1.0
        if n >= 5:
            arm += n * GAIN * \
                cfg["rate"] * _window(d, cfg["tau"]) * \
                (0.2 + 0.8 * effort) * g * (1 - arm / 0.95)
        else:
            arm -= 0.0012
        out.append(float(np.clip(arm, 0.0, 1.2)))
    return out


class FakeTwinFilter:
    def __init__(self, session_info: dict | None = None, n: int = 150, seed: int = 0):
        self.n, self.day, self.arm = n, 0, 0.4

    def update(self, day_result: DayResult) -> None:
        self.day, self.arm = day_result.day, day_result.arm_function

    def forecast(self) -> dict:
        d = max(self.day, 1)
        half = 0.20 * math.exp(-d / 8.0) + 0.03
        p50 = float(min(1.1, self.arm + (0.85 - self.arm) * 0.55))
        days = list(range(d, 91))
        span = max(90 - d, 1)
        f = [(1 - math.exp(-(x - d) / 30)) / (1 - math.exp(-span / 30))
             for x in days]
        c50 = [self.arm + (p50 - self.arm) * v for v in f]
        w = [half * math.sqrt(max(x - d, 0) / span) for x in days]
        return {"day": d, "p10": p50 - half, "p50": p50, "p90": p50 + half,
                "curve": {"days": days, "p10": [a - b for a, b in zip(c50, w)], "p50": c50,
                          "p90": [a + b for a, b in zip(c50, w)]},
                "n_alive": max(5, int(self.n * math.exp(-d / 6.0)))}


class FakeSession:
    def __init__(self, card: str = "B", seed: int = 0, writer=None):
        self.card, self.seed, self.writer = card, seed, writer
        self.rng = np.random.default_rng(seed)
        self.names, self.centres = load_names_centres()
        self.idx = {n: i for i, n in enumerate(self.names)}
        cfg = CARDS.get(card)
        if cfg is None:  # "R": random patient
            cfg = dict(age=int(self.rng.integers(40, 86)), arm0=float(self.rng.uniform(0.2, 0.55)),
                       lesion={"lM1": float(self.rng.uniform(0.2, 0.7))},
                       rate=float(self.rng.lognormal(0, 0.3)), best=float(self.rng.uniform(-150, 0)),
                       tau=float(self.rng.uniform(20, 40)))
        self.cfg = cfg
        self.health = np.ones(76, np.float32)
        for n, h in cfg["lesion"].items():
            self.health[self.idx[n]] = h
        # diaschisis: left regions near lM1 are quieter
        dist = np.linalg.norm(
            self.centres - self.centres[self.idx["lM1"]], axis=1)
        self._dim = np.ones(76, np.float32)
        left = np.array([n.startswith("l") for n in self.names])
        self._dim[left] = 1 - 0.3 * np.exp(-dist[left] / 40.0)
        self._f = self.rng.uniform(0.05, 0.3, (3, 76))
        self._ph = self.rng.uniform(0, 2 * np.pi, (3, 76))
        self.arm_fn, self.arm_out = float(cfg["arm0"]), 0.0
        self.day, self.attempts_today = 1, 0
        self._stims, self._last_t, self._last = [], 0.0, None
        self._eff_today, self._stim_today = [], 0
        self._dW = np.zeros((76, 76), np.float32)
        self.curve = {"days": [], "arm": []}
        self.last_day_result: DayResult | None = None
        self.twins = FakeTwinFilter(self.info(), seed=seed)
        self.twins.update(DayResult(0, self.arm_fn, 0.0, 0, 1.0, 0))
        self._pairs = [(a, b) for a, b in [("lPMCDL", "lPFCDL"), ("lM1", "lPMCDL"), ("lM1", "lPMCM"),
                                           ("lM1", "lPMCVL"), ("lPMCM", "lPFCDL"), ("lPMCDL", "lPMCM")]
                       if a in self.idx and b in self.idx]

    # ---- API ----
    def info(self) -> dict:
        return {"card": self.card, "age": self.cfg["age"], "lesion": dict(self.cfg["lesion"]),
                "affected_arm": "right", "regions": list(self.names),
                "centres": self.centres.round(2).tolist()}

    def submit_stim(self, t_ms: float, region: str, kind: str, intensity: float = 1.0,
                    actor: str = "human") -> None:
        self._stims.append(dict(t=float(t_ms), region=region,
                           kind=kind, intensity=float(intensity), actor=actor))

    def submit_attempt(self, t_ms: float, effort: float, depth: float = 0.8, speed: float = 0.7,
                       hold_ms: float = 300.0) -> AttemptResult:
        effort = float(np.clip(effort, 0, 1))
        near = [s for s in self._stims if t_ms -
                RESOLVE_MS <= s["t"] <= t_ms + RESOLVE_MS]
        self._stims = [s for s in self._stims if s["t"]
                       > t_ms - RESOLVE_MS]  # keep future ones
        stim = min(near, key=lambda s: abs(s["t"] - t_ms)) if near else None
        offset = verdict = None
        gate = 1.0
        if stim:
            offset = stim["t"] - t_ms
            valid = stim["region"] in VALID.get(stim["kind"], ())
            best = self.cfg["best"]
            if not valid:
                verdict = "wrong_region"
            elif abs(offset - best) <= 60:
                verdict = "good"
            elif offset < best - 60:
                verdict = "early"
            else:
                verdict = "late"
            if valid and offset <= 250:
                gate = 1 + 2.0 * stim["intensity"] * \
                    math.exp(-0.5 * ((offset - best) / 90) ** 2)
            self._stim_today += 1

        frames = self._frames(effort, stim, offset)
        win = _window(self.day, self.cfg["tau"])
        self.arm_fn = float(np.clip(self.arm_fn + GAIN * 6 * self.cfg["rate"] * win * (0.2 + 0.8 * effort)
                                    * gate * (1 - self.arm_fn / 0.95), 0, 1.2))
        out = self.arm_fn * (0.35 + 0.65 * effort) * (1 +
                                                      0.1 * (gate - 1)) + 0.02 * self.rng.standard_normal()
        self.arm_out = float(np.clip(out, 0, 1.2))
        inc = 0.004 * effort * gate * win
        for a, b in self._pairs:
            self._dW[self.idx[a], self.idx[b]] += inc
        self._eff_today.append(effort)
        self.attempts_today += 1
        self._last_t = t_ms
        res = AttemptResult(t_ms, effort, frames, FRAME_DT_MS, self.arm_out, self.arm_fn, offset,
                            verdict, float(gate), self.attempts_today, self.day, self._dW.copy())
        self._last = res
        if self.attempts_today >= ATTEMPTS_PER_DAY:
            self.last_day_result = self.end_day()
        return res

    def end_day(self) -> DayResult:
        lesioned = self.health < 0.99
        win = _window(self.day, self.cfg["tau"])
        self.health[lesioned] += (0.01 * win *
                                  (1 - self.health[lesioned])).astype(np.float32)
        mean_e = float(np.mean(self._eff_today)) if self._eff_today else 0.0
        dr = DayResult(self.day, self.arm_fn, mean_e,
                       self.attempts_today, win, self._stim_today)
        self.curve["days"].append(self.day)
        self.curve["arm"].append(self.arm_fn)
        self.twins.update(dr)
        if self.writer is not None:
            try:
                self.writer.write_daily(dr)  # optional; C's tiger writer
            except Exception as e:  # never crash
                log.warning("writer failed: %s", e)
        self.day += 1
        self.attempts_today, self._eff_today, self._stim_today = 0, [], 0
        return dr

    def _resolve_plan(self, plan):
        if plan is None or plan == "standard":
            return None
        if plan == "none":
            return {"target": None, "sessions_per_day": 0}
        if plan == "ai":
            return self.best_plan()
        return plan

    def fast_forward(self, days: int, plan: dict | str | None = None) -> list[DayResult]:
        p = self._resolve_plan(plan)
        arms = _roll(self.arm_fn, self.day, days, p, self.cfg)
        out = []
        for a in arms:
            self.arm_fn = a
            n = _n_attempts(p, self.day)
            dr = DayResult(self.day, a, 0.7 if n else 0.0, n, _window(self.day, self.cfg["tau"]),
                           n if (p and p.get("target")) else 0)
            self.curve["days"].append(self.day)
            self.curve["arm"].append(a)
            if self.day % 5 == 0 or self.day == arms.__len__():
                self.twins.update(dr)
            out.append(dr)
            self.day += 1
        self.twins.update(out[-1]) if out else None
        self.attempts_today, self._eff_today, self._stim_today = 0, [], 0
        return out

    def best_plan(self) -> dict:
        if self.card == "A":
            return {"target": "rM1", "kind": "calm", "offset_ms": -40, "intensity": 1.0,
                    "sessions_per_day": 2, "attempts_per_session": 20, "start_day": 3}
        off = - \
            80 if self.card == "B" else int(round(self.cfg["best"] / 10) * 10)
        return {"target": "lPMCDL", "kind": "excite", "offset_ms": off, "intensity": 1.0,
                "sessions_per_day": 2, "attempts_per_session": 20, "start_day": 3}

    def planned_stim(self) -> dict:
        p = self.best_plan()
        return {"region": p["target"], "kind": p["kind"], "offset_ms": p["offset_ms"], "intensity": p["intensity"]}

    def idle_frame(self, t_ms: float) -> np.ndarray:
        s = np.sin(2 * np.pi * self._f *
                   (t_ms / 1000.0) + self._ph).mean(axis=0)
        return np.clip((0.06 + 0.025 * s) * self._dim * (0.5 + 0.5 * self.health), 0, 1).astype(np.float32)

    def state(self) -> dict:
        fc = self.twins.forecast()
        r = self._last
        return {
            "patient": {"card": self.card, "age": self.cfg["age"], "lesion": dict(self.cfg["lesion"]),
                        "affected_arm": "right"},
            "day": self.day, "attempts_today": self.attempts_today,
            "arm_function": self.arm_fn, "arm_output": self.arm_out,
            "activity": self.idle_frame(self._last_t).tolist(), "health": self.health.tolist(),
            "timing": ({"offset_ms": r.stim_offset_ms, "verdict": r.stim_verdict}
                       if r and r.stim_offset_ms is not None else None),
            "forecast": {k: fc[k] for k in ("p10", "p50", "p90", "n_alive")},
            "curve": {"days": list(self.curve["days"]), "arm": list(self.curve["arm"])},
        }

    # ---- internals ----
    def _frames(self, effort, stim, offset) -> np.ndarray:
        t = -250.0 + FRAME_DT_MS * np.arange(N_FRAMES)
        def b(tp, w): return np.exp(-0.5 * ((t - tp) / w) ** 2)  # noqa: E731
        i, h = self.idx, self.health
        drive = 0.25 + 0.75 * effort
        a = 0.05 + 0.008 * self.rng.standard_normal((N_FRAMES, 76))
        a += 0.04 * drive * b(120, 80)[:, None] * self.rng.random(76)[None, :]

        def add(name, amp, tp, w):
            if name in i:
                a[:, i[name]] += amp * b(tp, w)
        add("lPFCDL", 0.60 * drive * h[i["lPFCDL"]], 40, 45)
        add("lPMCM", 0.45 * drive * h[i["lPMCM"]], 80, 50)
        add("lPMCDL", 0.55 * drive * h[i["lPMCDL"]], 100, 50)
        add("lPMCVL", 0.40 * drive * h[i["lPMCVL"]], 110, 50)
        add("lM1", 0.80 * drive * min(1.2, self.arm_fn)
            * h[i["lM1"]] + 0.05, 170, 55)
        add("rM1", 0.25 * drive, 170, 60)
        if stim:
            sign = 0.3 if stim["kind"] == "excite" else -0.3
            if stim["region"] in i:
                on = offset if offset is not None else 0
                a[:, i[stim["region"]]] += sign * \
                    stim["intensity"] * ((t >= on) & (t < on + 150))
        a[:, :] *= self._dim[None, :]
        return np.clip(a, 0, 1).astype(np.float32)


def plan_for(session, budget_s: float = 10.0) -> dict:
    """Fake planner: static best plan + three consistent curves (same format as 7.3)."""
    t0 = time.monotonic()
    best = session.best_plan()
    cfg, day, arm = session.cfg, session.day, session.arm_fn
    days = list(range(day, 91))

    def curve(plan):
        p50 = _roll(arm, day, len(days), plan, cfg)
        w = [0.03 + 0.10 * math.sqrt((d - day) / max(90 - day, 1))
             for d in days]
        return {"days": days, "p50": p50, "p10": [a - b for a, b in zip(p50, w)],
                "p90": [a + b for a, b in zip(p50, w)]}
    ai, std, none = curve(best), curve(None), curve(
        {"target": None, "sessions_per_day": 0})
    verb = "Calm right M1" if best["kind"] == "calm" else "Excite left PMCDL"
    expl = (f"{verb}: stimulating {abs(best['offset_ms'])} ms before each movement is predicted to reach "
            f"{ai['p50'][-1]:.0%} vs {std['p50'][-1]:.0%} with standard rehab.")
    time.sleep(min(0.3, budget_s))
    log.info("fake plan_for took %.2fs", time.monotonic() - t0)
    return {"best": best, "explanation": expl, "curves": {"ai": ai, "standard": std, "none": none},
            "candidates_tested": 600}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for card in "AB":
        s = FakeSession(card, seed=1)
        info = s.info()
        print(
            f"\n== card {card}: age {info['age']} lesion {info['lesion']} arm {s.arm_fn:.2f}")
        t = 1000.0
        s.submit_stim(t - 80, "lPMCDL", "excite")
        r = s.submit_attempt(t, 0.8, 0.9, 0.7, 380)
        print("attempt:", r.frames.shape,
              f"out={r.arm_output:.2f} offset={r.stim_offset_ms} verdict={r.stim_verdict} gate={r.gate:.2f}")
        for k in range(1, 40):
            s.submit_attempt(t + 3000 * k, 0.8, 0.9, 0.7, 380)
        print(
            f"after 40 attempts: day {s.day} arm {s.arm_fn:.2f} twins band {s.twins.forecast()['p10']:.2f}-{s.twins.forecast()['p90']:.2f}")
        for p in (None, "none", "ai"):
            c = FakeSession(card, seed=1)
            c.fast_forward(90, p)
            print(f"  90d plan={p}: arm {c.arm_fn:.2f}")
        pf = plan_for(FakeSession(card, seed=1))
        print(" ", pf["explanation"])
        assert len(s.state()["activity"]) == 76 and len(
            led_string(s.idle_frame(0), s.health, "lM1", 0)) == 104
    print("\nOK")
