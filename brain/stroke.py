"""Lesions + overcompensation (PERSON_A 7)."""
from __future__ import annotations
import numpy as np
from brain.connectome import Connectome


def apply_lesion(conn: Connectome, W_pre: np.ndarray, lesion: dict):
    """-> (health0 [76], health_max [76], W). Damaged white matter: in/out weights x (0.5+0.5h)."""
    h0 = np.ones(76, np.float32)
    W = W_pre.copy()
    for name, h in lesion.items():
        i = conn.idx[name]
        h0[i] = h
        s = 0.5 + 0.5 * h
        W[i, :] *= s
        W[:, i] *= s
    hmax = np.where(h0 < 1.0, h0 + 0.3 * (1.0 - h0), 1.0).astype(np.float32)
    return h0, hmax, W.astype(np.float32)


def apply_overcomp(conn: Connectome, b_pre: np.ndarray, k_base: float, overcomp: float, affected: str = "l"):
    """-> (b, k_ih). Healthy side inhibits the affected side more; healthy motor drive nudged up."""
    aff_flag = 0 if affected == "l" else 1
    k = np.full(76, k_base)
    k[conn.hemi == aff_flag] = k_base * overcomp
    b = b_pre.copy()
    healthy = "r" if affected == "l" else "l"
    for n in ("M1", "PMCDL"):
        b[conn.idx[healthy + n]] += 0.05 * (overcomp - 1.0)
    return b, k
