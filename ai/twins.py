"""Particle-filter digital twins for the simulated stroke-recovery cohort."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np

from brain.batch import (
    BatchCohort,
    BatchEngine,
    REP_ATTEMPTS,
    StimSpec,
    _plan_arrays,
)
from brain.stroke import apply_lesion

log = logging.getLogger(__name__)

OBSERVATION_SD = 0.03
FORECAST_PARTICLES = 50
JITTER_SD = 0.05


class TwinFilter:
    """Infer a patient's hidden recovery traits from public state and observations.

    ``session_info`` must contain only scan-visible information. The optional
    wiring and baseline arguments are also observable simulator state; no
    Patient object or hidden trait dictionary is accepted.
    """

    def __init__(
        self,
        session_info: dict,
        n: int = 150,
        seed: int = 0,
        *,
        model=None,
        W_pre: np.ndarray | None = None,
    ):
        from brain.model import get_model

        self.info = dict(session_info)
        self.n = max(1, int(n))
        self.seed = int(seed)
        self.rng = np.random.default_rng(seed)
        self.model = model or get_model()
        self.affected = "l" if self.info.get("affected_arm", "right") == "right" else "r"
        self.lesion = dict(self.info.get("lesion", {}))
        self.age = int(self.info.get("age", 60))
        self.current_day = 0
        self._updated = False
        self._plan = None
        self._ess = float(self.n)

        c, p = self.model.c, self.model.p
        if W_pre is None:
            wiring_rng = np.random.default_rng(seed)
            W_pre = c.W0 * np.exp(wiring_rng.normal(0.0, 0.15, c.W0.shape))
        self.W_pre = np.asarray(W_pre, dtype=np.float32).copy()
        health0, health_max, W_start = apply_lesion(c, self.W_pre, self.lesion)
        ones = np.ones(76, dtype=np.float32)
        k_base = np.full(76, p.k_base, dtype=np.float32)
        b_pre = self.model.baseline_drive(self.W_pre, ones, k_base)
        M_base = float(self.model.run(
            self.W_pre, ones, k_base, b_pre, 1.0, noise=False).peak_M)

        # BatchCohort.particles needs only these scan-visible patient fields.
        known_state = SimpleNamespace(
            W_pre=self.W_pre,
            W_start=W_start,
            health0=health0,
            health_max=health_max,
            M_base=float(M_base),
            affected=self.affected,
        )
        self._known_state = known_state
        self.traits = self._sample_prior()
        self._overcomp = np.asarray(
            [particle["overcomp"] for particle in self.traits], dtype=np.float64)
        # Coarser twin-only steps keep three replay attempts under the 2 s budget.
        # 4 ms steps: 10 ms (= the rate time constant) made high-overcompensation twins collapse to ~4% while the
        # true patient recovers to ~45% (card A), so the planner rejected stimulation. (B, DECISIONS.md)
        twin_engine = BatchEngine(self.model, dt_ms=4.0)
        self.cohort = BatchCohort.particles(
            self.model, known_state, self.traits, engine=twin_engine)
        self.log_weights = np.full(self.n, -np.log(self.n), dtype=np.float64)
        self.weights = np.full(self.n, 1.0 / self.n, dtype=np.float64)
        self._particle_arm, self._particle_rest = self.cohort.arm_function()
        self._forecast_cache: dict | None = None

    def _sample_prior(self) -> list[dict[str, float]]:
        age_factor = 1.3 - 0.01 * (self.age - 40)
        samples = []
        for _ in range(self.n):
            samples.append({
                "plasticity": float(self.rng.lognormal(0.0, 0.3) * age_factor),
                "overcomp": float(self.rng.uniform(0.7, 1.8)),
                "stim_sens": float(self.rng.lognormal(0.0, 0.35)),
                "best_offset_ms": float(self.rng.uniform(-150.0, 0.0)),
                "window_tau_days": float(20.0 + 20.0 * self.rng.random() * age_factor),
                "adherence": float(self.rng.uniform(0.5, 1.0)),
                "effort_mean": float(self.rng.uniform(0.5, 0.95)),
            })
        return samples

    def set_plan(self, plan: dict | None) -> None:
        """Set the plan used for projections; ``None`` means standard rehab."""
        self._plan = None if plan is None else dict(plan)
        self._forecast_cache = None

    def posterior_samples(self, count: int = 64, seed: int | None = None) -> list[dict[str, float]]:
        """Draw hidden-trait candidates using the current posterior weights."""
        rng = self.rng if seed is None else np.random.default_rng(seed)
        indices = rng.choice(self.n, size=max(1, int(count)), p=self.weights)
        return [dict(self.traits[int(index)]) for index in indices]

    def update(self, day_result, *, attempts: list[dict] | None = None,
               plan: dict | None = None) -> None:
        """Assimilate an observed day, optionally with its visible attempt log.

        For batched fast-forward updates, omitted days are replayed with the
        supplied plan and the observed day's effort/attempt count.
        """
        day = int(day_result.day)
        if self._updated and day <= self.current_day:
            return

        if plan is not None:
            self._plan = dict(plan)
        if day > self.current_day:
            actual_plan = self._observed_plan(day_result, attempts, plan)
            n_days = day - self.current_day
            n_attempts = max(0, int(day_result.attempts))
            if n_attempts == 0:
                actual_plan["sessions_per_day"] = 0
                actual_plan["attempts_per_session"] = 0
            else:
                actual_plan["sessions_per_day"] = 1
                actual_plan["attempts_per_session"] = n_attempts
            actual_plan["start_day"] = 1
            effort = float(np.clip(day_result.mean_effort, 0.0, 1.0))
            self._advance_cohort(n_days, actual_plan, n_attempts, effort)
            self.current_day = day

        observed = float(np.clip(day_result.arm_function, 0.0, 1.2))
        residual = (observed - self._particle_arm) / OBSERVATION_SD
        self.log_weights += -0.5 * residual * residual
        self.log_weights -= np.max(self.log_weights)
        self.weights = np.exp(self.log_weights)
        total = float(self.weights.sum())
        if not np.isfinite(total) or total <= 0.0:
            log.warning("Twin likelihood collapsed; resetting particle weights")
            self.weights.fill(1.0 / self.n)
            self.log_weights.fill(-np.log(self.n))
        else:
            self.weights /= total
            self.log_weights = np.log(np.maximum(self.weights, np.finfo(float).tiny))

        self._updated = True
        ess = self._effective_sample_size()
        self._ess = ess
        if ess < self.n / 2:
            self._resample_and_jitter()
        self._forecast_cache = None

    def _observed_plan(self, day_result, attempts: list[dict] | None,
                       plan: dict | None) -> dict:
        if plan is not None:
            return {**(self._plan or {}), **plan}
        if attempts:
            stim_counts: dict[tuple[str, str], list[dict]] = {}
            for attempt in attempts:
                region = attempt.get("stim_region")
                kind = attempt.get("stim_kind")
                offset = attempt.get("stim_offset_ms")
                if region and kind and offset is not None:
                    stim_counts.setdefault((region, kind), []).append(attempt)
            if stim_counts:
                (region, kind), selected = max(stim_counts.items(), key=lambda item: len(item[1]))
                offsets = [float(a["stim_offset_ms"]) for a in selected]
                return {
                    "target": region,
                    "kind": kind,
                    "offset_ms": float(np.median(offsets)),
                    "intensity": 1.0,
                }
            return {"target": None}
        return dict(self._plan or {"target": None})

    def _effective_sample_size(self) -> float:
        return float(1.0 / np.square(self.weights).sum())

    def _advance_cohort(self, days: int, plan: dict, attempts: int,
                        effort: float) -> None:
        """Replay observations with the batched engine's representative attempts."""
        cohort = self.cohort
        p = _plan_arrays(self.model, [plan] * self.n, cohort.traits)
        effort_vector = np.full(self.n, effort, dtype=np.float32)
        attempt_vector = np.full(self.n, attempts, dtype=np.int32)
        n_rep = min(REP_ATTEMPTS, attempts)
        scale = attempt_vector.astype(np.float64) / max(n_rep, 1)
        for _ in range(days):
            label = cohort.day + 1
            stim_on = ((p["tgt"] >= 0) & (label >= p["start"]) &
                       (attempt_vector > 0))
            for _ in range(n_rep):
                stim = StimSpec(
                    idx=np.where(stim_on, p["tgt"], -1),
                    sign=p["sign"],
                    intensity=p["inten"],
                    t_ms=p["off"],
                )
                simulated = cohort.eng.run(
                    cohort.W, cohort.h, cohort.k_ih, cohort.b,
                    effort_vector, stim, plasticity=True,
                    r_rest=self._particle_rest,
                )
                cohort._plasticity_update(
                    simulated.E, effort_vector, np.ones(self.n, dtype=bool),
                    scale, stim_on, p)
            cohort._end_of_day(attempt_vector)
            self._particle_arm, self._particle_rest = cohort.arm_function()

    def _resample_and_jitter(self) -> None:
        positions = (self.rng.random() + np.arange(self.n)) / self.n
        indices = np.searchsorted(np.cumsum(self.weights), positions, side="right")
        indices = np.minimum(indices, self.n - 1)
        cohort = self.cohort
        for name in ("W", "h", "k_ih", "b", "day"):
            setattr(cohort, name, getattr(cohort, name)[indices].copy())
        cohort.W_pre = cohort.W_pre[indices].copy()
        cohort.h0 = cohort.h0[indices].copy()
        cohort.hmax = cohort.hmax[indices].copy()
        cohort.M_base = cohort.M_base[indices].copy()
        cohort.traits = {key: values[indices].copy()
                         for key, values in cohort.traits.items()}
        self._overcomp = self._overcomp[indices].copy()
        self._particle_arm = self._particle_arm[indices].copy()
        self._particle_rest = self._particle_rest[indices].copy()

        for key in ("plasticity", "stim_sens", "window_tau_days",
                    "adherence", "effort_mean"):
            cohort.traits[key] *= self.rng.lognormal(0.0, JITTER_SD, self.n)
        self._overcomp *= self.rng.lognormal(0.0, JITTER_SD, self.n)
        self._overcomp = np.clip(self._overcomp, 0.4, 2.2)
        cohort.traits["plasticity"] = np.clip(cohort.traits["plasticity"], 0.1, 3.0)
        cohort.traits["stim_sens"] = np.clip(cohort.traits["stim_sens"], 0.1, 3.0)
        cohort.traits["window_tau_days"] = np.clip(
            cohort.traits["window_tau_days"], 5.0, 80.0)
        cohort.traits["adherence"] = np.clip(cohort.traits["adherence"], 0.1, 1.0)
        cohort.traits["effort_mean"] = np.clip(cohort.traits["effort_mean"], 0.1, 1.0)
        cohort.traits["best_offset_ms"] = np.clip(
            cohort.traits["best_offset_ms"] + self.rng.normal(0.0, 7.5, self.n),
            -200.0, 100.0,
        )

        c, p = self.model.c, self.model.p
        b_pre = self.model.baseline_drive(
            self.W_pre, np.ones(76), np.full(76, p.k_base))
        healthy = "r" if self.affected == "l" else "l"
        cohort.k_ih = np.full((self.n, 76), p.k_base, dtype=np.float64)
        cohort.k_ih[:, c.hemi == (0 if self.affected == "l" else 1)] *= (
            self._overcomp[:, None])
        cohort.b = np.broadcast_to(b_pre, (self.n, 76)).copy()
        for name in ("M1", "PMCDL"):
            cohort.b[:, c.idx[healthy + name]] += (
                0.05 * (self._overcomp - 1.0))

        self.traits = [
            {**{key: float(cohort.traits[key][i]) for key in cohort.traits},
             "overcomp": float(self._overcomp[i])}
            for i in range(self.n)
        ]
        self.weights.fill(1.0 / self.n)
        self.log_weights.fill(-np.log(self.n))

    def _forecast_particles(self) -> tuple[list[int], np.ndarray]:
        count = min(self.n, FORECAST_PARTICLES)
        if count == self.n:
            selected = np.arange(self.n)
        else:
            selected = np.argpartition(self.weights, -count)[-count:]
        weights = self.weights[selected].astype(np.float64)
        weights /= weights.sum()
        return selected, weights

    @staticmethod
    def _weighted_quantile(values: np.ndarray, weights: np.ndarray,
                           quantile: float) -> float:
        order = np.argsort(values)
        sorted_values = values[order]
        sorted_weights = weights[order]
        midpoint_cdf = np.cumsum(sorted_weights) - 0.5 * sorted_weights
        return float(np.interp(quantile, midpoint_cdf, sorted_values,
                              left=sorted_values[0], right=sorted_values[-1]))

    def forecast(self) -> dict:
        """Return weighted recovery percentiles and a day-by-day forecast curve."""
        if self._forecast_cache is not None:
            return self._forecast_cache

        selected, weights = self._forecast_particles()
        days = list(range(self.current_day, 91))
        if not days:
            days = [90]
        arms = np.clip(self._particle_arm[selected].copy(), 0.0, 1.2)
        traits = self.cohort.traits
        plasticity = traits["plasticity"][selected]
        tau = traits["window_tau_days"][selected]
        effort = traits["effort_mean"][selected]
        overcomp = self._overcomp[selected]
        gate = np.ones(len(selected), dtype=np.float64)
        plan = self._plan or {"target": None, "sessions_per_day": 1}
        target = plan.get("target")
        kind = plan.get("kind", "excite")
        offset = float(plan.get("offset_ms", 0.0))
        if target is not None:
            from brain.plasticity import stim_gate
            for j, particle_index in enumerate(selected):
                result = stim_gate(
                    target, kind, offset,
                    {
                        "stim_sens": float(traits["stim_sens"][particle_index]),
                        "best_offset_ms": float(traits["best_offset_ms"][particle_index]),
                    },
                    self.affected,
                )
                gate[j] = result.gate

        p10, p50, p90 = [], [], []
        for day in days:
            if day > self.current_day:
                elapsed_day = day - 1
                window = 0.25 + 0.75 * np.exp(-elapsed_day / tau)
                sessions = int(plan.get("sessions_per_day", 1))
                if sessions <= 0:
                    arms *= 0.9995
                else:
                    gain = (0.0018 * plasticity * window *
                            (0.2 + 0.8 * effort) * gate * sessions)
                    gain *= 0.8 + 0.2 * np.clip(overcomp, 0.5, 2.0)
                    arms += gain * (1.0 - arms / 1.0)
                arms = np.clip(arms, 0.0, 1.2)
            p10.append(self._weighted_quantile(arms, weights, 0.1))
            p50.append(self._weighted_quantile(arms, weights, 0.5))
            p90.append(self._weighted_quantile(arms, weights, 0.9))

        self._forecast_cache = {
            "day": max(self.current_day, 1),
            "p10": p10[-1],
            "p50": p50[-1],
            "p90": p90[-1],
            "curve": {"days": days, "p10": p10, "p50": p50, "p90": p90},
            "n_alive": max(1, int(round(self._ess))),
        }
        return self._forecast_cache

    def simulate_plans(self, plans: list[dict], n: int = 20,
                       days: int = 90, seed: int = 0,
                       representative_attempts: int = REP_ATTEMPTS) -> list[dict]:
        """Verify plans on matched posterior particles using the batched brain."""
        if not plans:
            return []
        count = min(max(1, int(n)), self.n)
        rng = np.random.default_rng(seed)
        indices = rng.choice(self.n, size=count, replace=False, p=self.weights)
        cohort = self.cohort
        repeated = np.tile(indices, len(plans))
        traits = {key: values[repeated].copy()
                  for key, values in cohort.traits.items()}
        verification = BatchCohort(
            self.model,
            cohort.eng,
            W=cohort.W[repeated],
            W_pre=cohort.W_pre[repeated],
            health=cohort.h[repeated],
            health0=cohort.h0[repeated],
            health_max=cohort.hmax[repeated],
            k_ih=cohort.k_ih[repeated],
            b=cohort.b[repeated],
            M_base=cohort.M_base[repeated],
            traits=traits,
            day=cohort.day[repeated],
        )
        result = verification.run_days(
            max(0, min(int(days), 90 - self.current_day)),
            plans=[dict(plan) for plan in plans for _ in range(count)],
            rng=rng,
            representative_attempts=representative_attempts,
        )
        curves = []
        for plan_index in range(len(plans)):
            start = plan_index * count
            stop = start + count
            curves.append({
                "days": list(range(self.current_day + 1,
                                   self.current_day + 1 + result["arm"].shape[1])),
                "arm": result["arm"][start:stop].astype(float).tolist(),
                "arm0": result["arm0"][start:stop].astype(float).tolist(),
                "particle_traits": [
                    dict(self.traits[int(index)]) for index in indices
                ],
            })
        return curves
