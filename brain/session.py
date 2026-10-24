"""The real Session (SHARED_CONTEXT 7.3 contract; PERSON_A brief s9).

One Session = one simulated patient. It owns the hidden Patient, the stimulation buffer, the day counters,
the recovery curve and a TwinFilter. The server (C) calls it from an asyncio loop; long jobs (fast_forward,
planner) run via asyncio.to_thread, so every mutating method takes a re-entrant lock.

Day numbering (DECISIONS.md): Session.day is the 1-based day label (day 1 = first day of rehab).
patient.day is the number of COMPLETED days (0 on day 1) and is what the window/recovery maths use.
DayResult.day = the label of the day that just ended, so the first end_day() returns day=1.

Time: every t_ms is server monotonic ms. No wall-clock time anywhere.
"""
from __future__ import annotations

import copy
import logging
import threading
from typing import Optional

import numpy as np

from brain import plasticity as pl
from brain.types import AttemptResult, DayResult

log = logging.getLogger(__name__)

STIM_WINDOW_MS = 400.0        # stims from onset-400 to onset+400 count for an attempt
ATTEMPTS_PER_DAY = 20         # auto end_day after this many live attempts
MEASURE_EVERY = 5             # recompute the cheap arm_function every N live attempts
# representative attempts per simulated day in fast_forward. 1 gives the same day-90 outcomes as 3
# (card B standard: 0.71 both) at ~3x the speed (B, 2026-10-25, DECISIONS.md).
REP_ATTEMPTS = 1
TWIN_EVERY_DAYS = 10          # twins update cadence during fast_forward (each update ~1 s)
# twins simulate every particle through every day, so their cost dominates fast_forward; 40 keeps the
# forecast band usable while making a 90-day skip several times faster than 150 (B, DECISIONS.md)
TWIN_PARTICLES = 24
MAX_STIM_BUFFER = 200

STANDARD_PLAN = {"target": None, "sessions_per_day": 1,
                 "attempts_per_session": 20, "start_day": 1}
NO_REHAB_PLAN = {"target": None, "sessions_per_day": 0}


class _NullTwins:
    """Used if ai.twins is missing/broken so the live demo never crashes."""

    def update(self, day_result, **_observed) -> None:
        return None

    def forecast(self):
        return None


