from types import SimpleNamespace
import logging

import numpy as np
import pytest

from ai import population


def test_random_plan_matches_schema_and_target_kind():
    rng = np.random.default_rng(42)
    plans = [population.random_plan(rng) for _ in range(500)]
    for plan in plans:
        assert plan["offset_ms"] in population.OFFSET_CHOICES
        assert plan["intensity"] in (0.5, 1.0)
        assert plan["sessions_per_day"] in (0, 1, 2, 3)
        assert plan["start_day"] in (1, 3, 7, 14)
        if plan["target"] is None:
            assert plan["kind"] == ""
        elif plan["target"] in population.EXCITE_TARGETS:
            assert plan["kind"] == "excite"
        else:
            assert plan["target"] in population.CALM_TARGETS
            assert plan["kind"] == "calm"


def test_missing_tiger_package_is_an_info_message(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=population.__name__)

    def missing_module(_name):
        raise ModuleNotFoundError("No module named 'storage'", name="storage")

    monkeypatch.setattr(population.importlib, "import_module", missing_module)
    assert population._tiger_writer() is None
    assert "saving population data locally" in caplog.text
    assert "WARNING" not in caplog.text


def test_population_checkpoints_resume_and_save_feature_shapes(tmp_path, monkeypatch):
    generated = []

    def fake_chunk(_model, start, count, days, _seed):
        generated.append(start)
        return {
            "patient_id": np.arange(start, start + count, dtype=np.int32),
            "age": np.full(count, 50, dtype=np.int16),
            "traits": np.ones((count, len(population.TRAIT_NAMES)), dtype=np.float32),
            "lesion": np.ones((count, 76), dtype=np.float32),
            "plan_target": np.full(count, "", dtype="U16"),
            "plan_kind": np.full(count, "", dtype="U8"),
            "plan_offset_ms": np.zeros(count, dtype=np.float32),
            "plan_intensity": np.ones(count, dtype=np.float32),
            "plan_sessions_per_day": np.zeros(count, dtype=np.int8),
            "plan_attempts_per_session": np.full(count, 20, dtype=np.int16),
            "plan_start_day": np.ones(count, dtype=np.int8),
            "arm0": np.full(count, 0.4, dtype=np.float32),
            "arm": np.full((count, days), 0.5, dtype=np.float32),
            "efforts": np.full((count, days), 0.7, dtype=np.float32),
            "attempts": np.zeros((count, days), dtype=np.int16),
            "stim_count": np.zeros((count, days), dtype=np.int16),
            "features": np.zeros((count, len(population.FEATURE_NAMES)), dtype=np.float32),
            "_region_names": np.asarray([f"r{i}" for i in range(76)], dtype="U32"),
            "_trait_names": np.asarray(population.TRAIT_NAMES, dtype="U32"),
            "_feature_names": np.asarray(population.FEATURE_NAMES, dtype="U40"),
        }

    monkeypatch.setattr(population, "_simulate_chunk", fake_chunk)
    output = tmp_path / "population.npz"
    model = SimpleNamespace()

    result = population.run_population(
        3, chunk_size=2, days=4, seed=9, output_path=output,
        model=model, upload_to_tiger=False,
    )
    assert result == output
    assert generated == [0, 2]
    with np.load(output, allow_pickle=False) as saved:
        assert int(saved["completed_patients"]) == 3
        assert saved["arm"].shape == (3, 4)
        assert saved["lesion"].shape == (3, 76)
        assert saved["features"].shape == (3, len(population.FEATURE_NAMES))

    population.run_population(
        3, chunk_size=2, days=4, seed=9, output_path=output,
        model=model, upload_to_tiger=False,
    )
    assert generated == [0, 2]


