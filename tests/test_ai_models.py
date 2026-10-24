from __future__ import annotations

import numpy as np

from ai import forecaster, planner


def _population_arrays(patients: int = 12, days: int = 90) -> dict:
    rng = np.random.default_rng(12)
    names = np.asarray([
        "lM1", "lPMCDL", "lPMCVL", "lPMCM", "lS1", "lPCS", "lS2",
    ] + [f"region_{i}" for i in range(69)], dtype="U32")
    traits = rng.uniform(0.5, 1.5, (patients, 7)).astype(np.float32)
    lesion = np.ones((patients, 76), dtype=np.float32)
    lesion[:, 0] = rng.uniform(0.2, 0.9, patients)
    arm = np.clip(
        rng.uniform(0.3, 0.5, (patients, 1)) +
        np.linspace(0.0, 0.2, days)[None, :],
        0.0, 1.0,
    ).astype(np.float32)
    return {
        "age": rng.integers(40, 86, patients, dtype=np.int16),
        "traits": traits,
        "lesion": lesion,
        "plan_target": np.full(patients, "lPMCDL", dtype="U16"),
        "plan_kind": np.full(patients, "excite", dtype="U8"),
        "plan_offset_ms": np.full(patients, -80.0, dtype=np.float32),
        "plan_intensity": np.ones(patients, dtype=np.float32),
        "plan_sessions_per_day": np.full(patients, 2, dtype=np.int8),
        "plan_start_day": np.ones(patients, dtype=np.int8),
        "arm": arm,
        "efforts": np.full((patients, days), 0.7, dtype=np.float32),
        "attempts": np.full((patients, days), 20, dtype=np.int16),
        "_trait_names": np.asarray(forecaster.REQUIRED_TRAITS, dtype="U32"),
        "_region_names": names,
    }


def test_forecaster_feature_builders_match_declared_schema():
    arrays = _population_arrays()
    forecast_features = forecaster._common_features(arrays)
    surrogate_features, target = forecaster._surrogate_dataset(arrays)

    assert forecast_features.shape == (12, len(forecaster.FORECAST_FEATURES))
    assert surrogate_features.shape == (
        12 * len(forecaster.SURROGATE_DAYS),
        len(forecaster.SURROGATE_FEATURES),
    )
    assert target.shape == (surrogate_features.shape[0],)


class _FakeBooster:
    def predict(self, features):
        target_index = forecaster.SURROGATE_FEATURES.index("plan_target_lPMCDL")
        return 0.55 + 0.2 * features[:, target_index]


class _FakeTwins:
    current_day = 0

    def posterior_samples(self, count, seed=None):
        return [{
            "plasticity": 1.0, "overcomp": 1.0, "stim_sens": 1.0,
            "best_offset_ms": -80.0, "window_tau_days": 30.0,
            "adherence": 0.8, "effort_mean": 0.7,
        } for _ in range(count)]

    def simulate_plans(self, plans, n, days, seed, representative_attempts):
        return [{
            "days": list(range(1, days + 1)),
            "arm": np.tile(np.linspace(0.4, 0.6, days), (n, 1)).tolist(),
            "arm0": np.full(n, 0.4).tolist(),
            "particle_traits": self.posterior_samples(n),
        } for _ in plans]


class _FakeSession:
    seed = 3
    twins = _FakeTwins()

    def info(self):
        return {
            "card": "R", "age": 60, "lesion": {"lM1": 0.5},
            "affected_arm": "right",
        }

    def state(self):
        return {"day": 1, "arm_function": 0.4}


def test_planner_returns_contract_curves_with_surrogate_and_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "N_CANDIDATES", 24)
    monkeypatch.setattr(planner, "DEFAULT_MODEL_DIR", tmp_path)
    monkeypatch.setattr(
        planner, "load_boosters",
        lambda: {"surrogate": {q: _FakeBooster() for q in forecaster.QUANTILES}},
    )

    result = planner.plan_for(_FakeSession(), budget_s=10.0)

    assert result["best"]["target"] == "lPMCDL"
    assert result["candidates_tested"] > 0
    assert set(result["curves"]) == {"ai", "standard", "none"}
    for curve in result["curves"].values():
        assert len(curve["days"]) == 90
        assert len(curve["p10"]) == len(curve["p50"]) == len(curve["p90"]) == 90
        assert np.all(np.asarray(curve["p10"]) <= np.asarray(curve["p50"]))
        assert np.all(np.asarray(curve["p50"]) <= np.asarray(curve["p90"]))
