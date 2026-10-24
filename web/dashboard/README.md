# NeuroNest dashboard (Person B)

Big-screen view of the live simulation: 3D brain (76 regions, real wiring), stroke lesion,
activity on each squeeze, rewiring edges, stimulation rings (blue excite / yellow calm),
the AI treatment target, seizure spread and treatment, right-arm function with an animated arm,
effort gauge and stimulation timing, day-90 forecast (digital twins), recovery curves with the
closing recovery window, and AI plan vs standard vs no-rehab comparison plus a treatment log.

## Run it now (before C's server exists)
```
python web/dashboard/dev_server.py --card A      # or B
```
Open **http://localhost:8000/dashboard/** (big screen: F11 for fullscreen).
The dev server runs Person A's brain (`brain.session.Session` if present, else `FakeSession`) and also
serves `/api/device`, so the Uno Q keeps working against it.

## With C's real server
C's server serves this folder at `/dashboard` and broadcasts `/ws/state` per SHARED_CONTEXT §7.5
(including the dashboard additions listed there). No dashboard changes are needed.

## Controls
- Footer: patient A/B/random · Squeeze (or **Space**) · End day · Mode Human/AI · Fast-forward 90 days (standard / AI plan / no rehab) · Run AI planner · demo toggles (auto rehab, simulate seizure, AI treats seizures)
- Click a brain region → **Excite** / **Calm** (or keys **B** / **Y**) sends a stimulation via `/ws/input`
- Hover a region for its name, activity, health and seizure level

## Files
`index.html` · `style.css` · `app.js` (state → panels) · `brain3d.js` (three.js scene) · `charts.js` (recovery chart) ·
`regions.js` (region names) · `connectome.json` (static wiring from TVB) · `vendor/` (three.js r160, local copy so it works offline) · `dev_server.py`

No fast flashing anywhere (seizure pulse 1.2 Hz, rings fade over 0.8 s): photosensitivity safe.
