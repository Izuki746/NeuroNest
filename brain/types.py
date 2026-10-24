"""Shared dataclasses (SHARED_CONTEXT 7.3). Real and fake sessions both import these."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class AttemptResult:
    t_ms: float
    effort: float
    frames: np.ndarray            # [F, 76] activity every 20 ms (F ~ 35)
    frame_dt_ms: float            # 20.0
    arm_output: float             # 0..1.2, peak motor response this attempt
    arm_function: float           # current arm function estimate, 0..1.2
    # nearest stim minus onset (negative = before)
    stim_offset_ms: float | None
    stim_verdict: str | None      # "good" | "early" | "late" | "wrong_region" | None
    gate: float                   # plasticity gate multiplier (>= 1)
    attempts_today: int
    day: int
    weight_change: np.ndarray     # [76,76] W_now - W_start (float32)


@dataclass
class DayResult:
    day: int
    arm_function: float
    mean_effort: float
    attempts: int
    window_level: float
    stim_count: int
