"""Rate model (SHARED_CONTEXT 3 / PERSON_A 6). dt = 1 ms, delays via 4 delay bins (fast), homolog
inhibition via exact ring-buffer delay. Rows = targets, cols = sources.

Window per attempt: t = -250..+450 ms (700 steps), onset t=0. Frames = every 20 ms (35 frames).
Pure functions of arrays: no hidden state except the cached calibrated model (get_model())."""
from __future__ import annotations
import logging
from dataclasses import dataclass, replace
from functools import lru_cache
import numpy as np
from brain import connectome as _cx
from brain.connectome import Connectome

log = logging.getLogger(__name__)

T_START, T_END = -250, 450
PULSE_MS, STIM_MS = 250, 150
READOUT = {"M1": 1.0, "PMCDL": 0.15, "PMCVL": 0.08,
           "PMCM": 0.12}  # spec: .35/.2/.25 (DECISIONS.md)
DRIVE = {"PFCDL": 1.0, "PMCM": 0.6}


@dataclass(frozen=True)
class Params:
    tau: float = 10.0
    G: float = 0.5            # background coupling, set by calibrate()
    # motor/planning-network coupling, set by calibrate() (DECISIONS.md)
    G_m: float = 3.0
    theta: float = 1.0
    kappa: float = 0.18
    r0: float = 0.05          # target resting rate
    k_base: float = 0.35
    sigma: float = 0.03
    amp: float = 1.0          # attempt-amplitude scale, set by calibrate()
    # attempt drive reaching the healthy hemisphere (DECISIONS.md)
    overflow: float = 1.0
    # slow adaptation strength (stops latching; DECISIONS.md)
    c_adapt: float = 1.5
    tau_adapt: float = 60.0
    # global inhibition: cancels this fraction of mean network excitation (DECISIONS.md)
    inh: float = 0.85
    stim_amp: float = 0.9
    beta: float = 0.6
    tau_trace: float = 20.0
    stride: int = 4           # eligibility sampled every 4 ms
    E_ref: float = 1.0        # eligibility normaliser, set by calibrate()


@dataclass
class Sim:
    frames: np.ndarray            # [35,76] float32
    full: np.ndarray | None       # [700,76] float32 (return_full only)
    M: np.ndarray                 # [700] motor readout trace
    peak_M: float
    r_rest: np.ndarray
    # [76,76] normalised eligibility (plasticity only)
    E: np.ndarray | None


def onset_latency_ms(trace: np.ndarray, rest: float, thresh: float = 0.10) -> float:
    """Arrival time (ms re onset) of activity: first time r - rest >= thresh (absolute)."""
    ex = trace - rest
    if ex.max() < thresh:
        return float("inf")
    return float(np.argmax(ex >= thresh) + T_START)


