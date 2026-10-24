"""Train and use quantile recovery forecasters and a plan-response surrogate."""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "population.npz"
DEFAULT_MODEL_DIR = ROOT / "models"
MOTOR_REGIONS = ("lM1", "lPMCDL", "lPMCVL", "lPMCM", "lS1", "lPCS", "lS2")
PLAN_TARGETS = ("", "lPMCDL", "lPMCVL", "lPMCM", "lM1", "lS1", "rM1", "rPMCDL")
PLAN_KINDS = ("", "excite", "calm")
REQUIRED_TRAITS = (
    "plasticity",
    "overcomp",
    "stim_sens",
    "best_offset_ms",
    "window_tau_days",
    "adherence",
    "effort_mean",
)

FORECAST_FEATURES = (
    *(f"health_{region}" for region in MOTOR_REGIONS),
    "lesion_load",
    "age",
    "arm_day1",
    "arm_day7",
    "arm_slope_days1_7",
    "mean_effort_days1_7",
    "adherence_proxy_days1_7",
    "planned_stim_dose",
    "plan_offset_ms",
    "plan_start_day",
    *(f"plan_target_{target or 'none'}" for target in PLAN_TARGETS),
    *(f"plan_kind_{kind or 'none'}" for kind in PLAN_KINDS),
)
SURROGATE_FEATURES = (
    "plasticity",
    "overcomp",
    "stim_sens",
    "best_offset_ms",
    "window_tau_days",
    "adherence",
    "effort_mean",
    *(f"health_{region}" for region in MOTOR_REGIONS),
    "lesion_load",
    "age",
    "current_day",
    "current_arm",
    "plan_offset_ms",
    "plan_intensity",
    "plan_sessions_per_day",
    "plan_start_day",
    *(f"plan_target_{target or 'none'}" for target in PLAN_TARGETS),
    *(f"plan_kind_{kind or 'none'}" for kind in PLAN_KINDS),
)
QUANTILES = (0.1, 0.5, 0.9)
SURROGATE_DAYS = (1, 7, 14, 30, 60)


def _target_one_hot(value: str, values: tuple[str, ...], prefix: str) -> list[float]:
    return [float(value == choice) for choice in values]


