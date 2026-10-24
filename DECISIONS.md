# Decisions log

One line each: `time (UTC) · initials · decision / assumption`.

- 2026-10-24 · setup · Folder `inputs/` (not `io/`) to avoid shadowing Python's stdlib `io`.
- 2026-10-24 · setup · Uno Q polls `GET /api/device` over HTTP (stdlib only) instead of WebSockets.
- 2026-10-24 · setup · `inputs/hands.py` runs in its own venv (`.venv-hands`) because of MediaPipe/OpenCV dependency conflicts.
- 2026-10-24 · setup · Attempts are simulated 400 ms after onset so stims from -400..+400 ms are included.
- 2026-10-24 · setup · TVB connectivity_76: x = front/back, y = left(+)/right(-), z = up/down; r* = idx 0-37, l* = 38-75; weights asymmetric with non-zero diagonal (zero it).
- 2026-10-24 � B � Uno Q sketch Bridge `setLcd` takes one string `line1|line2|r|g|b` (internal to unoq/; /api/device contract unchanged).
