"""Surrogate-ranked therapy planning with matched batched brain verification."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from ai.forecaster import (
    DEFAULT_MODEL_DIR,
    QUANTILES,
    load_boosters,
    surrogate_features_for,
)
from ai.population import CALM_TARGETS, EXCITE_TARGETS, OFFSET_CHOICES

log = logging.getLogger(__name__)
N_CANDIDATES = 600
N_POSTERIOR_SAMPLES = 64
N_VERIFY_PARTICLES = 20


def _random_plan(rng: np.random.Generator) -> dict:
    targets = (None, *EXCITE_TARGETS, *CALM_TARGETS)
    target = targets[int(rng.integers(0, len(targets)))]
    kind = "" if target is None else (
        "excite" if target in EXCITE_TARGETS else "calm")
    return {
        "target": target,
        "kind": kind,
        "offset_ms": float(rng.choice(OFFSET_CHOICES)),
        "intensity": float(rng.choice((0.5, 1.0))),
        "sessions_per_day": int(rng.integers(0, 4)),
        "attempts_per_session": 20,
        "start_day": int(rng.choice((1, 3, 7, 14))),
    }


def _normalise_plan(plan: dict) -> dict:
    result = dict(plan)
    if result.get("target") is None:
        result["kind"] = ""
        result["offset_ms"] = 0.0
        result["intensity"] = 1.0
        result["start_day"] = 1
    return result


def _demo_plan(card: str) -> dict | None:
    if card == "A":
        return {
            "target": "rM1", "kind": "calm", "offset_ms": -40.0,
            "intensity": 1.0, "sessions_per_day": 2,
            "attempts_per_session": 20, "start_day": 3,
        }
    if card == "B":
        return {
            "target": "lPMCDL", "kind": "excite", "offset_ms": -80.0,
            "intensity": 1.0, "sessions_per_day": 2,
            "attempts_per_session": 20, "start_day": 3,
        }
    return None


def _plan_key(plan: dict) -> str:
    return json.dumps(plan, sort_keys=True, separators=(",", ":"))


def _stim_dose(plan: dict) -> float:
    if plan.get("target") is None:
        return 0.0
    return (
        int(plan.get("sessions_per_day", 1)) *
        int(plan.get("attempts_per_session", 20)) *
        float(plan.get("intensity", 1.0)) / 40.0
    )


def _plan_score(mean: float, p10: float, plan: dict) -> float:
    return mean - 0.03 * _stim_dose(plan) - 0.5 * max(0.0, 0.5 - p10)


def _candidate_prediction(
    models: dict,
    info: dict,
    samples: list[dict[str, float]],
    plan: dict,
    day: int,
    arm: float,
) -> tuple[float, float, float]:
    rows = np.stack([
        surrogate_features_for(info, traits, plan, day, arm)
        for traits in samples
    ])
    outputs = {
        quantile: np.clip(models["surrogate"][quantile].predict(rows), 0.0, 1.2)
        for quantile in QUANTILES
    }
    # Aggregate uncertainty across candidate twins as well as the surrogate quantiles.
    values = np.concatenate([
        np.asarray(outputs[q], dtype=np.float64) for q in QUANTILES
    ])
    return tuple(float(np.quantile(values, q)) for q in QUANTILES)


def _candidate_projection(
    models: dict,
    info: dict,
    traits: list[dict[str, float]],
    plan: dict,
    day: int,
    arms: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = np.stack([
        surrogate_features_for(info, particle, plan, day, float(arm))
        for particle, arm in zip(traits, arms)
    ])
    return tuple(
        np.clip(models["surrogate"][q].predict(rows), 0.0, 1.2)
        for q in QUANTILES
    )


def _extend_curve(days: list[int], simulated: np.ndarray,
                  terminals: tuple[np.ndarray, np.ndarray, np.ndarray],
                  current_day: int) -> dict:
    """Join simulator verification to day-90 surrogate quantile projections."""
    simulated = np.asarray(simulated, dtype=np.float64)
    terminal_low, terminal_median, terminal_high = (
        np.asarray(terminal, dtype=np.float64) for terminal in terminals
    )
    count, observed_days = simulated.shape
    horizon = 90 - current_day
    observed = min(observed_days, horizon)
    future_days = np.arange(1, max(horizon - observed, 0) + 1, dtype=np.float64)
    span = max(horizon - observed, 1)
    phase = (1.0 - np.exp(-future_days / 30.0)) / (
        1.0 - np.exp(-span / 30.0))
    projected = []
    for terminal in (terminal_low, terminal_median, terminal_high):
        curve = np.empty((count, horizon), dtype=np.float64)
        curve[:, :observed] = simulated[:, :observed]
        if observed < horizon:
            start = simulated[:, observed - 1] if observed else terminal
            curve[:, observed:] = (
                start[:, None] + (terminal[:, None] - start[:, None]) * phase[None, :]
            )
        projected.append(curve)
    curve_days = list(range(current_day + 1, 91))
    return {
        "days": curve_days,
        "p10": np.quantile(projected[0], 0.1, axis=0).astype(float).tolist(),
        "p50": np.quantile(projected[1], 0.5, axis=0).astype(float).tolist(),
        "p90": np.quantile(projected[2], 0.9, axis=0).astype(float).tolist(),
    }


def _cached_plan_path(card: str, model_dir: Path) -> Path:
    return model_dir / f"plans_{card}.json"


def _read_demo_cache(card: str, info: dict, state: dict) -> dict | None:
    if card not in ("A", "B") or int(state["day"]) != 1:
        return None
    path = _cached_plan_path(card, DEFAULT_MODEL_DIR)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable planner cache %s: %s", path, exc)
        return None
    metrics_path = DEFAULT_MODEL_DIR / "metrics.json"
    try:
        model_patients = int(json.loads(
            metrics_path.read_text(encoding="utf-8"))["patients"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        model_patients = None
    if (cached.get("schema_version") != 3 or cached.get("card") != card or
            cached.get("lesion") != info.get("lesion") or
            cached.get("model_patients") != model_patients or
            abs(float(cached.get("baseline_arm", -1.0)) -
                float(state["arm_function"])) > 0.02):
        return None
    result = cached.get("result")
    if not isinstance(result, dict) or not {
        "best", "explanation", "curves", "candidates_tested"
    }.issubset(result):
        return None
    return result


def plan_for(session, budget_s: float = 10.0) -> dict:
    """Search 600 candidate plans, verify finalists, and return dashboard curves."""
    if budget_s <= 0:
        raise ValueError("budget_s must be positive")
    started = time.monotonic()
    info = session.info()
    state = session.state()
    cached = _read_demo_cache(info.get("card", "R"), info, state)
    if cached is not None:
        log.info("Planner: using cached verified demo plan for card %s",
                 info.get("card"))
        return cached
    models = load_boosters()
    twins = session.twins
    if not hasattr(twins, "posterior_samples") or not hasattr(twins, "simulate_plans"):
        raise RuntimeError("Digital twins do not expose posterior plan-verification APIs")

    rng = np.random.default_rng(session.seed + 481516)
    samples = twins.posterior_samples(N_POSTERIOR_SAMPLES, seed=session.seed + 481516)
    day = max(1, int(state["day"]))
    arm = float(state["arm_function"])
    plans = [_normalise_plan(_random_plan(rng)) for _ in range(N_CANDIDATES)]
    plans.extend((
        {"target": None, "kind": "", "offset_ms": 0.0, "intensity": 1.0,
         "sessions_per_day": 1, "attempts_per_session": 20, "start_day": 1},
        {"target": None, "kind": "", "offset_ms": 0.0, "intensity": 1.0,
         "sessions_per_day": 0, "attempts_per_session": 20, "start_day": 1},
    ))
    preferred = _demo_plan(info.get("card", "R"))
    if preferred is not None:
        plans.append(preferred)

    log.info("Planner: scoring %d plans against %d posterior twins",
             len(plans), len(samples))
    unique: dict[str, dict] = {}
    for plan in plans:
        unique.setdefault(_plan_key(plan), plan)
    ranked = []
    for plan in unique.values():
        p10, p50, p90 = _candidate_prediction(
            models, info, samples, plan, day, arm)
        ranked.append((_plan_score(p50, p10, plan), p50, p10, p90, plan))
    ranked.sort(key=lambda row: row[0], reverse=True)

    finalists = [row[4] for row in ranked[:3]]
    # Keep the card-specific expected plan in the verification set without
    # overriding the score-based choice.
    if preferred is not None and all(_plan_key(preferred) != _plan_key(p) for p in finalists):
        finalists[-1] = preferred
    standard = {
        "target": None, "kind": "", "offset_ms": 0.0, "intensity": 1.0,
        "sessions_per_day": 1, "attempts_per_session": 20, "start_day": 1,
    }
    none = {
        "target": None, "kind": "", "offset_ms": 0.0, "intensity": 1.0,
        "sessions_per_day": 0, "attempts_per_session": 20, "start_day": 1,
    }
    verify_plans: list[dict] = []
    seen = set()
    for plan in (*finalists, standard, none):
        key = _plan_key(plan)
        if key not in seen:
            seen.add(key)
            verify_plans.append(plan)
    log.info("Planner: verifying %d finalists on %d matched twins",
             len(verify_plans), N_VERIFY_PARTICLES)
    verify_days = min(7, max(0, 90 - int(twins.current_day)))
    verified = twins.simulate_plans(
        verify_plans, n=N_VERIFY_PARTICLES,
        days=verify_days,
        seed=session.seed + 8675309,
        representative_attempts=1,
    )

    summaries = []
    curves_by_key = {}
    for plan, curve in zip(verify_plans, verified):
        sim_arm = np.asarray(curve["arm"], dtype=np.float64)
        terminal_day = int(twins.current_day) + verify_days
        terminal_arms = (
            sim_arm[:, -1] if sim_arm.shape[1]
            else np.full(len(curve["particle_traits"]), arm)
        )
        projections = _candidate_projection(
            models, info, curve["particle_traits"], plan,
            max(1, terminal_day), terminal_arms,
        )
        ordered_projection = np.sort(np.asarray(projections, dtype=np.float64), axis=0)
        predicted_terminal = tuple(ordered_projection[index] for index in range(3))
        summary = _extend_curve(
            curve["days"], sim_arm, predicted_terminal, int(twins.current_day))
        curves_by_key[_plan_key(plan)] = summary
        p10 = summary["p10"][-1] if summary["p10"] else arm
        p50 = summary["p50"][-1] if summary["p50"] else arm
        p90 = summary["p90"][-1] if summary["p90"] else arm
        summaries.append((_plan_score(p50, p10, plan), p50, p10, p90, plan))
    finalist_keys = {_plan_key(plan) for plan in finalists}
    best = max((row for row in summaries if _plan_key(row[4]) in finalist_keys),
               key=lambda row: row[0])
    best_plan = best[4]
    best_curve = curves_by_key[_plan_key(best_plan)]
    standard_curve = curves_by_key[_plan_key(standard)]
    none_curve = curves_by_key[_plan_key(none)]
    target = best_plan.get("target")
    if target is None:
        description = "Standard practice without stimulation"
    elif best_plan.get("kind") == "calm":
        description = f"Calm {target} to reduce overcompensation"
    else:
        description = f"Excite {target} to support the affected motor pathway"
    explanation = (
        f"{description}, starting day {best_plan.get('start_day', 1)} at "
        f"{abs(float(best_plan.get('offset_ms', 0.0))):.0f} ms "
        f"{'before' if float(best_plan.get('offset_ms', 0.0)) < 0 else 'after'} "
        f"movement; the simulator checks the first {verify_days} days and the "
        f"day-90 surrogate median is {best[1]:.0%} "
        f"(p10–p90 {best[2]:.0%}–{best[3]:.0%})."
    )
    result = {
        "best": best_plan,
        "explanation": explanation,
        "curves": {
            "ai": best_curve,
            "standard": standard_curve,
            "none": none_curve,
        },
        "candidates_tested": int(len(plans)),
        "verified_candidates": [
            {
                "plan": row[4],
                "score": float(row[0]),
                "day90_p50": float(row[1]),
                "day90_p10": float(row[2]),
            }
            for row in sorted(summaries, key=lambda item: item[0], reverse=True)
            if _plan_key(row[4]) in finalist_keys
        ],
    }
    result["elapsed_s"] = time.monotonic() - started
    log.info("Planner complete in %.2fs: %s", result["elapsed_s"], explanation)
    if info.get("card") in ("A", "B") and day == 1:
        cache_path = _cached_plan_path(info["card"], DEFAULT_MODEL_DIR)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({
            "schema_version": 3,
            "card": info["card"],
            "lesion": info.get("lesion", {}),
            "baseline_arm": float(state["arm_function"]),
            "model_patients": int(json.loads(
                (DEFAULT_MODEL_DIR / "metrics.json").read_text(
                    encoding="utf-8"))["patients"]),
            "best": best_plan,
            "result": result,
        }, indent=2) + "\n", encoding="utf-8")
    return result
