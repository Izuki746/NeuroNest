"""Batched engine + cohort simulator (PERSON_A brief 6.4 BatchEngine, s10 population, s11 twins).

BatchEngine  : the same rate model as brain.model.BrainModel.run, but for P patients at once
               (arrays [P,76], per-patient W [P,76,76]). dt = 2 ms by default (dt = 1 reproduces model.run exactly).
BatchCohort  : P patients' mutable state + the day loop (same rules as Session.fast_forward + plasticity.py),
               vectorised. Used by ai/population.py and ai/twins.py.

Everything is seeded through the np.random.Generator you pass in. No wall-clock time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from brain import plasticity as pl
from brain.model import DRIVE, PULSE_MS, READOUT, STIM_MS, BrainModel  # noqa: F401  (READOUT used via model._side)

log = logging.getLogger(__name__)

REP_ATTEMPTS = 3
STANDARD_PLAN = {"target": None, "sessions_per_day": 1,
                 "attempts_per_session": 20, "start_day": 1}


# ----------------------------------------------------------------------------- engine
@dataclass
class StimSpec:
    """One stimulation per patient (idx = -1 -> none). t_ms is relative to the attempt onset."""
    idx: np.ndarray        # [P] int region index or -1
    sign: np.ndarray       # [P] +1 excite / -1 calm
    intensity: np.ndarray  # [P]
    t_ms: np.ndarray       # [P]


@dataclass
class BatchSim:
    peak_M: np.ndarray            # [P]
    M: np.ndarray                 # [T, P] motor readout trace
    r_rest: np.ndarray            # [P, 76]
    # [P, 76, 76] normalised eligibility (plasticity only)
    E: Optional[np.ndarray]


class BatchEngine:
    def __init__(self, model: BrainModel, dt_ms: float = 2.0, t_start_ms: int = -200, t_end_ms: int = 450,
                 dtype=np.float32):
        self.m, self.dtype, self.dt = model, dtype, float(dt_ms)
        c, p = model.c, model.p
        self.T = int(round((t_end_ms - t_start_ms) / self.dt))
        # step index of attempt onset
        self.k0 = int(round(-t_start_ms / self.dt))
        self.t_start_ms = int(t_start_ms)
        # [4] delay steps per bin
        self.dB = np.maximum(1, np.round(model.dB / self.dt)).astype(int)
        self.Dih = np.maximum(1, np.round(
            model.Dih / self.dt)).astype(int)    # [76]
        self.Lb = int(max(self.dB.max(), self.Dih.max())) + 2
        self.hom = np.asarray(model.hom)
        self.Gm = model.gmat().astype(dtype)
        self.gam = float(model.gam())
        self.masks = np.asarray(model.masks).astype(
            dtype)                     # [4,76,76]
        self.n_pulse = int(round(PULSE_MS / self.dt))
        self.pulse = np.sin(
            np.pi * np.arange(self.n_pulse) / self.n_pulse).astype(dtype)
        self.n_stim = int(round(STIM_MS / self.dt))
        self.stride = max(1, int(round(p.stride / self.dt)))
        self.stride_ms = self.stride * self.dt
        self.dec = float(np.exp(-self.stride_ms / p.tau_trace))
        self.idx = c.idx

    # ------------------------------------------------------------- resting state
    def steady_state(self, W, h, k_ih, b, iters: int = 300, tol: float = 1e-6) -> np.ndarray:
        p, dt_ = self.m.p, self.dtype
        W = np.asarray(W, dt_)
        h, k_ih, b = (np.asarray(a, dt_) for a in (h, k_ih, b))
        GW = self.Gm[None] * W
        r = np.full(h.shape, p.r0, dt_)
        for it in range(iters):
            out = h * r
            u = (np.matmul(GW, out[:, :, None])[:, :, 0] - self.gam * out.mean(axis=1, keepdims=True)
                 - p.c_adapt * r - k_ih * out[:, self.hom] + b)
            r_new = (0.5 * r + 0.5 * self.m.sig(u)).astype(dt_)
            if it % 10 == 9 and float(np.abs(r_new - r).max()) < tol:
                return r_new
            r = r_new
        return r

    # ------------------------------------------------------------- one attempt for all patients
    def run(self, W, h, k_ih, b, effort, stim: Optional[StimSpec] = None, plasticity: bool = False,
            r_rest=None, noise: bool = False, rng=None, attempt: bool = True,
            affected: str = "l") -> BatchSim:
        m, p, c, dt_, dt = self.m, self.m.p, self.m.c, self.dtype, self.dt
        W = np.asarray(W, dt_)
        h, k_ih, b = (np.asarray(a, dt_) for a in (h, k_ih, b))
        effort = np.asarray(effort, dt_)
        P, T, k0, Lb = h.shape[0], self.T, self.k0, self.Lb
        if r_rest is None:
            r_rest = self.steady_state(W, h, k_ih, b)
        r_rest = np.asarray(r_rest, dt_)

        I = np.zeros((T, P, 76), dt_)
        if attempt:
            # [P]
            A = p.amp * 1.6 * (0.25 + 0.75 * effort)
            healthy = "r" if affected == "l" else "l"
            for pre, scale in ((affected, 1.0), (healthy, p.overflow)):
                for n, w in DRIVE.items():
                    I[k0:k0 + self.n_pulse, :, c.idx[pre + n]] += ((A * w * scale)[None, :]
                                                                   * self.pulse[:, None])
        if stim is not None:
            for q in np.nonzero(np.asarray(stim.idx) >= 0)[0]:
                s = int(round(float(stim.t_ms[q]) / dt)) + k0
                lo, hi = max(s, 0), min(s + self.n_stim, T)
                if hi > lo:
                    I[lo:hi, q, int(stim.idx[q])] += float(stim.sign[q]) * \
                        p.stim_amp * float(stim.intensity[q])
        if noise:
            rng = rng or np.random.default_rng(0)
            I += (rng.standard_normal(I.shape) *
                  (p.sigma * np.sqrt(dt))).astype(dt_)
        I += b[None]

        GW = self.Gm[None] * W
        Wcat = np.concatenate(
            [GW * mk for mk in self.masks], axis=2)       # [P,76,4*76]
        dB, Dih, hom = self.dB, self.Dih, self.hom
        ar_p = np.arange(P)[:, None]
        hom_row = hom[None, :]
        dtau, da, cad, gam = dt / p.tau, dt / p.tau_adapt, p.c_adapt, self.gam
        buf = np.broadcast_to((h * r_rest)[None], (Lb, P, 76)).copy()
        r, a = r_rest.copy(), r_rest.copy()
        mi, mc = m._side(affected)
        # [P,4]
        w_m = (mc[None, :] * h[:, mi]).astype(dt_)
        rest_m = r_rest[:, mi]
        M = np.empty((T, P), dt_)
        if plasticity:
            n_el = (T + self.stride - 1) // self.stride
            x = np.zeros((P, 76), dt_)
            # rate above rest, sampled every `stride` steps
            RR = np.empty((n_el, P, 76), dt_)
            # low-passed trace of the same
            XX = np.empty((n_el, P, 76), dt_)
            n_s = 0

        for k in range(T):
            d2 = buf[(k - dB) % Lb].transpose(1, 0,
                                              # [P,4*76] bin-major
                                              2).reshape(P, -1)
            u = np.matmul(Wcat, d2[:, :, None])[:, :, 0]
            u -= gam * buf[(k - 1) % Lb].mean(axis=1, keepdims=True)
            u -= cad * a
            u -= k_ih * buf[(k - Dih) % Lb, ar_p, hom_row]
            u += I[k]
            r += dtau * (m.sig(u) - r)
            a += da * (r - a)
            buf[k % Lb] = h * r
            M[k] = ((r[:, mi] - rest_m) * w_m).sum(axis=1)
            if plasticity and k % self.stride == 0:
                rr = np.maximum(r - r_rest, 0.0)
                x = x * self.dec + rr * self.stride_ms
                RR[n_s], XX[n_s] = rr, x
                n_s += 1

        En = None
        if plasticity:
            # E_ij = stride * sum_t (rr_i x_j - beta * x_i rr_j); the 2nd term is the transpose of the 1st
            A = np.matmul(RR[:n_s].transpose(1, 2, 0),
                          XX[:n_s].transpose(1, 0, 2))    # [P,76,76]
            E = self.stride_ms * (A - p.beta * A.transpose(0, 2, 1))
            En = (E * m.S[None] / p.E_ref).astype(np.float32)
        return BatchSim(np.maximum(M.max(axis=0), 0.0).astype(np.float64), M, r_rest, En)


# ----------------------------------------------------------------------------- cohort
TRAIT_KEYS = ("plasticity", "stim_sens", "best_offset_ms",
              "window_tau_days", "adherence", "effort_mean")


def _plan_arrays(model: BrainModel, plans: Sequence[Optional[dict]], traits: dict) -> dict:
    P = len(plans)
    idx = model.c.idx
    tgt = np.full(P, -1, int)
    sign = np.ones(P)
    inten = np.ones(P)
    off = np.zeros(P)
    sess = np.zeros(P, int)
    aps = np.zeros(P, int)
    start = np.ones(P, int)
    gate = np.ones(P)
    mal = np.zeros(P, bool)
    for q, plan in enumerate(plans):
        pp = dict(STANDARD_PLAN) if plan is None else {**STANDARD_PLAN, **plan}
        sess[q], aps[q], start[q] = int(pp["sessions_per_day"]), int(
            pp.get("attempts_per_session", 20)), int(pp.get("start_day", 1))
        t = pp.get("target")
        if t is not None and t in idx:
            kind = pp.get("kind", "excite")
            tgt[q], sign[q] = idx[t], (1.0 if kind == "excite" else -1.0)
            inten[q], off[q] = float(pp.get("intensity", 1.0)), float(
                pp.get("offset_ms", 0.0))
            g = pl.stim_gate(t, kind, off[q], {"stim_sens": traits["stim_sens"][q],
                                               "best_offset_ms": traits["best_offset_ms"][q]}, "l")
            gate[q], mal[q] = g.gate, g.maladaptive
    return dict(tgt=tgt, sign=sign, inten=inten, off=off, sess=sess, aps=aps, start=start, gate=gate, mal=mal)


class BatchCohort:
    """P patients' mutable state, advanced day by day with the same rules as Session.fast_forward."""

    def __init__(self, model: BrainModel, engine: BatchEngine, *, W, W_pre, health, health0, health_max,
                 k_ih, b, M_base, traits: dict, day=None):
        self.m, self.eng = model, engine
        self.W = np.asarray(W, np.float32).copy()
        self.W_pre = np.asarray(W_pre, np.float32)
        self.h = np.asarray(health, np.float64).copy()
        self.h0 = np.asarray(health0, np.float64)
        self.hmax = np.asarray(health_max, np.float64)
        self.k_ih = np.asarray(k_ih, np.float64).copy()
        self.b = np.asarray(b, np.float64)
        self.M_base = np.asarray(M_base, np.float64)
        self.traits = {k: np.asarray(traits[k], np.float64)
                       for k in TRAIT_KEYS}
        self.P = self.h.shape[0]
        self.day = np.zeros(
            self.P, int) if day is None else np.asarray(day, int).copy()
        self._motor = pl.region_indices(model.c.idx, "l", pl.MOTOR_BASES)
        self._net = pl.region_indices(model.c.idx, "l", pl.NETWORK_BASES)

    # ---- constructors
    @classmethod
    def from_patients(cls, model, patients: Sequence, engine: Optional[BatchEngine] = None) -> "BatchCohort":
        engine = engine or BatchEngine(model)
        def st(f): return np.stack([np.asarray(f(q)) for q in patients])  # noqa: E731
        return cls(model, engine, W=st(lambda q: q.W), W_pre=st(lambda q: q.W_pre),
                   health=st(lambda q: q.health), health0=st(lambda q: q.health0),
                   health_max=st(lambda q: q.health_max), k_ih=st(lambda q: q.k_ih), b=st(lambda q: q.b),
                   M_base=[q.M_base for q in patients],
                   traits={k: [q.traits[k] for q in patients]
                           for k in TRAIT_KEYS},
                   day=[q.day for q in patients])

    @classmethod
    def particles(cls, model, patient, traits_list: Sequence[dict], engine: Optional[BatchEngine] = None
                  ) -> "BatchCohort":
        """Digital-twin particles: the patient's KNOWN lesion/wiring (scan) with candidate hidden traits.
        Starts from the post-lesion state (W_start, health0), never from patient.W or patient.traits."""
        from brain.stroke import apply_overcomp
        engine = engine or BatchEngine(model)
        n, p, c = len(traits_list), model.p, model.c
        ones, k_base = np.ones(76), np.full(76, p.k_base)
        b_pre = model.baseline_drive(patient.W_pre, ones, k_base)
        bs, ks = [], []
        for tr in traits_list:
            b_i, k_i = apply_overcomp(c, b_pre, p.k_base, tr["overcomp"])
            bs.append(b_i)
            ks.append(k_i)

        def rep(a): return np.repeat(np.asarray(a)[None], n, axis=0)  # noqa: E731
        full = {k: [tr.get(k, 0.8 if k == "adherence" else 0.7)
                    for tr in traits_list] for k in TRAIT_KEYS}
        return cls(model, engine, W=rep(patient.W_start), W_pre=rep(patient.W_pre), health=rep(patient.health0),
                   health0=rep(patient.health0), health_max=rep(patient.health_max), k_ih=np.stack(ks),
                   b=np.stack(bs), M_base=np.full(n, patient.M_base), traits=full)

    # ---- measurement
    def arm_function(self, r_rest=None):
        """Standard test (effort 1, no stim, no noise, no plasticity) for every patient. -> (arm [P], r_rest)"""
        sim = self.eng.run(self.W, self.h, self.k_ih, self.b,
                           np.ones(self.P), r_rest=r_rest)
        return np.clip(sim.peak_M / self.M_base, 0.0, 1.2), sim.r_rest

    # ---- the day loop
    def run_days(self, days: int, plans=None, rng: Optional[np.random.Generator] = None,
                 efforts: Optional[np.ndarray] = None,
                 representative_attempts: int = REP_ATTEMPTS) -> dict:
        """Advance every patient `days` days. plans: None (standard rehab), one dict, or a list of P dicts.
        efforts: optional [P] to fix the practice effort (else ~ N(effort_mean, 0.1)).
        Returns arrays [P, days]: arm, mean_effort, attempts, stim_count; plus arm0 [P] (before day 1)."""
        rng = rng or np.random.default_rng(0)
        P, eng, tr = self.P, self.eng, self.traits
        if representative_attempts < 1:
            raise ValueError("representative_attempts must be at least 1")
        if plans is None or isinstance(plans, dict):
            plans = [plans] * P
        pa = _plan_arrays(self.m, plans, tr)
        arm0, rest = self.arm_function()
        out = {k: np.zeros((P, days)) for k in (
            "arm", "mean_effort", "attempts", "stim_count")}
        out["arm0"] = arm0
        for d in range(days):
            label = self.day + 1
            std_phase = (label < pa["start"]) & (pa["sess"] > 0)
            sess = np.where(
                std_phase, STANDARD_PLAN["sessions_per_day"], pa["sess"])
            aps = np.where(
                std_phase, STANDARD_PLAN["attempts_per_session"], pa["aps"])
            attempts = rng.binomial(sess, np.clip(tr["adherence"], 0, 1)) * aps
            stim_on = (pa["tgt"] >= 0) & (
                label >= pa["start"]) & (attempts > 0)
            n_rep = np.minimum(representative_attempts, attempts)
            scale = attempts / np.maximum(n_rep, 1)
            stim = StimSpec(
                np.where(stim_on, pa["tgt"], -1), pa["sign"], pa["inten"], pa["off"])
            eff_sum = np.zeros(P)
            for rep in range(representative_attempts):
                active = n_rep > rep
                if not active.any():
                    break
                eff = (np.clip(rng.normal(tr["effort_mean"], 0.1), 0.05, 1.0) if efforts is None
                       else np.asarray(efforts, float))
                sim = eng.run(self.W, self.h, self.k_ih, self.b,
                              eff, stim, plasticity=True, r_rest=rest)
                self._plasticity_update(sim.E, eff, active, scale, stim_on, pa)
                eff_sum += eff * active
            self._end_of_day(attempts)
            arm, rest = self.arm_function()
            out["arm"][:, d] = arm
            out["mean_effort"][:, d] = eff_sum / np.maximum(n_rep, 1)
            out["attempts"][:, d] = attempts
            out["stim_count"][:, d] = np.where(stim_on, attempts, 0)
        return out

    # ---- vectorised plasticity.py rules
    def _plasticity_update(self, E, effort, active, scale, stim_on, pa) -> None:
        tr = self.traits
        win = 0.25 + 0.75 * np.exp(-self.day / tr["window_tau_days"])
        gate = np.where(stim_on, pa["gate"], 1.0)
        eta = (pl.ETA0 * tr["plasticity"] * win * (0.2 + 0.8 * np.clip(effort, 0, 1)) * gate
               # [P]
               * scale * active)
        dW = (eta[:, None, None] * E * self.m.S[None]).astype(np.float32)
        self.W = np.clip(self.W + dW, pl.W_MIN_FRAC * self.W_pre,
                         pl.W_MAX_FRAC * self.W_pre).astype(np.float32)
        mal = stim_on & pa["mal"] & active
        if mal.any():
            q = np.nonzero(mal)[0]
            sub = self.k_ih[np.ix_(q, self._motor)]
            self.k_ih[np.ix_(q, self._motor)] = np.minimum(
                sub * pl.K_IH_LATE_FACTOR ** scale[q][:, None], np.maximum(pl.K_IH_CAP, sub))

    def _end_of_day(self, attempts: np.ndarray) -> None:
        tr = self.traits
        low = np.nonzero(attempts < pl.USE_IT_MIN_ATTEMPTS)[0]
        if low.size:
            ix = np.ix_(low, self._net, self._net)
            self.W[ix] = np.maximum(
                self.W[ix] * pl.USE_IT_DECAY, pl.W_MIN_FRAC * self.W_pre[ix])
        gain = pl.RECOVERY_RATE * \
            (0.25 + 0.75 * np.exp(-self.day / tr["window_tau_days"]))
        gain = np.where(self.day <= pl.RECOVERY_DAYS, gain, 0.0)
        les = self.h0 < 1.0
        self.h = np.minimum(
            self.h + gain[:, None] * (self.hmax - self.h) * les, self.hmax)
        self.day = self.day + 1


if __name__ == "__main__":
    import time
    from brain.model import get_model
    from brain.patients import sample_random_patient

    logging.basicConfig(level=logging.INFO)
    model = get_model()
    rng = np.random.default_rng(0)
    pats = [sample_random_patient(rng, model) for _ in range(20)]
    co = BatchCohort.from_patients(model, pats)
    arm_b, _ = co.arm_function()
    arm_s = np.array([model.arm_function(q) for q in pats])
    print("batch vs single arm_function  max|diff| =",
          float(np.abs(arm_b - arm_s).max()))

    big = BatchCohort.from_patients(
        model, [sample_random_patient(rng, model) for _ in range(200)])
    t0 = time.perf_counter()
    big.eng.run(big.W, big.h, big.k_ih, big.b,
                np.full(200, 0.8), plasticity=True)
    print(
        f"P=200 one attempt (plasticity on): {time.perf_counter() - t0:.2f}s  (target < 0.6)")
    t0 = time.perf_counter()
    res = big.run_days(5, None, rng)
    print(
        f"P=200 x 5 days: {time.perf_counter() - t0:.1f}s ; mean arm d5 = {res['arm'][:, -1].mean():.3f}")
