import numpy as np
import pytest

from ai.twins import TwinFilter
from brain.patients import make_card
from brain.types import DayResult


@pytest.fixture(scope="module")
def model():
    from brain.model import get_model
    return get_model()


def test_twins_infer_from_public_state_and_return_forecast(model):
    patient = make_card("B", seed=4, model=model)
    info = {
        "card": "B",
        "age": 45,
        "lesion": {"lM1": 0.5},
        "affected_arm": "right",
        "regions": list(model.c.names),
        "centres": model.c.centres.tolist(),
    }
    twins = TwinFilter(
        info, n=12, seed=4, model=model,
        W_pre=patient.W_pre,
    )
    initial_arm = model.arm_function(patient)
    twins.update(DayResult(0, initial_arm, 0.0, 0, 1.0, 0))
    twins.update(DayResult(1, initial_arm + 0.01, 0.7, 20, 0.95, 0))

    forecast = twins.forecast()
    assert "traits" not in twins.info
    assert not hasattr(twins, "patient")
    assert forecast["day"] == 1
    assert forecast["n_alive"] >= 1
    assert forecast["p10"] <= forecast["p50"] <= forecast["p90"]
    assert forecast["curve"]["days"][0] == 1
    assert len(forecast["curve"]["p50"]) == 90
    assert np.all(np.diff(forecast["curve"]["p50"]) >= -1e-8)
    assert np.isclose(twins.weights.sum(), 1.0)


def test_twin_forecast_cache_invalidates_when_plan_changes(model):
    patient = make_card("B", seed=5, model=model)
    info = {
        "card": "B",
        "age": 45,
        "lesion": {"lM1": 0.5},
        "affected_arm": "right",
    }
    twins = TwinFilter(
        info, n=8, seed=5, model=model,
        W_pre=patient.W_pre,
    )
    first = twins.forecast()
    twins.set_plan({
        "target": "lPMCDL", "kind": "excite", "offset_ms": -80,
        "intensity": 1.0, "sessions_per_day": 2,
    })
    second = twins.forecast()
    assert first is not second
    assert second["p50"] >= first["p50"]
