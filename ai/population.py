"""Generate a resumable synthetic patient population for training and planning.

Run with ``python -m ai.population``. Patient batches are simulated with the
vectorised BatchCohort and saved locally before optional Tiger Data upload.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
from pathlib import Path
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from brain.batch import BatchCohort
from brain.patients import sample_random_patient

log = logging.getLogger(__name__)

DEFAULT_PATIENTS = 5_000
DEFAULT_CHUNK_SIZE = 200
DEFAULT_DAYS = 90
SCHEMA_VERSION = 1
TRAIT_NAMES = (
    "plasticity",
    "overcomp",
    "stim_sens",
    "best_offset_ms",
    "window_tau_days",
    "adherence",
    "effort_mean",
)
EXCITE_TARGETS = ("lPMCDL", "lPMCVL", "lPMCM", "lM1", "lS1")
CALM_TARGETS = ("rM1", "rPMCDL")
OFFSET_CHOICES = np.arange(-200, 201, 20)
START_DAY_CHOICES = np.array((1, 3, 7, 14))
CHUNK_ARRAYS = (
    "patient_id",
    "age",
    "traits",
    "lesion",
    "plan_target",
    "plan_kind",
    "plan_offset_ms",
    "plan_intensity",
    "plan_sessions_per_day",
    "plan_attempts_per_session",
    "plan_start_day",
    "arm0",
    "arm",
    "efforts",
    "attempts",
    "stim_count",
    "features",
)
FEATURE_NAMES = (
    "age",
    "lesion_load",
    "arm_day1",
    "arm_day7",
    "arm_slope_days1_7",
    "mean_effort_days1_7",
    "adherence_proxy_days1_7",
    "planned_stim_dose",
)


def random_plan(rng: np.random.Generator) -> dict:
    """Sample one plan from the plan schema, with kind matching the target."""
    targets = (None, *EXCITE_TARGETS, *CALM_TARGETS)
    target = targets[int(rng.integers(0, len(targets)))]
    if target is None:
        kind = ""
    elif target in EXCITE_TARGETS:
        kind = "excite"
    else:
        kind = "calm"
    return {
        "target": target,
        "kind": kind,
        "offset_ms": float(rng.choice(OFFSET_CHOICES)),
        "intensity": float(rng.choice((0.5, 1.0))),
        "sessions_per_day": int(rng.integers(0, 4)),
        "attempts_per_session": 20,
        "start_day": int(rng.choice(START_DAY_CHOICES)),
    }


def _features(arm0: np.ndarray, arm: np.ndarray, efforts: np.ndarray, attempts: np.ndarray,
              lesion: np.ndarray, ages: np.ndarray, plan_sessions: np.ndarray,
              plan_target: np.ndarray, plan_intensity: np.ndarray,
              days: int) -> np.ndarray:
    observed = min(days, 7)
    arm_day1 = arm[:, 0] if days else arm0
    arm_day7 = arm[:, observed - 1] if observed else arm0
    if observed > 1:
        slope = (arm_day7 - arm_day1) / (observed - 1)
    else:
        slope = np.zeros_like(arm0)
    effort_mean = efforts[:, :observed].mean(axis=1) if observed else np.zeros_like(arm0)
    adherence_proxy = (attempts[:, :observed] > 0).mean(axis=1) if observed else np.zeros_like(arm0)
    dose = (plan_sessions * 20 * plan_intensity *
            (plan_target != "").astype(np.float32) / 40.0)
    return np.column_stack((
        ages,
        (1.0 - lesion).sum(axis=1),
        arm_day1,
        arm_day7,
        slope,
        effort_mean,
        adherence_proxy,
        dose,
    )).astype(np.float32)


def _simulate_chunk(model, start: int, count: int, days: int, seed: int) -> dict[str, np.ndarray]:
    """Generate a deterministic chunk independent of which other chunks exist."""
    rng = np.random.default_rng(np.random.SeedSequence((seed, start)))
    patients = [sample_random_patient(rng, model) for _ in range(count)]
    plans = [random_plan(rng) for _ in range(count)]
    cohort = BatchCohort.from_patients(model, patients)
    results = cohort.run_days(days, plans=plans, rng=rng)

    names = model.c.names
    lesions_by_patient = np.stack([patient.health0 for patient in patients]).astype(np.float32)
    traits = np.asarray(
        [[patient.traits[name] for name in TRAIT_NAMES] for patient in patients],
        dtype=np.float32,
    )
    targets = np.asarray([plan["target"] or "" for plan in plans], dtype="U16")
    kinds = np.asarray([plan["kind"] for plan in plans], dtype="U8")
    ages = np.asarray([patient.age for patient in patients], dtype=np.int16)
    plan_sessions = np.asarray([plan["sessions_per_day"] for plan in plans], dtype=np.int8)
    plan_intensity = np.asarray([plan["intensity"] for plan in plans], dtype=np.float32)
    arm0 = np.asarray(results["arm0"], dtype=np.float32)
    arm = np.asarray(results["arm"], dtype=np.float32)
    efforts = np.asarray(results["mean_effort"], dtype=np.float32)
    attempts = np.asarray(results["attempts"], dtype=np.int16)
    stim_count = np.asarray(results["stim_count"], dtype=np.int16)

    return {
        "patient_id": np.arange(start, start + count, dtype=np.int32),
        "age": ages,
        "traits": traits,
        "lesion": lesions_by_patient,
        "plan_target": targets,
        "plan_kind": kinds,
        "plan_offset_ms": np.asarray([plan["offset_ms"] for plan in plans], dtype=np.float32),
        "plan_intensity": plan_intensity,
        "plan_sessions_per_day": plan_sessions,
        "plan_attempts_per_session": np.asarray(
            [plan["attempts_per_session"] for plan in plans], dtype=np.int16),
        "plan_start_day": np.asarray([plan["start_day"] for plan in plans], dtype=np.int8),
        "arm0": arm0,
        "arm": arm,
        "efforts": efforts,
        "attempts": attempts,
        "stim_count": stim_count,
        "features": _features(
            arm0, arm, efforts, attempts, lesions_by_patient, ages,
            plan_sessions, targets, plan_intensity, days),
        "_region_names": np.asarray(names, dtype="U32"),
        "_trait_names": np.asarray(TRAIT_NAMES, dtype="U32"),
        "_feature_names": np.asarray(FEATURE_NAMES, dtype="U40"),
    }


def _chunk_directory(output_path: Path) -> Path:
    return output_path.parent / f"{output_path.stem}_chunks"


def _atomic_savez(path: Path, **arrays) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, **arrays)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_chunk(path: Path, *, start: int, count: int, patients: int,
                chunk_size: int, days: int, seed: int) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as saved:
            meta = (int(saved["schema_version"]), int(saved["start"]),
                    int(saved["count"]), int(saved["patients"]),
                    int(saved["chunk_size"]), int(saved["days"]), int(saved["seed"]))
            expected = (SCHEMA_VERSION, start, count, patients, chunk_size, days, seed)
            if meta != expected:
                raise ValueError(f"{path} was created with different run settings")
            data = {key: saved[key].copy() for key in CHUNK_ARRAYS}
            for name in ("_region_names", "_trait_names", "_feature_names"):
                data[name] = saved[name].copy()
            return data
    except (KeyError, OSError, ValueError) as exc:
        raise ValueError(f"Cannot resume from population checkpoint {path}: {exc}") from exc


def _write_chunk(path: Path, arrays: dict[str, np.ndarray], *, start: int, count: int,
                 patients: int, chunk_size: int, days: int, seed: int) -> None:
    _atomic_savez(
        path, **arrays,
        schema_version=np.asarray(SCHEMA_VERSION),
        start=np.asarray(start),
        count=np.asarray(count),
        patients=np.asarray(patients),
        chunk_size=np.asarray(chunk_size),
        days=np.asarray(days),
        seed=np.asarray(seed),
    )


def _combine_chunks(chunks: list[dict[str, np.ndarray]]) -> dict[str, np.ndarray]:
    combined = {
        key: np.concatenate([chunk[key] for chunk in chunks], axis=0)
        for key in CHUNK_ARRAYS
    }
    for name in ("_region_names", "_trait_names", "_feature_names"):
        combined[name] = chunks[0][name]
    return combined


def _tiger_writer() -> Optional[Callable]:
    try:
        module = importlib.import_module("storage.tiger")
    except ModuleNotFoundError as exc:
        if exc.name in ("storage", "storage.tiger"):
            log.info("Tiger uploader is not implemented yet; saving population data locally")
        else:
            log.warning("Tiger uploader could not load a dependency (%s); saving locally", exc)
        return None
    except ImportError as exc:
        log.warning("Tiger uploader could not be imported (%s); saving locally", exc)
        return None
    writer = getattr(module, "bulk_write_population", None)
    if writer is None:
        log.info("storage.tiger.bulk_write_population is not available; saving population data locally")
    return writer


def _tiger_rows(model, arrays: dict[str, np.ndarray], traits: np.ndarray,
                plans: list[dict], seed: int, days: int) -> tuple[list[dict], list[tuple]]:
    sessions = []
    daily_rows = []
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for local_index, patient_id in enumerate(arrays["patient_id"]):
        session_id = f"population-{seed}-{int(patient_id):06d}"
        lesion = {
            model.c.names[index]: float(value)
            for index, value in enumerate(arrays["lesion"][local_index])
            if value < 1.0
        }
        patient_traits = {
            name: float(value)
            for name, value in zip(TRAIT_NAMES, traits[local_index])
        }
        sessions.append({
            "session_id": session_id,
            "card": "R",
            "age": int(arrays["age"][local_index]),
            "lesion": lesion,
            "traits": patient_traits,
            "kind": "population",
        })
        plan = plans[local_index]
        for day_index in range(days):
            sim_day = day_index + 1
            daily_rows.append((
                base_time + timedelta(days=sim_day),
                session_id,
                sim_day,
                float(arrays["arm"][local_index, day_index]),
                float(arrays["efforts"][local_index, day_index]),
                int(arrays["attempts"][local_index, day_index]),
                float(0.25 + 0.75 * np.exp(-(sim_day - 1) /
                                           patient_traits["window_tau_days"])),
                int(arrays["stim_count"][local_index, day_index]),
                plan,
            ))
    return sessions, daily_rows


def _upload_chunk(writer: Callable, model, arrays: dict[str, np.ndarray],
                  *, seed: int, days: int, marker: Path) -> None:
    if marker.exists():
        return
    count = len(arrays["patient_id"])
    plans = []
    for i in range(count):
        target = str(arrays["plan_target"][i]).strip() or None
        kind = str(arrays["plan_kind"][i]).strip()
        if target is None:
            kind = ""
        elif not kind:
            kind = "excite" if target in EXCITE_TARGETS else "calm"
        plans.append({
            "target": target,
            "kind": kind,
            "offset_ms": float(arrays["plan_offset_ms"][i]),
            "intensity": float(arrays["plan_intensity"][i]),
            "sessions_per_day": int(arrays["plan_sessions_per_day"][i]),
            "attempts_per_session": int(arrays["plan_attempts_per_session"][i]),
            "start_day": int(arrays["plan_start_day"][i]),
        })
    sessions, daily_rows = _tiger_rows(
        model, arrays, arrays["traits"], plans, seed, days)
    try:
        writer(sessions, daily_rows)
    except Exception as exc:  # Tiger is an optional external dependency.
        log.warning("Tiger population upload failed for patients %s: %s",
                    arrays["patient_id"].tolist(), exc)
        return
    temporary = marker.with_name(marker.name + ".tmp")
    try:
        temporary.write_text("uploaded\n", encoding="ascii")
        os.replace(temporary, marker)
    finally:
        if temporary.exists():
            temporary.unlink()


def run_population(
    n_patients: int = DEFAULT_PATIENTS,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    days: int = DEFAULT_DAYS,
    seed: int = 0,
    output_path: Optional[Path | str] = None,
    model=None,
    upload_to_tiger: bool = True,
) -> Path:
    """Generate and checkpoint a synthetic cohort; resume matching completed chunks.

    Each batch uses an independent deterministic seed. Completed chunk files are
    retained beside the output, so interrupted runs can continue without
    resimulating saved patients.
    """
    if n_patients < 1 or chunk_size < 1 or days < 1:
        raise ValueError("n_patients, chunk_size, and days must all be positive")
    output = Path(output_path) if output_path is not None else (
        Path(__file__).resolve().parent / "data" / "population.npz")
    chunk_dir = _chunk_directory(output)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    if model is None:
        from brain.model import get_model
        model = get_model()
    tiger_upload = _tiger_writer() if upload_to_tiger else None

    starts = list(range(0, n_patients, chunk_size))
    loaded: dict[int, dict[str, np.ndarray]] = {}
    for start in starts:
        count = min(chunk_size, n_patients - start)
        path = chunk_dir / f"chunk_{start:08d}.npz"
        if path.exists():
            loaded[start] = _read_chunk(
                path, start=start, count=count, patients=n_patients,
                chunk_size=chunk_size, days=days, seed=seed)

    started_at = time.perf_counter()
    for start in starts:
        if start in loaded:
            continue
        count = min(chunk_size, n_patients - start)
        arrays = _simulate_chunk(model, start, count, days, seed)
        chunk_path = chunk_dir / f"chunk_{start:08d}.npz"
        _write_chunk(
            chunk_path, arrays, start=start, count=count, patients=n_patients,
            chunk_size=chunk_size, days=days, seed=seed)
        loaded[start] = arrays

        # Checkpoint the combined file only after the resumable chunk is durable.
        combined = _combine_chunks([loaded[index] for index in starts if index in loaded])
        _atomic_savez(
            output,
            **combined,
            schema_version=np.asarray(SCHEMA_VERSION),
            seed=np.asarray(seed),
            requested_patients=np.asarray(n_patients),
            completed_patients=np.asarray(sum(len(loaded[index]["patient_id"]) for index in loaded)),
            days=np.asarray(days),
        )

        completed = sum(len(loaded[index]["patient_id"]) for index in loaded)
        elapsed = time.perf_counter() - started_at
        rate = completed / elapsed if elapsed > 0 else 0.0
        eta = (n_patients - completed) / rate if rate > 0 else float("inf")
        log.info("Population progress: %d/%d patients (%.1f%%); ETA %.1f min",
                 completed, n_patients, 100.0 * completed / n_patients, eta / 60.0)

    combined = _combine_chunks([loaded[index] for index in starts])
    _atomic_savez(
        output,
        **combined,
        schema_version=np.asarray(SCHEMA_VERSION),
        seed=np.asarray(seed),
        requested_patients=np.asarray(n_patients),
        completed_patients=np.asarray(n_patients),
        days=np.asarray(days),
    )
    if tiger_upload is not None:
        for start in starts:
            arrays = loaded[start]
            _upload_chunk(
                tiger_upload, model, arrays, seed=seed, days=days,
                marker=chunk_dir / f"chunk_{start:08d}.uploaded")
    log.info("Population saved to %s (%d patients, %d simulated days)",
             output, n_patients, days)
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patients", type=int, default=DEFAULT_PATIENTS)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-tiger", action="store_true",
                        help="skip optional Tiger Data upload")
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run_population(
        args.patients,
        chunk_size=args.chunk_size,
        days=args.days,
        seed=args.seed,
        output_path=args.output,
        upload_to_tiger=not args.no_tiger,
    )


if __name__ == "__main__":
    main()