class Session:
    def __init__(self, card: str = "B", seed: int = 0, writer=None, model=None,
                 use_twins: bool = True, session_id: Optional[str] = None):
        from brain.model import get_model
        from brain.patients import make_card

        self.model = model or get_model()
        self.card, self.seed = card, seed
        self.pt = make_card(card, seed, self.model)
        self.idx = self.model.c.idx
        self.names = list(self.model.c.names)
        self.writer = writer
        self.session_id = session_id or f"{card}-{seed}"
        self.plan: Optional[dict] = None

        self._lock = threading.RLock()
        self._rng = np.random.default_rng(seed)
        # (t_ms, region, kind, intensity, actor)
        self._stims: list[tuple] = []
        self._reset_day_counters()
        self._since_measure = 0
        self._arm_fn = float(self.model.arm_function(self.pt))
        self._arm_out = 0.0
        self._timing: Optional[dict] = None
        self._t_last = 0.0
        self._r_rest: Optional[np.ndarray] = None
        self._forecast: Optional[dict] = None
        self.curve_days: list[int] = []
        self.curve_arm: list[float] = []
        self.history: list[DayResult] = []
        self.last_day_result: Optional[DayResult] = None
        # for twins/forecaster features (no hidden traits in here)
        self.attempt_log: list[dict] = []

        # idle animation: 3 slow sines per region
        r = np.random.default_rng(seed + 7919)
        self._idle_f = r.uniform(0.15, 0.6, (3, 76))
        self._idle_ph = r.uniform(0, 2 * np.pi, (3, 76))

        self.twins = self._make_twins() if use_twins else _NullTwins()

    # ------------------------------------------------------------------ setup helpers
    def _make_twins(self):
        try:
            from ai.twins import TwinFilter
            twins = TwinFilter(
                self.info(), n=TWIN_PARTICLES, seed=self.seed, model=self.model,
                W_pre=self.pt.W_pre)
            twins.update(DayResult(
                day=0, arm_function=self._arm_fn, mean_effort=0.0,
                attempts=0, window_level=1.0, stim_count=0))
            forecast = twins.forecast()
            self._forecast = {key: forecast[key] for key in (
                "p10", "p50", "p90", "n_alive")}
            return twins
        except Exception as e:  # noqa: BLE001 - never crash the demo for the AI layer
            log.warning(
                "TwinFilter unavailable (%s); running without twins", e)
            return _NullTwins()

    def _reset_day_counters(self) -> None:
        self.attempts_today = 0
        self._efforts_today: list[float] = []
        self._stim_count_today = 0

    @property
    def day(self) -> int:
        return int(self.pt.day) + 1

    def _log(self, table: str, row: dict) -> None:
        """Hook for Tiger. Adapt the method name to storage.tiger.TigerWriter once its API is known."""
        if self.writer is None:
            return
        try:
            self.writer.write(table, {"session_id": self.session_id, **row})
        except Exception as e:  # noqa: BLE001
            log.warning("writer.write(%s) failed: %s", table, e)

    # ------------------------------------------------------------------ public contract
    def info(self) -> dict:
        """Public patient info. Deliberately contains NO hidden traits (twins consume this)."""
        c = self.model.c
        return {
            "card": self.pt.card,
            "age": int(self.pt.age),
            "lesion": {k: float(v) for k, v in self.pt.lesion.items()},
            "affected_arm": "right" if self.pt.affected == "l" else "left",
            "regions": list(self.names),
            "centres": np.asarray(c.centres).tolist(),
        }

    def submit_stim(self, t_ms: float, region: str, kind: str, intensity: float = 1.0,
                    actor: str = "human") -> None:
        if kind not in ("excite", "calm"):
            log.warning("ignored stim with kind=%r", kind)
            return
        if region not in self.idx:
            log.warning("ignored stim with unknown region=%r", region)
            return
        with self._lock:
            self._stims.append(
                (float(t_ms), region, kind, float(intensity), actor))
            if len(self._stims) > MAX_STIM_BUFFER:
                self._stims = self._stims[-MAX_STIM_BUFFER:]

    def submit_attempt(self, t_ms: float, effort: float, depth: float = 0.0, speed: float = 0.0,
                       hold_ms: float = 0.0) -> AttemptResult:
        with self._lock:
            pt = self.pt
            effort = float(np.clip(effort, 0.0, 1.0))
            hi = t_ms + STIM_WINDOW_MS
            lo = t_ms - STIM_WINDOW_MS
            used = [s for s in self._stims if lo <= s[0] <= hi]
            # drop used + stale, keep future
            self._stims = [s for s in self._stims if s[0] > hi]
            rel = [(s[0] - t_ms, s[1], s[2], s[3])
                   for s in used]   # model wants onset-relative times
            abs4 = [(s[0], s[1], s[2], s[3])
                    for s in used]         # plasticity wants absolute times

            frames, arm_out, E = self.model.simulate_attempt(
                pt, effort, stims=rel, rng=self._rng, plasticity_on=True, noise=True)
            res = pl.apply_attempt_plasticity(
                pt, self.model.S, self.idx, E, effort, abs4, t_ms, pt.affected)

            self.attempts_today += 1
            self._efforts_today.append(effort)
            self._stim_count_today += len(used)
            self._since_measure += 1
            if self._since_measure >= MEASURE_EVERY:
                self._arm_fn = float(self.model.arm_function(pt))
                self._since_measure = 0
            self._arm_out = float(arm_out)
            self._t_last = float(t_ms)
            self._timing = ({"offset_ms": res.offset_ms, "verdict": res.verdict}
                            if res.verdict is not None else None)
            self._r_rest = None   # weights/inhibition changed

            result = AttemptResult(
                t_ms=float(t_ms), effort=effort,
                frames=np.asarray(frames, dtype=np.float32), frame_dt_ms=20.0,
                arm_output=float(arm_out), arm_function=float(self._arm_fn),
                stim_offset_ms=res.offset_ms, stim_verdict=res.verdict, gate=float(
                    res.gate),
                attempts_today=self.attempts_today, day=self.day,
                weight_change=(pt.W - pt.W_start).astype(np.float32))

            self.attempt_log.append({
                "day": self.day, "effort": effort, "depth": depth, "speed": speed, "hold_ms": hold_ms,
                "stim_offset_ms": res.offset_ms,
                "stim_region": used[0][1] if used else None, "stim_kind": used[0][2] if used else None})
            self._log("attempts", {
                "sim_day": self.day, "effort": effort, "depth": depth, "speed": speed, "hold_ms": hold_ms,
                "arm_output": float(arm_out), "arm_function": float(self._arm_fn),
                "stim_offset_ms": res.offset_ms, "stim_verdict": res.verdict, "gate": float(res.gate)})
            for s in used:
                self._log("stimulation", {"sim_day": self.day, "actor": s[4], "region": s[1], "kind": s[2],
                                          "intensity": s[3], "offset_ms": s[0] - t_ms})

            if self.attempts_today >= ATTEMPTS_PER_DAY:
                dr = self.end_day()
                # attempts_today / day stay as of this attempt
                result.arm_function = dr.arm_function
            return result

    def end_day(self) -> DayResult:
        with self._lock:
            dr = self._close_day(self.attempts_today,
                                 self._efforts_today, self._stim_count_today)
            day_attempts = [a for a in self.attempt_log if a["day"] == dr.day]
            self._notify_twins(dr, attempts=day_attempts)
            return dr

    def fast_forward(self, days: int, plan: Optional[dict] = None) -> list[DayResult]:
        """Simulate `days` days using REP_ATTEMPTS representative attempts per day, scaling the weight change
        by attempts/REP_ATTEMPTS. plan None = standard rehab. See SHARED_CONTEXT 7.4 for the schema."""
        with self._lock:
            p = dict(STANDARD_PLAN) if plan is None else {
                **STANDARD_PLAN, **plan}
            if plan is not None and "sessions_per_day" not in plan:
                p["sessions_per_day"] = STANDARD_PLAN["sessions_per_day"]
            if self.attempts_today > 0:
                self.end_day()          # finish the partial live day first
            pt = self.pt
            tr = pt.traits
            out: list[DayResult] = []
            for n in range(int(days)):
                label = self.day
                stim_on = p.get("target") is not None and label >= int(
                    p.get("start_day", 1))
                if label < int(p.get("start_day", 1)) and p["sessions_per_day"] > 0:
                    sessions, aps = STANDARD_PLAN["sessions_per_day"], STANDARD_PLAN["attempts_per_session"]
                else:
                    sessions = int(p["sessions_per_day"])
                    aps = int(p.get("attempts_per_session", 20))
                done = int(sum(self._rng.random() <
                           tr["adherence"] for _ in range(sessions)))
                attempts = done * aps
                efforts: list[float] = []
                if attempts > 0:
                    n_rep = min(REP_ATTEMPTS, attempts)
                    scale = attempts / n_rep
                    stims_rel = stims_abs = []
                    if stim_on:
                        off = float(p.get("offset_ms", 0.0))
                        item = (off, p["target"], p.get(
                            "kind", "excite"), float(p.get("intensity", 1.0)))
                        stims_rel, stims_abs = [item], [item]
                    for _ in range(n_rep):
                        eff = float(np.clip(self._rng.normal(
                            tr["effort_mean"], 0.1), 0.05, 1.0))
                        efforts.append(eff)
                        _, _, E = self.model.simulate_attempt(
                            pt, eff, stims=stims_rel, rng=self._rng, plasticity_on=True, noise=False)
                        self._scaled_plasticity(E, eff, stims_abs, scale)
                dr = self._close_day(
                    attempts, efforts, attempts if stim_on else 0)
                out.append(dr)
                if (n + 1) % TWIN_EVERY_DAYS == 0 or n == days - 1:
                    self._notify_twins(dr, plan=p)
            self._r_rest = None
            return out

    def state(self) -> dict:
        with self._lock:
            pt = self.pt
            return {
                "patient": {"card": pt.card, "age": int(pt.age),
                            "lesion": {k: float(v) for k, v in pt.lesion.items()},
                            "affected_arm": "right" if pt.affected == "l" else "left"},
                "day": self.day,
                "attempts_today": self.attempts_today,
                "arm_function": float(self._arm_fn),
                "arm_output": float(self._arm_out),
                "activity": self.idle_frame(self._t_last).astype(float).tolist(),
                "health": np.asarray(pt.health, dtype=float).tolist(),
                "timing": self._timing,
                "forecast": self._forecast,
                "curve": {"days": list(self.curve_days), "arm": list(self.curve_arm)},
            }

    def idle_frame(self, t_ms: float) -> np.ndarray:
        """[76] gentle resting activity for animating between attempts. Cheap."""
        if self._r_rest is None:
            pt = self.pt
            self._r_rest = np.asarray(
                self.model.steady_state(pt.W, pt.health, pt.k_ih, pt.b), dtype=np.float64)
        t = float(t_ms) / 1000.0
        wob = np.sin(2 * np.pi * self._idle_f * t +
                     self._idle_ph).sum(axis=0) / 3.0
        return np.clip(self._r_rest + 0.012 * wob, 0.0, 1.0).astype(np.float32)

    # ------------------------------------------------------------------ AI-mode helpers (not in the 7.3 contract)
    def set_plan(self, plan: Optional[dict]) -> None:
        with self._lock:
            self.plan = None if plan is None else dict(plan)
            if hasattr(self.twins, "set_plan"):
                self.twins.set_plan(self.plan)

    def planned_stim(self) -> Optional[dict]:
        p = self.plan
        if not p or p.get("target") is None:
            return None
        return {"region": p["target"], "kind": p.get("kind", "excite"),
                "offset_ms": float(p.get("offset_ms", 0.0)), "intensity": float(p.get("intensity", 1.0))}

    def clone(self, seed: Optional[int] = None) -> "Session":
        """Independent copy of the brain (same hidden patient state) for planner/twin what-if runs.
        No writer, no twins, fresh RNG."""
        with self._lock:
            c = Session.__new__(Session)
            c.model, c.idx, c.names = self.model, self.idx, self.names
            c.card, c.seed = self.card, self.seed if seed is None else seed
            c.pt = copy.deepcopy(self.pt)
            c.writer, c.session_id, c.plan = None, self.session_id + "-clone", None
            c._lock = threading.RLock()
            c._rng = np.random.default_rng(c.seed + 104729)
            c._stims = []
            c.attempts_today = self.attempts_today
            c._efforts_today = list(self._efforts_today)
            c._stim_count_today = self._stim_count_today
            c._since_measure = self._since_measure
            c._arm_fn, c._arm_out, c._timing = self._arm_fn, self._arm_out, None
            c._t_last, c._r_rest, c._forecast = self._t_last, None, None
            c.curve_days, c.curve_arm = list(
                self.curve_days), list(self.curve_arm)
            c.history, c.last_day_result = list(
                self.history), self.last_day_result
            c.attempt_log = list(self.attempt_log)
            c._idle_f, c._idle_ph = self._idle_f, self._idle_ph
            c.twins = _NullTwins()
            return c

    # ------------------------------------------------------------------ internals
    def _scaled_plasticity(self, E, effort: float, stims_abs: list, scale: float) -> None:
        """Apply one attempt's plasticity, multiplied by `scale` (representative attempt -> whole day)."""
        pt = self.pt
        W0, k0 = pt.W.copy(), np.array(pt.k_ih, dtype=np.float64, copy=True)
        pl.apply_attempt_plasticity(
            pt, self.model.S, self.idx, E, effort, stims_abs, 0.0, pt.affected)
        if scale != 1.0:
            dW = pt.W.astype(np.float64) - W0
            pt.W = np.clip(W0 + scale * dW, pl.W_MIN_FRAC * pt.W_pre, pl.W_MAX_FRAC * pt.W_pre
                           ).astype(np.float32)
            ratio = np.divide(np.asarray(pt.k_ih, dtype=np.float64),
                              k0, out=np.ones_like(k0), where=k0 > 0)
            pt.k_ih = np.minimum(k0 * ratio ** scale, max(pl.K_IH_CAP, float(k0.max()))).astype(
                np.asarray(pt.k_ih).dtype)

    def _close_day(self, attempts: int, efforts: list, stim_count: int) -> DayResult:
        pt = self.pt
        tau = float(pt.traits["window_tau_days"])
        label = self.day
        win = pl.window_level(pt.day, tau)
        pl.use_it_or_lose_it(pt, self.idx, attempts, pt.affected)
        pt.health = pl.spontaneous_recovery(
            pt.health, pt.health0, pt.health_max, pt.day, tau)
        pt.day += 1
        self._arm_fn = float(self.model.arm_function(pt))
        self._since_measure = 0
        self._r_rest = None
        dr = DayResult(day=label, arm_function=self._arm_fn,
                       mean_effort=float(np.mean(efforts)) if len(
                           efforts) else 0.0,
                       attempts=int(attempts), window_level=float(win), stim_count=int(stim_count))
        self.curve_days.append(label)
        self.curve_arm.append(self._arm_fn)
        self.history.append(dr)
        self.last_day_result = dr
        self._reset_day_counters()
        self._log("daily", {"sim_day": dr.day, "arm_function": dr.arm_function, "mean_effort": dr.mean_effort,
                            "attempts": dr.attempts, "window_level": dr.window_level,
                            "stim_count": dr.stim_count, "plan": self.plan})
        return dr

    def _notify_twins(self, dr: DayResult, *, attempts: list[dict] | None = None,
                      plan: Optional[dict] = None) -> None:
        try:
            self.twins.update(dr, attempts=attempts, plan=plan)
            f = self.twins.forecast()
            if f:
                self._forecast = {k: f[k] for k in (
                    "p10", "p50", "p90", "n_alive") if k in f}
                self._log("forecasts", {"sim_day": dr.day, **{k: f.get(k) for k in ("p10", "p50", "p90", "n_alive")},
                                        "method": "twins"})
        except Exception as e:  # noqa: BLE001
            log.warning("twins update failed: %s", e)


