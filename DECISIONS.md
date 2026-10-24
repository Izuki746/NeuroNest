# Decisions log

One line each: `time (UTC) · initials · decision / assumption`.

- 2026-10-24 · setup · Folder `inputs/` (not `io/`) to avoid shadowing Python's stdlib `io`.
- 2026-10-24 · setup · Uno Q polls `GET /api/device` over HTTP (stdlib only) instead of WebSockets.
- 2026-10-24 · setup · `inputs/hands.py` runs in its own venv (`.venv-hands`) because of MediaPipe/OpenCV dependency conflicts.
- 2026-10-24 · setup · Attempts are simulated 400 ms after onset so stims from -400..+400 ms are included.
- 2026-10-24 · setup · TVB connectivity_76: x = front/back, y = left(+)/right(-), z = up/down; r* = idx 0-37, l* = 38-75; weights asymmetric with non-zero diagonal (zero it).
- 2026-10-24 · B · Uno Q sketch Bridge `setLcd` takes one string `line1|line2|r|g|b` (internal to unoq/; /api/device contract unchanged).
- 2026-10-24 17:59 UTC · A · Cap plasticity weights at 1.2× pre-stroke values; 3.0× caused unstable simulated recovery and reversed the good-timing and rehab-vs-no-rehab tests.
- 2026-10-24 17:59 UTC · A · Measure each patient's pre-stroke arm baseline with their overcompensation traits so card A recovery is normalized to its own healthy state.
- 2026-10-24 19:24 UTC · A · Population chunks use independent deterministic seeds and local checkpoints; Tiger uploads are retried for saved chunks until successful.
