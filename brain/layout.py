"""LED layout (SHARED_CONTEXT 7.6). 8 rows (front=row 0) x 13 cols. Left hemi cols 0-5
(col 5 medial), divider col 6, right hemi cols 7-12 (col 7 medial)."""
from __future__ import annotations
from functools import lru_cache
import numpy as np
from brain.connectome import load_names_centres

ROWS, COLS, DIV = 8, 13, 6
_BANDS = (5, 5, 5, 5, 5, 5, 4, 4)  # 38 regions per hemisphere


@lru_cache(maxsize=1)
def _arrays():
    names, c = load_names_centres()
    rows, cols = np.zeros(76, int), np.zeros(76, int)
    for pre in ("l", "r"):
        idxs = sorted([i for i, n in enumerate(names) if n[0]
                      == pre], key=lambda i: -c[i, 0])
        assert len(idxs) == sum(
            _BANDS), f"hemisphere {pre} has {len(idxs)} regions"
        start = 0
        for r, size in enumerate(_BANDS):
            band = sorted(idxs[start:start + size],
                          key=lambda i: abs(c[i, 1]))  # medial first
            start += size
            n = len(band)
            offs = [0] if n == 1 else [
                int(round(k * 5 / (n - 1))) for k in range(n)]
            for i, off in zip(band, offs):
                rows[i], cols[i] = r, (5 - off if pre == "l" else 7 + off)
    cells = set(zip(rows.tolist(), cols.tolist()))
    assert len(cells) == 76, "LED cell collision"
    return names, rows, cols


def led_layout() -> dict:
    names, rows, cols = _arrays()
    return {n: (int(rows[i]), int(cols[i])) for i, n in enumerate(names)}


def led_string(activity, health, stim_region=None, t_ms: float = 0.0) -> str:
    """104 chars '0'..'9', row-major, row 0 first."""
    names, rows, cols = _arrays()
    a, h = np.asarray(activity, float), np.asarray(health, float)
    d = np.minimum(9, np.rint(9 * np.clip(a * h * 1.2, 0, 1))).astype(int)
    d = np.where(h < 0.5, np.minimum(d, 1), d)
    grid = np.zeros((ROWS, COLS), int)
    grid[:, DIV] = 1
    grid[rows, cols] = d
    if stim_region in names:
        i = names.index(stim_region)
        grid[rows[i], cols[i]] = 5 if int(
            t_ms // 333) % 2 == 0 else 9  # <= 1.5 Hz
    return "".join(map(str, grid.ravel()))


if __name__ == "__main__":
    names, rows, cols = _arrays()
    grid = [["  .  "] * COLS for _ in range(ROWS)]
    for r in range(ROWS):
        grid[r][DIV] = " | "
    for i, n in enumerate(names):
        grid[rows[i]][cols[i]] = n[:5].center(5)
    print("FRONT (top)   [left hemisphere | right hemisphere]")
    for r in range(ROWS):
        print("".join(grid[r]))
    print("BACK (bottom)")
    s = led_string(np.full(76, .5), np.ones(76), "lM1", 0.0)
    assert len(s) == 104 and set(s) <= set("0123456789")
    print(s)
