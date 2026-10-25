# NeuroNest dashboard (Person B)

Big-screen view of the live simulation: 3D brain (76 regions, real wiring), stroke lesion,
activity on each squeeze, rewiring edges, stimulation rings (blue excite / yellow calm),
the AI treatment target, seizure spread and treatment, right-arm function with an animated arm,
effort gauge and stimulation timing, day-90 forecast (digital twins), recovery curves with the
closing recovery window, and AI plan vs standard vs no-rehab comparison plus a treatment log.

## Run it (production)
```
python web/dashboard/dev_server.py --card A      # or B
```
- Dashboard: **http://localhost:8000/dashboard/** (F11 for fullscreen) · Phones: **http://<laptop-ip>:8000/phone/** (QR code at **/qr**)
- Webcam squeezes: double-click **`run_hands.bat`** (or `run_hands.bat 1` for a second camera). First run sets up `.venv-hands` with MediaPipe; the hand model is in `inputs/models/hand_landmarker.task`. Window keys: **C** recalibrate, **Space** manual squeeze, **Q** quit.
- Keyboard backup: `python inputs/keyboard.py`
- The Uno Q polls `/api/device` on the same server.

This is the **one server to run**. It runs Person A's brain and integrates Person C's modules, each optional and fail-soft:
`server/narrator.py` (ElevenLabs voice coach clips, committed in `server/audio_cache/`, plus Gemini narration when `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID` are set),
`storage/tiger.py` (Tiger Data logging when `TIGER_DATABASE_URL` is set, CSV buffer in `storage/offline/` otherwise), and the hands / keyboard / phone clients over `/ws/input`.
`server/app.py` is C's original standalone hub, kept for reference. Don't run both on port 8000.

## Controls
- Footer: patient A/B/random · Squeeze (or **Space**) · End day · Mode Human/AI · Fast-forward 90 days (standard / AI plan / no rehab) · Run AI planner · demo toggles (auto rehab, simulate seizure, AI treats seizures)
- Click a brain region → **Excite** / **Calm** (or keys **B** / **Y**) sends a stimulation via `/ws/input`
- Hover a region for its name, activity, health and seizure level

## Files
`index.html` · `style.css` · `app.js` (state → panels) · `brain3d.js` (three.js scene) · `charts.js` (recovery chart) ·
`regions.js` (region names) · `connectome.json` (static wiring from TVB) · `vendor/` (three.js r160, local copy so it works offline) · `dev_server.py`

No fast flashing anywhere (seizure pulse 1.2 Hz, rings fade over 0.8 s): photosensitivity safe.
