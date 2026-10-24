from types import SimpleNamespace

import numpy as np
import pytest

from brain import plasticity as P

NAMES = [f"r{b}" for b in ("M1", "PMCDL", "PMCVL", "PMCM", "PFCDL", "S1")] + \
        [f"l{b}" for b in ("M1", "PMCDL", "PMCVL", "PMCM", "PFCDL", "S1")]
IDX = {n: i for i, n in enumerate(NAMES)}
N = len(NAMES)


def make_patient(seed=0, day=0):
    rng = np.random.default_rng(seed)
    S = rng.random((N, N)) < 0.4
    np.fill_diagonal(S, False)
    W_pre = (rng.random((N, N)) * S).astype(np.float32)
    return SimpleNamespace(
        W=W_pre.copy(), W_pre=W_pre, k_ih=np.full(N, 0.35, dtype=np.float32), day=day,
        traits=dict(plasticity=1.0, stim_sens=1.0,
                    best_offset_ms=-80.0, window_tau_days=30.0),
    ), S


def test_eligibility_causal_vs_anticausal():
    R = np.full((200, 3), 0.05)
    R[20:40, 0] = 0.8
    R[40:60, 1] = 0.8
    E = P.eligibility_from_trajectory(R, 0.05)
    assert E[1, 0] > 0 and E[0, 1] < 0 and E[1, 0] > abs(E[0, 1])


def test_eligibility_resting_is_zero_and_batch_matches_single():
    rest = np.full((100, 4), 0.05)
    assert np.allclose(P.eligibility_from_trajectory(rest, 0.05), 0)
    rng = np.random.default_rng(1)
    R = rng.random((3, 100, 4))
    Eb = P.eligibility_from_trajectory(R, 0.05)
    assert Eb.shape == (3, 4, 4)
    assert np.allclose(
        Eb[1], P.eligibility_from_trajectory(R[1], 0.05), atol=1e-5)


def test_gate_peaks_at_best_offset():
    tr = dict(stim_sens=1.0, best_offset_ms=-80.0)
    g_best = P.stim_gate("lPMCDL", "excite", -80, tr)
    assert g_best.verdict == "good" and g_best.gate == pytest.approx(3.5)
    assert P.stim_gate("lPMCDL", "excite", -250, tr).verdict == "early"
    assert P.stim_gate("lPMCDL", "excite", 100, tr).verdict == "late"
    assert P.stim_gate("lPMCDL", "excite", -250, tr).gate < g_best.gate
    assert P.stim_gate("lPMCDL", "excite", 100, tr).gate < g_best.gate


def test_gate_wrong_region_and_late_maladaptive():
    tr = dict(stim_sens=1.0, best_offset_ms=-80.0)
    for region, kind in [("rM1", "excite"), ("lM1", "calm"), ("lPFCDL", "excite")]:
        g = P.stim_gate(region, kind, -80, tr)
        assert g.verdict == "wrong_region" and g.gate == 1.0
    assert P.stim_gate("rM1", "calm", -40, tr).verdict == "good"
    late = P.stim_gate("lPMCDL", "excite", 300, tr)
    assert late.maladaptive and late.gate == 1.0
    none = P.stim_gate(None, None, None, tr)
    assert none.verdict is None and none.gate == 1.0


def test_good_timing_beats_bad_timing():
    E = np.random.default_rng(2).random((N, N)).astype(np.float32)
    deltas = {}
    for name, off in (("good", -80.0), ("bad", 300.0)):
        p, S = make_patient()
        P.apply_attempt_plasticity(p, S, IDX, E, 1.0, [(
            1000 + off, "lPMCDL", "excite", 1.0)], 1000.0)
        deltas[name] = float(np.abs(p.W - p.W_pre).sum())
    assert deltas["good"] > 2.0 * deltas["bad"]


def test_no_new_edges_and_bounds():
    p, S = make_patient()
    E = np.random.default_rng(3).normal(size=(N, N)).astype(np.float32) * 50
    for _ in range(200):
        P.update_weights(p, E, S, 1.0, 3.0)
    assert np.all(p.W[~S] == 0)
    assert np.all(p.W <= P.W_MAX_FRAC * p.W_pre + 1e-6)
    assert np.all(p.W >= P.W_MIN_FRAC * p.W_pre - 1e-6)


def test_window_and_effort_scale_learning():
    E = np.ones((N, N), dtype=np.float32)
    lo, S = make_patient(day=0)
    hi, _ = make_patient(day=0)
    late, _ = make_patient(day=90)
    assert P.update_weights(
        hi, E, S, 1.0, 1.0) > P.update_weights(lo, E, S, 0.2, 1.0)
    assert P.update_weights(make_patient(
        day=0)[0], E, S, 1.0, 1.0) > P.update_weights(late, E, S, 1.0, 1.0)


def test_late_stim_raises_inhibition_but_capped():
    p, S = make_patient()
    E = np.zeros((N, N), dtype=np.float32)
    for _ in range(2000):
        P.apply_attempt_plasticity(
            p, S, IDX, E, 0.5, [(1300.0, "lPMCDL", "excite", 1.0)], 1000.0)
    assert p.k_ih[IDX["lM1"]] == pytest.approx(P.K_IH_CAP)
    assert p.k_ih[IDX["rM1"]] == pytest.approx(0.35)


def test_use_it_or_lose_it():
    p, _ = make_patient()
    before = p.W.copy()
    assert not P.use_it_or_lose_it(p, IDX, 10)
    assert np.array_equal(p.W, before)
    assert P.use_it_or_lose_it(p, IDX, 2)
    a, b = IDX["lPMCDL"], IDX["lM1"]
    if before[b, a] > 0:
        assert p.W[b, a] == pytest.approx(
            before[b, a] * P.USE_IT_DECAY, rel=1e-5)
    # healthy side untouched
    assert np.array_equal(p.W[IDX["rM1"]], before[IDX["rM1"]])


def test_spontaneous_recovery():
    h0 = np.ones(N, dtype=np.float32)
    h0[IDX["lM1"]] = 0.3
    hmax = h0 + 0.3 * (1 - h0)
    h = P.spontaneous_recovery(h0, h0, hmax, day=1, window_tau_days=30)
    assert h[IDX["lM1"]] > 0.3 and h[IDX["lM1"]] <= hmax[IDX["lM1"]]
    assert np.all(h[np.arange(N) != IDX["lM1"]] == 1.0)
    assert np.array_equal(P.spontaneous_recovery(
        h0, h0, hmax, day=45, window_tau_days=30), h0)
