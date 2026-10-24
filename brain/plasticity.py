"""Rewiring rules for NeuroNest (SHARED_CONTEXT s3, PERSON_A brief s8).

Pieces, in the order they are used:
  1. eligibility_from_trajectory : spike-timing-style eligibility E[i, j] from one attempt's activity
  2. stim_gate                   : the TIME core -- stimulation timing gate + verdict
  3. update_weights              : W += eta_eff * E * S, clipped, existing edges only
  4. apply_attempt_plasticity    : 1-3 glued together (what Session.submit_attempt calls)
  5. spontaneous_recovery / use_it_or_lose_it : end-of-day updates

Conventions (match brain/connectome.py):
  * W[i, j] = connection from source j to target i (rows = targets).
  * S = boolean structural mask (W0 > 0). Rewiring never creates edges.
  * Time is in ms, days in days. No wall-clock time anywhere.
  * `patient` is duck-typed: needs .W, .W_pre, .k_ih, .traits, .day (see brain/patients.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy.signal import lfilter

# ----------------------------------------------------------------- constants
TAU_TRACE_MS = 20.0          # tau+ = tau- for the pre/post traces
BETA = 0.6                   # anti-causal LTD weight
# base learning rate (tune so standard rehab ~ +15-25 pp over 90 d, card B)
ETA0 = 0.02
# Keep activity stable while allowing plasticity above the pre-stroke baseline.
W_MIN_FRAC = 0.2
W_MAX_FRAC = 1.2

# gate = 1 + GATE_GAIN * stim_sens * gaussian(offset - best_offset)
GATE_GAIN = 2.5
GATE_WIDTH_MS = 90.0
VERDICT_TOL_MS = 60.0        # |offset - best_offset| <= this -> "good"
STIM_MAX_OFFSET_MS = 250.0   # stim later than this after onset is maladaptive
K_IH_LATE_FACTOR = 1.003     # maladaptive: wrong-side inhibition grows
# safety cap so repeated late stim cannot run away (see DECISIONS.md)
K_IH_CAP = 1.2

RECOVERY_RATE = 0.03         # spontaneous perilesional recovery per day
RECOVERY_DAYS = 30
USE_IT_MIN_ATTEMPTS = 5
USE_IT_DECAY = 0.996

# regions the arm readout / maladaptive rule touch
MOTOR_BASES = ("M1", "PMCDL", "PMCVL", "PMCM")
# "motor network" for use-it-or-lose-it
NETWORK_BASES = ("PFCDL", "PMCM", "PMCDL", "PMCVL", "M1", "S1")
EXCITE_TARGETS = ("PMCDL", "PMCVL", "PMCM", "M1", "S1")        # affected side
CALM_TARGETS = ("M1", "PMCDL")                                  # healthy side


# ----------------------------------------------------------------- helpers
def window_level(day: float, window_tau_days: float) -> float:
    """Recovery window: 1.0 on day 0, decays towards 0.25."""
    return 0.25 + 0.75 * float(np.exp(-day / window_tau_days))


def effort_factor(effort: float) -> float:
    return 0.2 + 0.8 * float(np.clip(effort, 0.0, 1.0))


def other_side(side: str) -> str:
    return "r" if side == "l" else "l"


def region_indices(idx: dict[str, int], side: str, bases: Sequence[str]) -> np.ndarray:
    """Indices of e.g. side='l', bases=('M1','PMCDL') -> [idx['lM1'], idx['lPMCDL']]."""
    return np.array([idx[side + b] for b in bases], dtype=int)


# ----------------------------------------------------------------- 1. eligibility
def eligibility_from_trajectory(
    R: np.ndarray,
    r_rest: np.ndarray | float,
    dt_ms: float = 1.0,
    tau_pre_ms: float = TAU_TRACE_MS,
    tau_post_ms: float = TAU_TRACE_MS,
    beta: float = BETA,
) -> np.ndarray:
    """Eligibility from an activity trajectory.

    R: [T, 76] (single patient) or [P, T, 76] (batched), full-resolution activity (not the 20 ms frames).
    r_rest: resting rate per region ([76] or scalar); subtracted (clipped >= 0) so resting noise doesn't learn.

    E_ij = sum_t ( r_i(t) x_j(t) - beta * r_j(t) y_i(t) ) * dt
    x = low-passed source trace, y = low-passed target trace (E[i, j]: rows = targets).
    A source firing shortly BEFORE its target gives E_ij > 0 (strengthen); the reverse gives E_ij < 0.
    Returns float32 with shape [76,76] or [P,76,76].
    """
    R = np.asarray(R, dtype=np.float64)
    single = R.ndim == 2
    if single:
        R = R[None]
    rc = np.clip(R - np.asarray(r_rest, dtype=np.float64), 0.0, None)

    def trace(tau_ms: float) -> np.ndarray:
        decay = float(np.exp(-dt_ms / tau_ms))
        return lfilter([dt_ms], [1.0, -decay], rc, axis=1)

    x = trace(tau_pre_ms)
    y = x if tau_post_ms == tau_pre_ms else trace(tau_post_ms)
    ltp = np.einsum("pti,ptj->pij", rc, x)    # r_i * x_j
    ltd = np.einsum("pti,ptj->pij", y, rc)    # y_i * r_j
    E = ((ltp - beta * ltd) * dt_ms).astype(np.float32)
    return E[0] if single else E


# ----------------------------------------------------------------- 2. stimulation timing gate
@dataclass(frozen=True)
class GateResult:
    gate: float                    # plasticity multiplier (>= 1)
    # "good" | "early" | "late" | "wrong_region" | None (no stim)
    verdict: Optional[str]
    # stim time minus attempt onset (negative = before)
    offset_ms: Optional[float]
    maladaptive: bool              # stim was too late -> strengthens wrong-side inhibition


def stim_gate(
    region: Optional[str],
    kind: Optional[str],
    offset_ms: Optional[float],
    traits: dict,
    affected_side: str = "l",
) -> GateResult:
    """Timing gate (PERSON_A brief 8.3 / 8.4). `traits` must hold stim_sens and best_offset_ms.

    The verdict uses the patient's TRUE best offset (live feedback only). Twins must never call this.
    """
    if region is None or kind is None or offset_ms is None:
        return GateResult(1.0, None, None, False)

    side, base = region[0], region[1:]
    if kind == "excite":
        valid = side == affected_side and base in EXCITE_TARGETS
    elif kind == "calm":
        valid = side == other_side(affected_side) and base in CALM_TARGETS
    else:
        valid = False
    if not valid:
        return GateResult(1.0, "wrong_region", float(offset_ms), False)

    best = float(traits["best_offset_ms"])
    if offset_ms > STIM_MAX_OFFSET_MS:
        gate, maladaptive = 1.0, True
    else:
        z = (offset_ms - best) / GATE_WIDTH_MS
        gate = 1.0 + GATE_GAIN * \
            float(traits["stim_sens"]) * float(np.exp(-0.5 * z * z))
        maladaptive = False

    if abs(offset_ms - best) <= VERDICT_TOL_MS:
        verdict = "good"
    elif offset_ms < best:
        verdict = "early"
    else:
        verdict = "late"
    return GateResult(gate, verdict, float(offset_ms), maladaptive)


def pick_nearest_stim(stims: Sequence[tuple], onset_ms: float) -> Optional[tuple]:
    """stims: (t_ms, region, kind, intensity) tuples. Returns the one closest to the onset, or None."""
    if not stims:
        return None
    return min(stims, key=lambda s: abs(s[0] - onset_ms))


# ----------------------------------------------------------------- 3. weight update
def update_weights(
    patient,
    E: np.ndarray,
    S: np.ndarray,
    effort: float,
    gate: float,
) -> float:
    """W <- clip(W + eta_eff * E * S, 0.2 W_pre, 3.0 W_pre). Mutates patient.W. Returns eta_eff."""
    tr = patient.traits
    eta_eff = (
        ETA0
        * float(tr["plasticity"])
        * window_level(patient.day, float(tr["window_tau_days"]))
        * effort_factor(effort)
        * float(gate)
    )
    dW = eta_eff * E * S                      # S masks to existing edges only
    patient.W = np.clip(
        patient.W + dW, W_MIN_FRAC * patient.W_pre, W_MAX_FRAC * patient.W_pre
    ).astype(np.float32)
    return eta_eff


def _apply_maladaptive(patient, idx: dict[str, int], affected_side: str) -> None:
    ids = region_indices(idx, affected_side, MOTOR_BASES)
    patient.k_ih[ids] = np.minimum(
        patient.k_ih[ids] * K_IH_LATE_FACTOR, K_IH_CAP)


# ----------------------------------------------------------------- 4. one attempt, end to end
@dataclass(frozen=True)
class PlasticityResult:
    gate: float
    verdict: Optional[str]
    offset_ms: Optional[float]
    eta_eff: float


def apply_attempt_plasticity(
    patient,
    S: np.ndarray,
    idx: dict[str, int],
    E: np.ndarray,
    effort: float,
    stims: Sequence[tuple] = (),
    onset_ms: float = 0.0,
    affected_side: str = "l",
) -> PlasticityResult:
    """Call once per attempt after simulate_attempt. Mutates patient.W (and patient.k_ih if stim was late)."""
    stim = pick_nearest_stim(stims, onset_ms)
    if stim is None:
        g = stim_gate(None, None, None, patient.traits, affected_side)
    else:
        t_ms, region, kind, _intensity = stim
        g = stim_gate(region, kind, t_ms - onset_ms,
                      patient.traits, affected_side)
    eta_eff = update_weights(patient, E, S, effort, g.gate)
    if g.maladaptive:
        _apply_maladaptive(patient, idx, affected_side)
    return PlasticityResult(g.gate, g.verdict, g.offset_ms, eta_eff)


# ----------------------------------------------------------------- 5. daily updates
def spontaneous_recovery(
    health: np.ndarray,
    health0: np.ndarray,
    health_max: np.ndarray,
    day: float,
    window_tau_days: float,
) -> np.ndarray:
    """h <- h + 0.03 * window(day) * (h_max - h) on lesioned regions, first ~30 days. Returns a new array."""
    h = np.array(health, dtype=np.float32, copy=True)
    if day > RECOVERY_DAYS:
        return h
    lesioned = np.asarray(health0) < 1.0
    gain = RECOVERY_RATE * window_level(day, window_tau_days)
    h[lesioned] += gain * (np.asarray(health_max)[lesioned] - h[lesioned])
    return np.minimum(h, health_max).astype(np.float32)


def use_it_or_lose_it(
    patient, idx: dict[str, int], attempts_today: int, affected_side: str = "l"
) -> bool:
    """If practice was low (<5 attempts), affected-side motor-network edges decay 0.4%. Returns True if applied."""
    if attempts_today >= USE_IT_MIN_ATTEMPTS:
        return False
    ids = region_indices(idx, affected_side, NETWORK_BASES)
    sub = np.ix_(ids, ids)
    W = np.array(patient.W, dtype=np.float32, copy=True)
    W[sub] *= USE_IT_DECAY
    patient.W = np.maximum(W, W_MIN_FRAC * patient.W_pre).astype(np.float32)
    return True


if __name__ == "__main__":
    # quick self-test: a source firing before its target should strengthen that edge
    T = 200
    R = np.full((T, 4), 0.05)
    R[20:40, 0] = 0.8      # region 0 fires first
    R[40:60, 1] = 0.8      # region 1 fires after
    E = eligibility_from_trajectory(R, 0.05)
    print("E[1,0] (0 -> 1, causal)     =", round(float(E[1, 0]), 3))
    print("E[0,1] (1 -> 0, anti-causal) =", round(float(E[0, 1]), 3))
    traits = {"stim_sens": 1.0, "best_offset_ms": -80.0}
    for off in (-200, -80, 0, 100, 300):
        g = stim_gate("lPMCDL", "excite", off, traits)
        print(
            f"offset {off:>5} ms -> gate {g.gate:.2f}  {g.verdict}  maladaptive={g.maladaptive}")
