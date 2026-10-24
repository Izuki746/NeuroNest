"""Session tests. These use the REAL model (get_model() calibrates once, a few seconds)."""
import numpy as np
import pytest

from brain.session import ATTEMPTS_PER_DAY, Session
from brain.types import AttemptResult, DayResult


@pytest.fixture(scope="module")
def model():
    from brain.model import get_model
    return get_model()


def make(model, card="B", seed=0):
    return Session(card, seed, model=model, use_twins=False)


def test_info_contract_and_no_hidden_traits(model):
    s = make(model)
    info = s.info()
    assert info["card"] == "B" and info["affected_arm"] == "right"
    assert len(info["regions"]) == 76 and np.asarray(
        info["centres"]).shape == (76, 3)
    assert "lM1" in info["lesion"]
    assert "traits" not in info and "best_offset_ms" not in str(info)


def test_attempt_result_shapes(model):
    s = make(model)
    r = s.submit_attempt(1000.0, 0.8, 0.9, 0.7, 300.0)
    assert isinstance(r, AttemptResult)
    assert r.frames.shape == (35, 76) and r.frame_dt_ms == 20.0
    assert r.weight_change.shape == (
        76, 76) and r.weight_change.dtype == np.float32
    assert 0.0 <= r.arm_output <= 1.2 and r.attempts_today == 1 and r.day == 1
    assert r.stim_verdict is None and r.stim_offset_ms is None and r.gate == 1.0


def test_stim_window_and_verdict(model):
    s = make(model)
    s.submit_stim(1000.0 - 80.0, "lPMCDL", "excite")
    r = s.submit_attempt(1000.0, 0.8, 0.9, 0.7, 300.0)
    assert r.stim_offset_ms == pytest.approx(
        -80.0) and r.stim_verdict in ("good", "early", "late")
    assert r.gate > 1.0
    # outside +-400 ms: must not be used
    s.submit_stim(1000.0 + 5000.0, "lPMCDL", "excite")
    r2 = s.submit_attempt(3000.0, 0.8, 0.9, 0.7, 300.0)
    assert r2.stim_verdict is None
    # valid region, wrong side for excite
    s.submit_stim(0.0, "rM1", "excite")
    r3 = s.submit_attempt(100.0, 0.8, 0.9, 0.7, 300.0)
    assert r3.stim_verdict == "wrong_region"


def test_unknown_region_and_kind_ignored(model):
    s = make(model)
    s.submit_stim(0.0, "nope", "excite")
    s.submit_stim(0.0, "lM1", "zap")
    assert s.submit_attempt(100.0, 0.5, 0.5, 0.5, 100.0).stim_verdict is None


def test_auto_end_day_at_20(model):
    s = make(model)
    last = None
    for i in range(ATTEMPTS_PER_DAY):
        last = s.submit_attempt(1000.0 + 3000 * i, 0.8, 0.9, 0.7, 300.0)
    assert last.attempts_today == ATTEMPTS_PER_DAY and last.day == 1
    assert s.day == 2 and s.attempts_today == 0
    assert isinstance(s.last_day_result,
                      DayResult) and s.last_day_result.day == 1
    assert s.state()["curve"]["days"] == [1]


def test_stroke_lowers_function_and_state_keys(model):
    s = make(model, "A")
    assert 0.1 < s.state()["arm_function"] < 0.5
    st = s.state()
    for k in ("patient", "day", "attempts_today", "arm_function", "arm_output", "activity", "health",
              "timing", "forecast", "curve"):
        assert k in st
    assert len(st["activity"]) == 76 and len(st["health"]) == 76


def test_idle_frame_shape_and_range(model):
    f = make(model).idle_frame(12345.0)
    assert f.shape == (76,) and f.min() >= 0 and f.max() <= 1


def test_deterministic_with_seed(model):
    a, b = make(model, seed=3), make(model, seed=3)
    for s in (a, b):
        for i in range(10):
            s.submit_attempt(1000.0 + 3000 * i, 0.8, 0.9, 0.7, 300.0)
    assert a._arm_fn == pytest.approx(b._arm_fn)


def test_good_timing_beats_bad_timing_over_a_week(model):
    gains = {}
    for name, off in (("good", -80.0), ("bad", 300.0)):
        s = make(model, "B", 0)
        base, t = s._arm_fn, 1000.0
        for _ in range(7 * ATTEMPTS_PER_DAY):
            s.submit_stim(t + off, "lPMCDL", "excite")
            s.submit_attempt(t, 0.8, 0.9, 0.7, 300.0)
            t += 3000.0
        gains[name] = s._arm_fn - base
    assert gains["good"] > gains["bad"]


def test_fast_forward_and_plans(model):
    s = make(model, "B", 0)
    res = s.fast_forward(10)
    assert [r.day for r in res] == list(range(1, 11)) and s.day == 11
    none = make(model, "B", 0)
    none.fast_forward(10, {"target": None, "sessions_per_day": 0})
    assert none.state()[
        "curve"]["arm"][-1] <= s.state()["curve"]["arm"][-1] + 1e-6
    assert all(r.attempts == 0 for r in none.history)


def test_clone_is_independent(model):
    s = make(model)
    c = s.clone()
    c.fast_forward(3)
    assert s.day == 1 and c.day == 4
    assert np.array_equal(s.pt.W, make(model).pt.W)


def test_planned_stim(model):
    s = make(model)
    assert s.planned_stim() is None
    s.set_plan({"target": "lPMCDL", "kind": "excite",
               "offset_ms": -80, "intensity": 1.0})
    assert s.planned_stim() == {
        "region": "lPMCDL", "kind": "excite", "offset_ms": -80.0, "intensity": 1.0}
