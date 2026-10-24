"""BatchEngine / BatchCohort tests. Uses the REAL model (get_model() calibrates once)."""
from types import SimpleNamespace

import numpy as np
import pytest

from brain.batch import BatchCohort, BatchEngine, StimSpec


@pytest.fixture(scope="module")
def model():
    from brain.model import get_model
    return get_model()


def _patients(model, n=3):
    from brain.patients import make_card, sample_random_patient
    rng = np.random.default_rng(5)
    return [make_card("A", 0, model), make_card("B", 0, model)] + [sample_random_patient(rng, model) for _ in range(n - 2)]


def test_dt1_float64_reproduces_single_engine_exactly(model):
    pats = _patients(model, 3)
    P = len(pats)
    eng = BatchEngine(model, dt_ms=1.0, t_start_ms=-250,
                      t_end_ms=450, dtype=np.float64)
    W, h = np.stack([q.W for q in pats]), np.stack([q.health for q in pats])
    k, b = np.stack([q.k_ih for q in pats]), np.stack([q.b for q in pats])
    eff = np.array([1.0, 0.7, 0.4])
    idx = model.c.idx
    sp = StimSpec(np.array([-1, idx["lPMCDL"], idx["rM1"]]), np.array([1.0, 1.0, -1.0]), np.ones(P),
                  np.array([0.0, -80.0, 40.0]))
    stims = [[], [(-80.0, "lPMCDL", "excite", 1.0)],
             [(40.0, "rM1", "calm", 1.0)]]
    sim = eng.run(W, h, k, b, eff, sp, plasticity=True)
    for i, q in enumerate(pats):
        ref = model.run(q.W, q.health, q.k_ih, q.b,
                        eff[i], stims[i], noise=False, plasticity=True)
        assert sim.peak_M[i] == pytest.approx(ref.peak_M, rel=1e-4, abs=1e-6)
        assert np.abs(sim.E[i] - ref.E).max() <= 1e-3 * \
            max(1.0, np.abs(ref.E).max())


def test_dt2_arm_function_close_to_single_engine(model):
    pats = _patients(model, 6)
    arm_b, _ = BatchCohort.from_patients(model, pats).arm_function()
    arm_s = np.array([model.arm_function(q) for q in pats])
    assert np.abs(arm_b - arm_s).max() < 0.08


def test_patient_results_do_not_depend_on_batch_mates(model):
    pats = _patients(model, 4)
    solo = BatchCohort.from_patients(model, pats[:1]).arm_function()[0][0]
    together = BatchCohort.from_patients(model, pats).arm_function()[0][0]
    assert solo == pytest.approx(together, abs=1e-4)


def test_one_attempt_p200_is_fast_enough(model):
    import time
    from brain.patients import sample_random_patient
    rng = np.random.default_rng(1)
    co = BatchCohort.from_patients(
        model, [sample_random_patient(rng, model) for _ in range(200)])
    t0 = time.perf_counter()
    co.eng.run(co.W, co.h, co.k_ih, co.b, np.full(200, 0.8), plasticity=True)
    # target 0.6 s; generous so slow laptops don't fail
    assert time.perf_counter() - t0 < 2.0


def test_run_days_shapes_and_no_rehab_matches_session(model):
    from brain.session import Session
    plan = {"target": None, "sessions_per_day": 0}
    s = Session("B", 0, model=model, use_twins=False)
    ref = s.fast_forward(10, plan)[-1].arm_function
    co = BatchCohort.from_patients(model, [_patients(model, 2)[1]])
    out = co.run_days(10, plan, np.random.default_rng(0))
    assert out["arm"].shape == (1, 10) and out["arm0"].shape == (1,)
    assert np.all(out["attempts"] == 0) and np.all(out["stim_count"] == 0)
    assert out["arm"][0, -1] == pytest.approx(ref, abs=0.03)
    assert co.day[0] == 10


def test_standard_plan_does_20_attempts_a_day(model):
    co = BatchCohort.from_patients(model, [_patients(model, 2)[1]])
    out = co.run_days(3, None, np.random.default_rng(0))
    # 0 or 20 (adherence)
    assert np.all(out["attempts"][0] == 20 * (out["attempts"][0] > 0))
    assert np.all(out["stim_count"] == 0)


def test_particles_use_only_scan_info_and_candidate_traits(model):
    pt = _patients(model, 2)[1]
    scan_only = SimpleNamespace(W_pre=pt.W_pre, W_start=pt.W_start, health0=pt.health0,
                                health_max=pt.health_max, M_base=pt.M_base)    # no .traits, no evolved .W
    rng = np.random.default_rng(0)
    tl = [dict(plasticity=1.0, overcomp=float(o), stim_sens=1.0, best_offset_ms=-80.0, window_tau_days=30.0)
          for o in rng.uniform(0.7, 1.8, 12)]
    co = BatchCohort.particles(model, scan_only, tl)
    arm, _ = co.arm_function()
    # different overcompensation -> different day-0 function
    assert arm.shape == (12,) and np.ptp(arm) > 0
    assert np.all(co.h0 == pt.health0[None])
