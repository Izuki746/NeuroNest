"""Patient dataclass + cards A/B/R (SHARED_CONTEXT 3, PERSON_A 7)."""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from brain.stroke import apply_lesion, apply_overcomp

CARDS = {
    "A": dict(age=74, lesion={"lM1": 0.30, "lPMCDL": 0.80},
              traits=dict(plasticity=0.6, overcomp=1.6, stim_sens=0.7, best_offset_ms=-40.0,
                          window_tau_days=22.0, adherence=0.8, effort_mean=0.7)),
    "B": dict(age=45, lesion={"lM1": 0.50},
              traits=dict(plasticity=1.3, overcomp=0.8, stim_sens=1.3, best_offset_ms=-80.0,
                          window_tau_days=36.0, adherence=0.8, effort_mean=0.7)),
}
MOTOR_CANDIDATES = ["lPMCDL", "lPMCVL", "lPMCM", "lS1", "lPCS", "lS2"]


@dataclass
class Patient:
    card: str
    age: int
    seed: int
    lesion: dict
    health0: np.ndarray
    health_max: np.ndarray
    traits: dict                 # HIDDEN from the twins
    W: np.ndarray                # current weights (changes with plasticity)
    W_pre: np.ndarray            # pre-stroke weights
    # post-lesion starting weights (weight_change baseline)
    W_start: np.ndarray
    k_ih: np.ndarray
    b: np.ndarray
    M_base: float
    # current health (spontaneous recovery changes it)
    health: np.ndarray = field(default=None)
    affected: str = "l"
    day: int = 0

    def __post_init__(self):
        if self.health is None:
            self.health = self.health0.copy()


def build_patient(model, card: str, seed: int, age: int, lesion: dict, traits: dict) -> Patient:
    c, p = model.c, model.p
    rng = np.random.default_rng(seed)
    W_pre = (c.W0 * np.exp(rng.normal(0, 0.15, c.W0.shape))
             ).astype(np.float32)  # existing edges only
    ones, k_base = np.ones(76), np.full(76, p.k_base)
    b_pre = model.baseline_drive(W_pre, ones, k_base)
    b, k_ih = apply_overcomp(c, b_pre, p.k_base, traits["overcomp"])
    M_base = model.run(W_pre, ones, k_ih, b, 1.0, noise=False).peak_M
    h0, hmax, W = apply_lesion(c, W_pre, lesion)
    return Patient(card, age, seed, dict(lesion), h0, hmax, dict(traits), W, W_pre, W.copy(), k_ih, b, float(M_base))


def make_card(card: str, seed: int = 0, model=None) -> Patient:
    from brain.model import get_model
    model = model or get_model()
    if card in CARDS:
        s = CARDS[card]
        return build_patient(model, card, seed, s["age"], s["lesion"], s["traits"])
    return sample_random_patient(np.random.default_rng(seed), model, card="R", seed=seed)


def sample_random_patient(rng: np.random.Generator, model=None, card: str = "R", seed: int | None = None) -> Patient:
    from brain.model import get_model
    model = model or get_model()
    seed = int(rng.integers(0, 2**31 - 1)) if seed is None else seed
    age = int(rng.integers(40, 86))
    af = 1.3 - 0.01 * (age - 40)
    lesion = {}
    if rng.random() < 0.7:
        lesion["lM1"] = float(rng.uniform(0.1, 0.8))
    n_other = int(rng.integers(0, 3)) if lesion else int(rng.integers(1, 4))
    for name in rng.choice(MOTOR_CANDIDATES, size=n_other, replace=False):
        lesion[str(name)] = float(rng.uniform(0.1, 0.8))
    traits = dict(plasticity=float(rng.lognormal(0, 0.3) * af), overcomp=float(rng.uniform(0.7, 1.8)),
                  stim_sens=float(rng.lognormal(0, 0.35)), best_offset_ms=float(rng.uniform(-150, 0)),
                  window_tau_days=float(20 + 20 * rng.random() * af), adherence=float(rng.uniform(0.5, 1.0)),
                  effort_mean=float(rng.uniform(0.5, 0.95)))
    return build_patient(model, card, seed, age, lesion, traits)


if __name__ == "__main__":
    from brain.model import get_model
    m = get_model()
    for c in ("A", "B", "R"):
        pt = make_card(c, 1)
        print(c, pt.age, pt.lesion, f"arm_function={m.arm_function(pt):.2f}")