class BrainModel:
    def __init__(self, conn: Connectome, params: Params | None = None):
        self.c, self.p = conn, params or Params()
        c = self.c
        self.ar = np.arange(76)
        self.hom = c.homolog
        self.Dih = c.D[self.ar, self.hom].astype(int)
        self.dB = c.bin_delays.astype(int)
        self.masks = c.W_bin_masks.astype(np.float64)
        self.S = c.S
        self.Lb = int(max(c.D.max(), self.Dih.max())) + 2
        self.rowmean = float(c.W0.sum(axis=1).mean())
        mset = [c.idx[pre + n]
                for pre in "lr" for n in ("PFCDL", "PMCDL", "PMCVL", "PMCM", "M1", "S1")]
        flag = np.zeros(76)
        flag[mset] = 1.0
        same_hemi = (c.hemi[:, None] == c.hemi[None, :]).astype(float)
        # within-hemisphere only
        self.mmask = np.outer(flag, flag) * same_hemi

    def gmat(self) -> np.ndarray:
        """Per-edge coupling: G_m inside the motor/planning network, G elsewhere."""
        return self.p.G + (self.p.G_m - self.p.G) * self.mmask

    def gam(self) -> float:
        """Global (pool) inhibition strength: u_i -= gam * mean_j(out_j)."""
        return self.p.inh * self.p.G * self.rowmean

    # ---------- helpers ----------
    def sig(self, u):
        return 1.0 / (1.0 + np.exp(-np.clip((u - self.p.theta) / self.p.kappa, -60, 60)))

    def steady_state(self, W, h, k_ih, b, iters: int = 300) -> np.ndarray:
        p = self.p
        W = np.asarray(W, np.float64)
        r = np.full(76, p.r0)
        for _ in range(iters):
            out = h * r
            u = ((self.gmat() * W) @ out) - self.gam() * \
                out.mean() - p.c_adapt * r - k_ih * out[self.hom] + b
            r = 0.5 * r + 0.5 * self.sig(u)
        return r

    def baseline_drive(self, W, h, k_ih) -> np.ndarray:
        """b such that the (healthy) brain rests at r0."""
        p = self.p
        u_t = p.theta + p.kappa * np.log(p.r0 / (1 - p.r0))
        out = h * p.r0
        return u_t - ((self.gmat() * np.asarray(W, np.float64)) @ out - self.gam() * out.mean() - p.c_adapt * p.r0 - k_ih * out[self.hom])

    def _side(self, pre: str):
        i = self.c.idx
        m = [(i[pre + n], w) for n, w in READOUT.items()]
        return np.array([a for a, _ in m]), np.array([w for _, w in m])

    # ---------- core ----------
    def run(self, W, h, k_ih, b, effort: float = 1.0, stims=(), affected: str = "l", rng=None,
            noise: bool = True, plasticity: bool = False, r_rest=None, return_full: bool = False,
            attempt: bool = True, t_end: int = T_END) -> Sim:
        """stims: iterable of (t_rel_ms, region_name, kind, intensity); t_rel = stim time - onset."""
        p, c = self.p, self.c
        T = t_end - T_START
        h = np.asarray(h, np.float64)
        k_ih = np.asarray(k_ih, np.float64)
        b = np.asarray(b, np.float64)
        if r_rest is None:
            r_rest = self.steady_state(W, h, k_ih, b)
        I = np.zeros((T, 76))
        k0 = -T_START
        if attempt:
            A = p.amp * 1.6 * (0.25 + 0.75 * effort)
            pulse = np.sin(np.pi * np.arange(PULSE_MS) / PULSE_MS)
            healthy = "r" if affected == "l" else "l"
            for pre, scale in ((affected, 1.0), (healthy, p.overflow)):
                for n, w in DRIVE.items():
                    I[k0:k0 + PULSE_MS, c.idx[pre + n]] += A * w * scale * pulse
        for t_rel, region, kind, inten in stims:
            s = int(round(t_rel)) - T_START
            lo, hi = max(s, 0), min(s + STIM_MS, T)
            if hi > lo and region in c.idx:
                I[lo:hi, c.idx[region]
                  ] += (1.0 if kind == "excite" else -1.0) * p.stim_amp * inten
        if noise:
            rng = rng or np.random.default_rng(0)
            I += rng.standard_normal((T, 76)) * p.sigma
        I += b

        Gm = self.gmat()
        Wcat = np.concatenate(
            [Gm * np.asarray(W, np.float64) * m for m in self.masks], axis=1)
        dB, Lb, Dih, hom, ar = self.dB, self.Lb, self.Dih, self.hom, self.ar
        dtau = 1.0 / p.tau
        gam = self.gam()
        cad = p.c_adapt
        buf = np.tile(h * r_rest, (Lb, 1))
        r = r_rest.copy()
        a = r_rest.copy()
        da = 1.0 / p.tau_adapt
        R = np.empty((T, 76))
        if plasticity:
            x = np.zeros(76)
            y = np.zeros(76)
            E = np.zeros((76, 76))
            dec = np.exp(-p.stride / p.tau_trace)
        for k in range(T):
            d = buf[(k - dB) % Lb]
            u = Wcat @ d.ravel() - gam * \
                buf[(k - 1) % Lb].mean() - cad * a - \
                k_ih * buf[(k - Dih) % Lb, hom] + I[k]
            r = r + dtau * (self.sig(u) - r)
            a += da * (r - a)
            buf[k % Lb] = h * r
            R[k] = r
            if plasticity and k % p.stride == 0:
                rr = np.maximum(r - r_rest, 0.0)
                x = x * dec + rr * p.stride
                y = y * dec + rr * p.stride
                E += (np.outer(rr, x) - p.beta * np.outer(y, rr)) * p.stride
        mi, mc = self._side(affected)
        M = ((R[:, mi] - r_rest[mi]) * (mc * h[mi])).sum(axis=1)
        En = None
        if plasticity:
            En = (E * self.S / p.E_ref).astype(np.float32)
        return Sim(R[::20].astype(np.float32), R.astype(np.float32) if return_full else None,
                   M, float(max(M.max(), 0.0)), r_rest, En)

    # ---------- patient-level API ----------
    def simulate_attempt(self, pt, effort, stims=(), rng=None, plasticity_on=False, noise=True):
        """Spec 6.4: -> (frames [35,76], arm_output 0..1.2, eligibility or None)."""
        sim = self.run(pt.W, pt.health, pt.k_ih, pt.b, effort,
                       stims, pt.affected, rng, noise, plasticity_on)
        return sim.frames, float(np.clip(sim.peak_M / pt.M_base, 0, 1.2)), sim.E

    def arm_function(self, pt, W=None, health=None, k_ih=None) -> float:
        """Standard test: effort 1, no stim, no noise, no plasticity."""
        sim = self.run(pt.W if W is None else W, pt.health if health is None else health,
                       pt.k_ih if k_ih is None else k_ih, pt.b, 1.0, (), pt.affected, noise=False)
        return float(np.clip(sim.peak_M / pt.M_base, 0, 1.2))

    # ---------- calibration ----------
    def calibrate(self, target_peak: float = 0.68) -> "BrainModel":
        """Choose G (stability) and amp (so healthy lM1 peaks ~target) for THIS connectome."""
        c, p0 = self.c, self.p
        W = c.W0.astype(np.float64)
        # global inhibition removes the mean-field mode
        Meff = W - p0.inh * self.rowmean / 76.0
        rho = float(np.max(np.abs(np.linalg.eigvals(Meff))))
        h, k = np.ones(76), np.full(76, p0.k_base)
        i1 = c.idx["lM1"]
        cands = []
        # background loop gain 0.6 of max slope: stable everywhere
        G_b = 0.6 / (rho * 1.39)
        for mult in (4, 6, 8, 10, 13, 17, 22, 28, 35, 45, 60):
            self.p = replace(p0, G=G_b, G_m=G_b * mult)
            b = self.baseline_drive(W, h, k)

            def sim_at(amp):
                self.p = replace(self.p, amp=amp)
                return self.run(W, h, k, b, 1.0, noise=False, return_full=True)

            if sim_at(8.0).full[:, i1].max() < target_peak:
                continue
            lo, hi = 0.05, 8.0
            for _ in range(14):
                mid = 0.5 * (lo + hi)
                (lo, hi) = (mid, hi) if sim_at(
                    mid).full[:, i1].max() < target_peak else (lo, mid)
            sim = sim_at(hi)
            err = float(np.abs(sim.full[-1] - sim.r_rest).max())
            lat = [onset_latency_ms(
                sim.full[:, c.idx["l" + n]], sim.r_rest[c.idx["l" + n]]) for n in ("PFCDL", "PMCDL", "M1")]
            ok = err <= 0.05 and lat[0] < lat[1] < lat[2] and hi <= 4.0
            log.info("calibrate mult=%d G=%.4f G_m=%.3f amp=%.3f back_err=%.3f latencies=%s ok=%s",
                     mult, self.p.G, self.p.G_m, hi, err, lat, ok)
            cands.append((ok, err, self.p))
            if ok:
                break
        if not cands:
            log.warning("calibration could not reach target; using defaults")
            self.p = p0
            return self
        best = next((c_ for c_ in cands if c_[0]), min(
            cands, key=lambda c_: c_[1]))
        if not best[0]:
            log.warning(
                "calibration: no candidate fully satisfied rest/latency checks; using least-bad")
        self.p = best[2]
        # eligibility normaliser: 95th percentile of positive E over structural edges (healthy, effort 1)
        self.p = replace(self.p, E_ref=1.0)
        b = self.baseline_drive(W, h, k)
        E = self.run(W, h, k, b, 1.0, noise=False, plasticity=True).E
        self.p = replace(self.p, E_ref=float(
            np.percentile(E[E > 0], 95)) or 1.0)
        log.info("calibrated: G=%.4f G_m=%.3f amp=%.3f E_ref=%.1f",
                 self.p.G, self.p.G_m, self.p.amp, self.p.E_ref)
        return self


@lru_cache(maxsize=1)
def get_model() -> BrainModel:
    return BrainModel(_cx.load()).calibrate()


if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO)
    t0 = time.time()
    m = get_model()
    print(
        f"calibration {time.time()-t0:.1f}s  G={m.p.G:.3f} amp={m.p.amp:.2f}")
    from brain.patients import make_card
    for card in "AB":
        pt = make_card(card, 0)
        t0 = time.perf_counter()
        fr, out, _ = m.simulate_attempt(pt, 0.8)
        dt = (time.perf_counter() - t0) * 1000
        print(
            f"card {card}: arm_function={m.arm_function(pt):.2f} arm_output(e=.8)={out:.2f}  attempt={dt:.0f} ms")