if __name__ == "__main__":
    # Good vs bad stimulation timing over 7 live-like days (brief s14: good should give >= 1.5x improvement).
    import time

    logging.basicConfig(level=logging.INFO)

    def run(offset_ms: float | None, label: str) -> float:
        s = Session("B", seed=0, use_twins=False)
        base = s._arm_fn
        t = 1_000.0
        t0 = time.perf_counter()
        for _ in range(7 * ATTEMPTS_PER_DAY):
            if offset_ms is not None:
                s.submit_stim(t + offset_ms, "lPMCDL", "excite")
            s.submit_attempt(t, 0.8, 0.9, 0.7, 300.0)
            t += 3_000.0
        gain = s._arm_fn - base
        print(f"{label:>14}: arm {base:.3f} -> {s._arm_fn:.3f}  (+{gain:.3f})  {time.perf_counter() - t0:.1f}s")
        return gain

    g_none = run(None, "no stim")
    g_good = run(-80.0, "good (-80 ms)")
    g_bad = run(300.0, "bad (+300 ms)")
    print(
        f"good/bad improvement ratio: {g_good / g_bad if g_bad > 1e-9 else float('inf'):.2f}")

    s = Session("B", seed=0, use_twins=False)
    t0 = time.perf_counter()
    res = s.fast_forward(90)
    print(
        f"fast_forward(90): {time.perf_counter() - t0:.1f}s  day90 arm={res[-1].arm_function:.3f}")
