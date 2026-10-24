"""Load TVB connectivity_76. Convention: rows = targets, cols = sources (see DECISIONS.md).
If the data folder is missing, falls back to a SYNTHETIC connectome (with a warning) so
everything still runs on a machine without data/."""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
import numpy as np

log = logging.getLogger(__name__)
N, HALF = 76, 38

# Fallback only: real names are read from centres.txt
_FALLBACK_HALF = ["A1", "A2", "AMYG", "CCA", "CCP", "CCR", "CCS", "CCV", "FEF", "G", "HC", "IA",
                  "IP", "M1", "PCI", "PCIP", "PCM", "PCS", "PFCCL", "PFCDL", "PFCDM", "PFCM",
                  "PFCORB", "PFCPOL", "PFCVL", "PHC", "PMCDL", "PMCM", "PMCVL", "S1", "S2",
                  "TCC", "TCI", "TCPOL", "TCS", "TCV", "V1", "V2"]


def synthetic_names_centres(seed: int = 0):
    rng = np.random.default_rng(seed)
    names = ["r" + n for n in _FALLBACK_HALF] + \
        ["l" + n for n in _FALLBACK_HALF]
    base = rng.uniform([-70, 10, -10], [75, 60, 60], size=(HALF, 3))
    c = np.zeros((N, 3))
    c[:HALF] = np.c_[base[:, 0], -base[:, 1], base[:, 2]]
    c[HALF:] = np.c_[base[:, 0] +
                     rng.normal(0, 2, HALF), base[:, 1], base[:, 2]]
    return names, c


def load_names_centres(path: str | Path = "data/connectivity_76"):
    f = Path(path) / "centres.txt"
    if not f.exists():
        log.warning("%s not found: using SYNTHETIC names/centres", f)
        return synthetic_names_centres()
    names, cen = [], []
    for line in f.read_text().splitlines():
        p = line.split()
        if len(p) < 4:
            continue
        try:
            xyz = [float(v) for v in p[1:4]]
        except ValueError:
            continue  # header
        names.append(p[0])
        cen.append(xyz)
    assert len(names) == N, f"expected 76 regions, got {len(names)}"
    return names, np.array(cen, dtype=float)


@dataclass(frozen=True)
class Connectome:
    names: list
    # [76,76] float32, rows=target, cols=source, diag 0, max 1
    W0: np.ndarray
    S: np.ndarray             # [76,76] bool structural mask (W0 > 0)
    L: np.ndarray             # [76,76] mm
    D: np.ndarray             # [76,76] int delay steps (>= 1)
    D_bins: np.ndarray        # [76,76] int index into bin_delays
    bin_delays: np.ndarray    # [B] int delay (steps) per bin (B <= 4)
    W_bin_masks: np.ndarray   # [B,76,76] bool, S & (D_bins == b)
    centres: np.ndarray       # [76,3]
    homolog: np.ndarray       # [76] int
    hemi: np.ndarray          # [76] 0 = left (l*), 1 = right (r*)
    idx: dict


def load(path: str | Path = "data/connectivity_76", v_mm_per_ms: float = 5.0,
         dt_ms: float = 1.0) -> Connectome:
    p = Path(path)
    names, centres = load_names_centres(p)
    homolog = (np.arange(N) + HALF) % N
    for i in range(N):  # assert homologs by NAME
        assert names[i][1:] == names[homolog[i]][1:] and names[i][0] != names[homolog[i]][0], \
            f"homolog mismatch at {i}: {names[i]} vs {names[homolog[i]]}"
    hemi = np.array([0 if n.startswith("l") else 1 for n in names])

    if (p / "weights.txt").exists() and (p / "tract_lengths.txt").exists():
        W = np.loadtxt(p / "weights.txt").astype(np.float64)
        L = np.loadtxt(p / "tract_lengths.txt").astype(np.float64)
    else:
        log.warning("weights/tract_lengths missing: SYNTHETIC connectome")
        rng = np.random.default_rng(1)
        W = rng.choice([1.0, 2.0, 3.0], size=(N, N)) * \
            (rng.random((N, N)) < 0.27)
        dist = np.linalg.norm(centres[:, None] - centres[None], axis=-1)
        L = np.clip(dist, 5, 150)
    np.fill_diagonal(W, 0.0)
    W0 = (W / W.max()).astype(np.float32)
    S = W0 > 0
    D = np.clip(np.rint(L / v_mm_per_ms / dt_ms), 1, None).astype(int)

    qs = np.percentile(D[S], [12.5, 37.5, 62.5, 87.5])
    bin_delays = np.unique(np.rint(qs).astype(int))
    D_bins = np.argmin(np.abs(D[..., None] - bin_delays), axis=-1)
    masks = np.stack([S & (D_bins == b) for b in range(len(bin_delays))])
    return Connectome(names, W0, S, L, D, D_bins, bin_delays, masks, centres, homolog,
                      hemi, {n: i for i, n in enumerate(names)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    c = load()
    print("regions", len(c.names), "| edges",
          int(c.S.sum()), f"({c.S.mean():.0%})")
    print("delay bins (steps):", c.bin_delays, "| max D:", c.D.max(),
          "| lM1<-rM1 W:", c.W0[c.idx['lM1'], c.idx['rM1']])
