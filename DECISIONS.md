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
- 2026-10-24 20:58 UTC · A · Twins receive public scan/session state and shared individual wiring only; their normalization baseline uses neutral model parameters, not the patient's hidden overcompensation.
- 2026-10-24 20:58 UTC · A · Twin updates use three representative attempts with a twin-only 10 ms engine step to meet the <2 s target; until a trained surrogate exists, day-90 forecasts use a particle-weighted compact recovery projection rather than full 90-day simulations.
- 2026-10-24 21:21 UTC · A · Forecaster and surrogate are trained on the 1,000 currently completed synthetic patients (80/20 held-out forecaster split); metrics explicitly state synthetic-only scope and observed quantile coverage.
- 2026-10-24 21:21 UTC · A · Planner verifies the top three candidates for seven days on 20 matched twins, then projects to day 90 with the surrogate to fit the 10 s budget. On current training data both demo cards select three daily practice sessions without stimulation; the expected card-specific stimulation plans remain an unmet acceptance target, so cached plans are not presented as clinically optimal.
- 2026-10-24 · B · .gitattributes: ai/models/** kept byte-exact (Windows CRLF conversion corrupted LightGBM models).
- 2026-10-24 · B · Gemini model default gemini-3.8-flash (2.5-flash retired for new keys); report_gen falls back across models.
- 2026-10-24 · B · dev_server resolves fast_forward plan names (ai/standard/none) to plan dicts per contract §7.4 before calling Session.
- 2026-10-25 · B (for A) · fast_forward REP_ATTEMPTS 3→1 (same day-90 results), TWIN_EVERY_DAYS 5→10, twins 150→40 particles: 90-day skip ~85 s → ~34 s. 46/46 tests pass.
- 2026-10-25 · B (for A) · planner cache moved from ai/models/plans_*.json (tracked) to ai/cache/ (git-ignored) so running the planner no longer modifies tracked files.
- 2026-10-25 · B (for A) · ROOT CAUSE of 'planner never picks stimulation': (1) BatchCohort.particles used one shared M_base, so high-overcomp patients (card A: 0.115 vs 1.13) read ~10x too weak; now per-particle M_base from each particle's own overcomp. (2) twin engine dt=10 ms (= tau) made high-overcomp twins collapse to ~4%; now dt=4 ms (24 particles to compensate).
- 2026-10-25 · B (for A) · planner verifies the shortlist by simulating it on twins for the full 90 days (6 twins) and builds dashboard curves from that simulation (was 7 days + surrogate projection); curves capped at 100%; plan cache versioned (PLAN_CACHE_VERSION). Card A -> calm rM1 -40 ms (49% vs 24% standard), card B -> excite lPMCDL -80 ms. 46/46 tests pass.
- 2026-10-25 · B · Merged C's experience branch. Production server = web/dashboard/dev_server.py with C's narrator (ElevenLabs coach clips + Gemini narration), Tiger storage, hands/keyboard/phone input handling (hello, attempt_onset, calibration, rtt), /qr, /api/stats, /phone, /audio, calibrate/report controls, AI-mode spoken cue + learned lag, per-LED 3 Hz limiter. server/app.py kept for reference. Planner/fast-forward errors can no longer leave the server stuck busy. 31/31 end-to-end checks pass.