def _load_arrays(path: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            required = {
                "age", "traits", "lesion", "plan_target", "plan_kind",
                "plan_offset_ms", "plan_intensity", "plan_sessions_per_day",
                "plan_start_day", "arm", "efforts", "attempts",
            }
            missing = required.difference(archive.files)
            if missing:
                raise ValueError(f"population dataset is missing {sorted(missing)}")
            return {name: archive[name].copy() for name in required | {
                "_trait_names", "_region_names", "completed_patients", "days"
            } if name in archive.files}
    except OSError as exc:
        raise FileNotFoundError(f"Cannot read population dataset {path}: {exc}") from exc


def _indices(names: np.ndarray, required: tuple[str, ...]) -> list[int]:
    name_to_index = {str(name): i for i, name in enumerate(names)}
    missing = [name for name in required if name not in name_to_index]
    if missing:
        raise ValueError(f"population data lacks required region names: {missing}")
    return [name_to_index[name] for name in required]


def _common_features(arrays: dict[str, np.ndarray]) -> np.ndarray:
    ages, lesion, arm = arrays["age"], arrays["lesion"], arrays["arm"]
    days = arm.shape[1]
    observed = min(days, 7)
    arm_day1 = arm[:, 0]
    arm_day7 = arm[:, observed - 1]
    slope = ((arm_day7 - arm_day1) / (observed - 1)
             if observed > 1 else np.zeros_like(arm_day1))
    effort = arrays["efforts"][:, :observed].mean(axis=1)
    adherence = (arrays["attempts"][:, :observed] > 0).mean(axis=1)
    region_ids = _indices(arrays["_region_names"], MOTOR_REGIONS)
    targets = np.char.strip(arrays["plan_target"].astype(str))
    kinds = np.char.strip(arrays["plan_kind"].astype(str))
    dose = (
        arrays["plan_sessions_per_day"] * arrays["plan_intensity"] *
        (targets != "").astype(np.float32) / 2.0
    )
    rows = []
    for i in range(len(ages)):
        rows.append([
            *lesion[i, region_ids],
            float((1.0 - lesion[i]).sum()),
            float(ages[i]),
            float(arm_day1[i]),
            float(arm_day7[i]),
            float(slope[i]),
            float(effort[i]),
            float(adherence[i]),
            float(dose[i]),
            float(arrays["plan_offset_ms"][i]),
            float(arrays["plan_start_day"][i]),
            *_target_one_hot(targets[i], PLAN_TARGETS, "target"),
            *_target_one_hot(kinds[i], PLAN_KINDS, "kind"),
        ])
    return np.asarray(rows, dtype=np.float32)


def _surrogate_dataset(
    arrays: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    traits = arrays["traits"]
    trait_names = [str(name) for name in arrays["_trait_names"]]
    trait_index = {name: i for i, name in enumerate(trait_names)}
    required_traits = REQUIRED_TRAITS
    if any(name not in trait_index for name in required_traits):
        raise ValueError("population data lacks required surrogate traits")
    trait_values = traits[:, [trait_index[name] for name in required_traits]]
    lesion = arrays["lesion"]
    region_ids = _indices(arrays["_region_names"], MOTOR_REGIONS)
    targets = np.char.strip(arrays["plan_target"].astype(str))
    kinds = np.char.strip(arrays["plan_kind"].astype(str))
    arms, features = [], []
    available_days = arrays["arm"].shape[1]
    for day in SURROGATE_DAYS:
        if day >= available_days:
            continue
        current_arms = arrays["arm"][:, day - 1]
        for i in range(len(current_arms)):
            features.append([
                *trait_values[i],
                *lesion[i, region_ids],
                float((1.0 - lesion[i]).sum()),
                float(arrays["age"][i]),
                float(day),
                float(current_arms[i]),
                float(arrays["plan_offset_ms"][i]),
                float(arrays["plan_intensity"][i]),
                float(arrays["plan_sessions_per_day"][i]),
                float(arrays["plan_start_day"][i]),
                *_target_one_hot(targets[i], PLAN_TARGETS, "target"),
                *_target_one_hot(kinds[i], PLAN_KINDS, "kind"),
            ])
            arms.append(float(arrays["arm"][i, -1]))
    return np.asarray(features, dtype=np.float32), np.asarray(arms, dtype=np.float32)


def surrogate_features_for(
    info: dict,
    traits: dict[str, float],
    plan: dict,
    day: int,
    arm_function: float,
) -> np.ndarray:
    """Build one surrogate feature row from public state and a candidate twin."""
    health = {name: 1.0 for name in MOTOR_REGIONS}
    health.update({name: float(value) for name, value in info.get("lesion", {}).items()
                   if name in health})
    target = str(plan.get("target") or "")
    kind = str(plan.get("kind") or "")
    row = [
        *(float(traits[name]) for name in (
            "plasticity", "overcomp", "stim_sens", "best_offset_ms",
            "window_tau_days", "adherence", "effort_mean")),
        *(health[name] for name in MOTOR_REGIONS),
        float(sum(1.0 - value for value in info.get("lesion", {}).values())),
        float(info.get("age", 60)),
        float(day),
        float(arm_function),
        float(plan.get("offset_ms", 0.0)),
        float(plan.get("intensity", 1.0)),
        float(plan.get("sessions_per_day", 1)),
        float(plan.get("start_day", 1)),
        *_target_one_hot(target, PLAN_TARGETS, "target"),
        *_target_one_hot(kind, PLAN_KINDS, "kind"),
    ]
    return np.asarray(row, dtype=np.float32)


def _train_booster(lgb, x_train, y_train, alpha: float, seed: int):
    dataset = lgb.Dataset(x_train, label=y_train, free_raw_data=True)
    return lgb.train(
        {
            "objective": "quantile",
            "alpha": alpha,
            "metric": "quantile",
            "verbosity": -1,
            "learning_rate": 0.04,
            "num_leaves": 15,
            "min_data_in_leaf": max(8, min(25, len(y_train) // 25)),
            "feature_fraction": 0.9,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "seed": seed,
            "num_threads": 2,
        },
        dataset,
        num_boost_round=120,
    )


def _calibration_table(y: np.ndarray, low: np.ndarray,
                       high: np.ndarray) -> list[dict]:
    if not len(y):
        return []
    order = np.argsort(high - low)
    bins = np.array_split(order, min(5, len(order)))
    return [{
        "count": int(len(index)),
        "mean_interval_width": float(np.mean(high[index] - low[index])),
        "coverage": float(np.mean((y[index] >= low[index]) & (y[index] <= high[index]))),
    } for index in bins if len(index)]


def _portable_data_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.parent.resolve()).as_posix()
    except ValueError:
        return str(path)


def train_models(
    data_path: str | Path = DEFAULT_DATA,
    output_dir: str | Path = DEFAULT_MODEL_DIR,
    *,
    seed: int = 42,
) -> dict:
    """Train patient-held-out forecasts and a full-data plan-response surrogate."""
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError(
            "LightGBM is required to train the forecaster; install requirements.txt"
        ) from exc

    data_path, output_dir = Path(data_path), Path(output_dir)
    arrays = _load_arrays(data_path)
    y = arrays["arm"][:, -1].astype(np.float32)
    x = _common_features(arrays)
    if len(y) < 30:
        raise ValueError(f"Need at least 30 patients to train, found {len(y)}")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(y))
    split = max(1, int(round(0.8 * len(y))))
    split = min(split, len(y) - 1)
    train_ids, test_ids = order[:split], order[split:]
    models = {
        alpha: _train_booster(lgb, x[train_ids], y[train_ids], alpha, seed)
        for alpha in QUANTILES
    }
    pred = {alpha: models[alpha].predict(x[test_ids]) for alpha in QUANTILES}
    p10, p50, p90 = (np.clip(pred[q], 0.0, 1.2) for q in QUANTILES)
    truth = y[test_ids]
    proportional = np.minimum(1.0, arrays["arm"][test_ids, 0] +
                              0.7 * (1.0 - arrays["arm"][test_ids, 0]))
    last_value = arrays["arm"][test_ids, 0]
    metrics = {
        "schema_version": 1,
        "data_path": _portable_data_path(data_path),
        "patients": int(len(y)),
        "train_patients": int(len(train_ids)),
        "test_patients": int(len(test_ids)),
        "seed": int(seed),
        "day90_mae": {
            "forecaster": float(np.mean(np.abs(truth - p50))),
            "proportional_recovery": float(np.mean(np.abs(truth - proportional))),
            "last_value": float(np.mean(np.abs(truth - last_value))),
        },
        "p10_p90_coverage": float(np.mean((truth >= p10) & (truth <= p90))),
        "mean_interval_width": float(np.mean(p90 - p10)),
        "calibration": _calibration_table(truth, p10, p90),
        "note": "Synthetic simulated patients only; not clinically validated.",
    }

    sx, sy = _surrogate_dataset(arrays)
    if len(sy) < 30:
        raise ValueError(f"Need at least 30 surrogate rows, found {len(sy)}")
    surrogate_models = {
        alpha: _train_booster(lgb, sx, sy, alpha, seed + 100 + i)
        for i, alpha in enumerate(QUANTILES)
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for alpha, booster in models.items():
        booster.save_model(str(output_dir / f"forecaster_q{int(alpha * 100):02d}.txt"))
    for alpha, booster in surrogate_models.items():
        booster.save_model(str(output_dir / f"surrogate_q{int(alpha * 100):02d}.txt"))
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "forecast_features": list(FORECAST_FEATURES),
        "surrogate_features": list(SURROGATE_FEATURES),
        "quantiles": list(QUANTILES),
        "surrogate_training_rows": int(len(sy)),
        "data_path": _portable_data_path(data_path),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    log.info("Trained models using %d patients (%d held out)", len(y), len(test_ids))
    return metrics


def load_boosters(model_dir: str | Path = DEFAULT_MODEL_DIR) -> dict[str, dict[float, object]]:
    try:
        import lightgbm as lgb
    except ImportError as exc:
        raise RuntimeError("LightGBM is required to load trained models") from exc
    root = Path(model_dir)
    result: dict[str, dict[float, object]] = {"forecaster": {}, "surrogate": {}}
    for family in result:
        for alpha in QUANTILES:
            path = root / f"{family}_q{int(alpha * 100):02d}.txt"
            if path.exists():
                result[family][alpha] = lgb.Booster(model_file=str(path))
    if any(len(result[family]) != len(QUANTILES) for family in result):
        raise FileNotFoundError(
            f"Trained forecaster and surrogate models not found in {root}; run python -m ai.forecaster"
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(json.dumps(train_models(args.data, args.output_dir, seed=args.seed), indent=2))


if __name__ == "__main__":
    main()
