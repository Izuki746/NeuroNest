"""Behaviour targets (PERSON_A 6.3). Run: python -m tests.test_model   (or pytest)."""
from __future__ import annotations
import numpy as np
from brain.model import get_model, onset_latency_ms
from brain.patients import make_card, build_patient, CARDS
from brain.stroke import apply_lesion


def _healthy(m):
    c, p = m.c, m.p
    W, h, k = c.W0.astype(np.float64), np.ones(76), np.full(76, p.k_base)
    return W, h, k, m.baseline_drive(W, h, k)


def t_rest():
    m = get_model()
    W, h, k, b = _healthy(m)
    sim = m.run(W, h, k, b, attempt=False, t_end=4750,
                rng=np.random.default_rng(1), return_full=True)  # 5 s
    lo, hi = sim.full.min(), sim.full.max()
    return 0.02 <= sim.r_rest.min() and sim.r_rest.max() <= 0.15 and hi < 0.3, f"rest {sim.r_rest.min():.3f}-{sim.r_rest.max():.3f}, 5 s noisy max {hi:.3f}"


def t_prestroke():
    m = get_model()
    W, h, k, b = _healthy(m)
    c = m.c
    s = m.run(W, h, k, b, 1.0, noise=False, return_full=True)
    pk = s.full[:, c.idx["lM1"]].max()
    back = np.abs(s.full[-1] - s.r_rest).max()
    return 0.5 <= pk <= 0.85 and back <= 0.05, f"lM1 peak {pk:.2f} (0.5-0.85); max |r-rest| at +450ms {back:.3f} (<=0.05)"


def t_sequence():
    m = get_model()
    W, h, k, b = _healthy(m)
    c = m.c
    s = m.run(W, h, k, b, 1.0, noise=False, return_full=True)
    lat = [onset_latency_ms(s.full[:, c.idx["l" + n]], s.r_rest[c.idx["l" + n]])
           for n in ("PFCDL", "PMCDL", "M1")]
    return lat[0] < lat[1] < lat[2], f"arrival ms (r>=rest+0.1) PFCDL/PMCDL/M1 = {lat}"


def t_effort():
    m = get_model()
    W, h, k, b = _healthy(m)
    hi = m.run(W, h, k, b, 1.0, noise=False).peak_M
    lo = m.run(W, h, k, b, 0.3, noise=False).peak_M
    return lo / hi < 0.7, f"effort 0.3 / effort 1.0 = {lo/hi:.2f} (<0.7)"


def t_cards():
    m = get_model()
    a = m.arm_function(make_card("A", 0))
    bb = m.arm_function(make_card("B", 0))
    return 0.2 <= a <= 0.4 and 0.35 <= bb <= 0.55, f"card A arm_function {a:.2f} (0.2-0.4), card B {bb:.2f} (0.35-0.55)"


def t_diaschisis():
    m = get_model()
    c = m.c
    W, h, k, b = _healthy(m)
    def ev(s): return np.maximum(s.full - s.r_rest, 0).max(axis=0)  # noqa: E731
    base = ev(m.run(W, h, k, b, 1.0, noise=False, return_full=True))
    h0, _, Wl = apply_lesion(c, W.astype(np.float32), {"lM1": 0.3})
    les = ev(m.run(Wl, h0, k, b, 1.0, noise=False, return_full=True))
    left = [i for i in range(76) if c.hemi[i] == 0 and i != c.idx["lM1"]]
    drops = [i for i in left if base[i] > 0.01 and (
        base[i] - les[i]) / base[i] >= 0.2]
    return len(drops) >= 3, f"{len(drops)} non-lesioned left regions drop >=20% (need >=3): {[c.names[i] for i in drops][:6]}"


def t_overcomp():
    m = get_model()
    pt = make_card("A", 0)
    base = m.run(pt.W, pt.health, pt.k_ih, pt.b,
                 1.0, (), "l", noise=False).peak_M
    calm = m.run(pt.W, pt.health, pt.k_ih, pt.b, 1.0, [
                 (-40, "rM1", "calm", 1.0)], "l", noise=False).peak_M
    return calm / base >= 1.10, f"card A: calm rM1 gives {calm/base:.2f}x arm output (>=1.10)"


TESTS = [t_rest, t_prestroke, t_sequence,
         t_effort, t_cards, t_diaschisis, t_overcomp]
for _t in TESTS:  # pytest wrappers
    globals()["test_" + _t.__name__[2:]] = (lambda f: (lambda: (lambda r: (print(r[1]),
                                                                           r[0])[1] or (_ for _ in ()).throw(AssertionError(r[1])))(f())))(_t)

if __name__ == "__main__":
    import logging
    import time
    logging.basicConfig(level=logging.WARNING)
    t0 = time.time()
    get_model()
    print(f"calibrated in {time.time()-t0:.1f}s\n")
    ok_all = True
    for t in TESTS:
        ok, msg = t()
        ok_all &= ok
        print(("PASS  " if ok else "FAIL  ") + t.__name__[2:].ljust(12) + msg)
    print("\nALL PASS" if ok_all else "\nSOME FAILED: paste this output to Claude to tune")
