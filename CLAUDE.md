# Lightswitch: Rewire (OxHack 2026)

Stroke-recovery flight simulator: a virtual human brain (The Virtual Brain `connectivity_76`) gets a stroke; webcam hand squeezes with effort tracking + timed stimulation drive rewiring; digital twins, a forecaster and a planner predict and optimise recovery; shown on a three.js dashboard and an Arduino UNO Q (LED brain map, Grove LCD, servo arm). Theme: TIME. Hacking ends 12:00 Sunday 25 Oct 2026.

## Before doing anything
1. Read `handoff/SHARED_CONTEXT.md` (project, hardware, team, **interface contract in Section 7**).
2. Ask your human which person they are, then read their brief:
   - **A, Brain + AI:** `handoff/PERSON_A_BRAIN_AI.md` (owns `brain/`, `ai/`)
   - **B, Hardware:** `handoff/PERSON_B_HARDWARE.md` (owns `unoq/`)
   - **C, Experience:** `handoff/PERSON_C_EXPERIENCE.md` (owns `inputs/`, `storage/`, `server/`, `web/`, Devpost)

## Rules
- Don't change interfaces in SHARED_CONTEXT §7 without team agreement; if changed, update that file in the same commit.
- Stay in your person's folders; coordinate before editing others'.
- `main` must always run. Small commits. Never commit `.env` or venvs.
- Log assumptions/deviations in `DECISIONS.md` (timestamp + initials).
- Everything external (Tiger, Gemini, ElevenLabs, Uno Q) fails soft. Never use local wall-clock time (DST tonight); use `time.monotonic()` and UTC.
- No fast flashing in any visual (photosensitivity).
- Honesty: it's a simulator, not a medical device.