def test_resume_rejects_mismatched_chunk_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        population, "_simulate_chunk",
        lambda _model, start, count, days, _seed: {
            key: np.zeros((count, days) if key in ("arm", "efforts", "attempts", "stim_count")
                          else (count, 76) if key == "lesion"
                          else (count, len(population.TRAIT_NAMES)) if key == "traits"
                          else (count, len(population.FEATURE_NAMES)) if key == "features"
                          else count, dtype="U16" if key in ("plan_target", "plan_kind")
                          else np.float32)
            for key in population.CHUNK_ARRAYS
        } | {
            "_region_names": np.asarray([f"r{i}" for i in range(76)], dtype="U32"),
            "_trait_names": np.asarray(population.TRAIT_NAMES, dtype="U32"),
            "_feature_names": np.asarray(population.FEATURE_NAMES, dtype="U40"),
        },
    )
    output = tmp_path / "population.npz"
    model = SimpleNamespace()
    population.run_population(
        2, chunk_size=2, days=1, seed=4, output_path=output,
        model=model, upload_to_tiger=False,
    )
    with pytest.raises(ValueError, match="different run settings"):
        population.run_population(
            2, chunk_size=2, days=2, seed=4, output_path=output,
            model=model, upload_to_tiger=False,
        )


def test_tiger_upload_preserves_empty_kind_when_no_stim_is_planned(tmp_path):
    model = SimpleNamespace(c=SimpleNamespace(
        names=[f"r{i}" for i in range(76)]))
    arrays = {
        "patient_id": np.array([3]),
        "age": np.array([60]),
        "traits": np.array([[1.0, 1.1, 0.9, -80.0, 30.0, 0.8, 0.7]], dtype=np.float32),
        "lesion": np.ones((1, 76), dtype=np.float32),
        "plan_target": np.array([""], dtype="U16"),
        "plan_kind": np.array([""], dtype="U8"),
        "plan_offset_ms": np.array([0.0]),
        "plan_intensity": np.array([1.0]),
        "plan_sessions_per_day": np.array([1]),
        "plan_attempts_per_session": np.array([20]),
        "plan_start_day": np.array([1]),
        "arm": np.full((1, 2), 0.5),
        "efforts": np.full((1, 2), 0.7),
        "attempts": np.full((1, 2), 20),
        "stim_count": np.full((1, 2), 20),
    }
    captured = []
    population._upload_chunk(
        lambda sessions, daily: captured.append((sessions, daily)),
        model, arrays, seed=5, days=2, marker=tmp_path / "chunk.uploaded",
    )
    assert captured and captured[0][1][0][8]["kind"] == ""


def test_tiger_upload_failure_can_be_retried(tmp_path):
    model = SimpleNamespace(c=SimpleNamespace(
        names=[f"r{i}" for i in range(76)]))
    arrays = {
        "patient_id": np.array([3]),
        "age": np.array([60]),
        "traits": np.array([[1.0, 1.1, 0.9, -80.0, 30.0, 0.8, 0.7]], dtype=np.float32),
        "lesion": np.ones((1, 76), dtype=np.float32),
        "plan_target": np.array(["lPMCDL"]),
        "plan_kind": np.array(["excite"]),
        "plan_offset_ms": np.array([-80.0]),
        "plan_intensity": np.array([1.0]),
        "plan_sessions_per_day": np.array([2]),
        "plan_attempts_per_session": np.array([20]),
        "plan_start_day": np.array([3]),
        "arm": np.full((1, 2), 0.5),
        "efforts": np.full((1, 2), 0.7),
        "attempts": np.full((1, 2), 20),
        "stim_count": np.full((1, 2), 20),
    }
    marker = tmp_path / "chunk.uploaded"

    def failed_writer(_sessions, _daily):
        raise RuntimeError("database offline")

    population._upload_chunk(
        failed_writer, model, arrays, seed=5, days=2, marker=marker)
    assert not marker.exists()

    calls = []
    population._upload_chunk(
        lambda sessions, daily: calls.append((sessions, daily)),
        model, arrays, seed=5, days=2, marker=marker)
    assert marker.exists()
    assert len(calls) == 1
    sessions, daily = calls[0]
    assert sessions[0]["session_id"] == "population-5-000003"
    assert len(daily) == 2 and daily[0][0].utcoffset().total_seconds() == 0
