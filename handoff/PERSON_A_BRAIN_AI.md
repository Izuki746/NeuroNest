# PERSON A: Brain + AI brief

> **For Claude:** your human is **Person A**. They own `brain/` and `ai/`. **First read `handoff/SHARED_CONTEXT.md` in full**: it has the project idea, hardware, team split and the interface contract (Section 7) you must honour exactly. This brief tells you what to build, in what order, with the exact maths and acceptance tests. Write the code, explain briefly what you did, run it, and show your human the result. When a design choice isn't covered, pick the simplest option that satisfies the acceptance tests and log it in `DECISIONS.md`.

---

## 0. Context recap (60 seconds)
- **Project:** *Lightswitch: Rewire*, a stroke-recovery flight simulator for OxHack 2026 (theme **TIME**). A virtual human brain (76 regions, The Virtual Brain's `connectivity_76`) gets a stroke. Webcam hand squeezes (with an **effort** score) are movement attempts. Timed 🔵 excite / 🟡 calm stimulation from phones. Connections rewire, the arm recovers over simulated days. Digital twins + forecaster + planner make up the AI.
- **You are the engine.** Person C's server imports your `Session` and the AI functions; Person B's Uno Q displays your `led_string()`. **If your API matches Section 7.3, nobody is blocked.**
- **Main prize you carry:** HRT (prediction + decision-making). Make the AI rigorous, honest and visual: calibrated uncertainty, held-out accuracy, a clear comparison with baselines.
- **Hacking ends 12:00 Sunday.** Feature freeze at 11:00.

## 1. Deliverables (in order)
| # | File(s) | What | Due |
|---|---|---|---|
| 1 | `brain/fake.py` | `FakeSession` with the exact Section 7.3 API, plausible fake data | **first 45 min** (unblocks C) |
| 2 | `brain/connectome.py`, `brain/layout.py` | Load data, names, homologs, delays; LED layout + `led_string` | +1 h (unblocks B/C) |
| 3 | `brain/model.py`, `brain/stroke.py`, `brain/patients.py` | Simulator + stroke + patient cards | **CP1 ~17:00** |
| 4 | `brain/plasticity.py`, `brain/session.py` | Rewiring, stim gate, days, real `Session` | **CP2 ~21:00** |
| 5 | `brain/model.py` batched mode | Batched + fast mode for P patients at once | ~23:00 |
| 6 | `ai/population.py` | Simulate thousands of patients → Tiger Data + local parquet/npz | start before 01:00, runs overnight |
| 7 | `ai/twins.py` | Particle-filter digital twins + forecast band | **CP4 ~06:00** |
| 8 | `ai/forecaster.py` | LightGBM quantile forecaster + proportional baseline + metrics JSON | CP4 |
| 9 | `ai/planner.py` | Surrogate-based plan search + sim verification + 3 curves | **CP5 ~10:00** |
| 10 | Tuning | Cards A and B → different best plans; good-vs-bad timing visible; numbers for the pitch | by 10:30 |
| 11 | Stretch | Brain on the Uno Q; post-stroke epilepsy plug-in | only if all done |

## 2. Setup
```bash
cd lightswitch
python -m venv .venv && .venv\Scripts\activate
pip install numpy scipy scikit-learn lightgbm python-dotenv psycopg[binary] pandas pyarrow
# optional speed-up later: pip install numba
```
Data is already in `data/connectivity_76/`: `weights.txt` (76×76), `tract_lengths.txt` (76×76, mm), `centres.txt` (name x y z), `areas.txt`, `cortical.txt` (all 1), `average_orientations.txt`.

**Verified facts about the data (don't rediscover them):**
- `weights` max = 3.0; ~27% non-zero; **asymmetric** (max |W − Wᵀ| = 3.0); **diagonal is non-zero → zero it**. Values look like categorical strengths (0–3) plus small inter-hemispheric values (e.g. lM1↔rM1 ≈ 0.0046).
- `tract_lengths`: 4.93–153.5 mm.
- Axes: **x = front(+)/back(−)**, **y = left(+)/right(−)**, **z = up/down**. E.g. lM1 (6.1, 34.2, 35.3), rM1 (8.0, −32.8, 38.7), rV1 (−70.4, …), lPFCPOL (77.9, …).
- Names: `r*` = indices 0–37, `l*` = 38–75, in the same order, so the **homolog of index i is (i+38) % 76**. Assert this by name.
- Key regions: `xM1` (primary motor), `xPMCDL`/`xPMCVL`/`xPMCM` (premotor dorsolateral/ventrolateral/medial ≈ SMA), `xPFCDL` (dorsolateral prefrontal = planning), `xS1`/`xS2` (somatosensory), `xPCS` (superior parietal), `xFEF`.
- Orientation: the TVB convention is that node *i* receives `Σ_j weights[i, j] · x_j`, i.e. **rows = targets, columns = sources**. Check quickly (e.g. web search "the virtual brain connectivity weights orientation"); if it's unclear, use rows = targets and note it in `DECISIONS.md`. lPMCDL↔lM1 = 2.0 in both directions, so the motor pathway exists either way.

## 3. `brain/connectome.py`
```python
@dataclass(frozen=True)
class Connectome:
    names: list[str]          # 76
    W0: np.ndarray            # [76,76] float32, rows=target, cols=source, diag=0, normalised so max=1
    L: np.ndarray             # [76,76] mm
    D: np.ndarray             # [76,76] int delay steps = clip(round(L / v / dt_ms), 1, None), v = 5 mm/ms
    centres: np.ndarray       # [76,3]
    homolog: np.ndarray       # [76] int
    hemi: np.ndarray          # [76] 0=left(l*), 1=right(r*)
    idx: dict[str,int]
def load(path="data/connectivity_76", v_mm_per_ms=5.0, dt_ms=1.0) -> Connectome
```
- Normalise W0 by its max (→ 0..1). Keep a mask `S = W0 > 0` (structural connections: rewiring may only change existing edges).
- **Delay bins (for speed):** also provide `D_bins`: quantise delays into **4 bins** (e.g. 3, 8, 16, 28 ms, chosen by quantiles of D over S) and `W_bin_masks[b]` (which edges are in bin b). The fast engine computes `input = Σ_b (W ⊙ M_b) @ r(t − d_b)`: 4 mat-vecs instead of a 76×76 gather.

## 4. `brain/layout.py` (ship early: B and C depend on it)
Implement Section 7.6 exactly.
```python
def led_layout() -> dict[str, tuple[int,int]]   # deterministic, cached
def led_string(activity, health, stim_region=None, t_ms=0.0) -> str   # 104 chars
```
- Per hemisphere: sort 38 regions by x (front first), split into 8 row-bands (5,5,5,5,5,5,4,4), sort within a band by |y| (most medial nearest col 6). Left → cols 5,4,3,2,1,0 (medial→lateral); right → cols 7..12. If a band has more regions than 6 columns, spill into the nearest row with space.
- Char per LED: unused cells '0'; divider col 6 = '1'; region = `str(min(9, round(9 * clip(activity*health*1.2, 0, 1))))`; if `health < 0.5`, cap at '1'; stimulated region = `'5'` or `'9'` alternating every 333 ms based on `t_ms` (≤1.5 Hz, never faster).
- Self-test: print the 8×13 grid with region abbreviations so a human can sanity-check that the front is at the top and left is on the left.

## 5. `brain/fake.py` (ship FIRST)
`FakeSession` with every method/field in Section 7.3. Activity = smooth random noise plus a travelling bump from PFCDL→PMC→M1 on each attempt. `arm_function` rises slowly with attempts, `forecast` narrows over days, `plan_for` returns a static plan with three made-up curves. Return the same dataclasses as the real code (define them in `brain/types.py` and import them in both). Commit and tell your human to tell C: "fake session is on main".

## 6. The simulator: `brain/model.py`

### 6.1 Equations (rate model, dt = 1 ms)
For region *i* at step *t*:
```
syn_i   = G · Σ_j W_ij · h_j · r_j(t − D_ij)                       # delayed input from other regions
ih_i    = − k_ih[i] · h_hom · r_hom(t − D_ih)                       # hemispheric inhibition from homolog
u_i     = syn_i + ih_i + b_i + I_attempt_i(t) + I_stim_i(t) + σ·ξ_i(t)
r_i    ← r_i + (dt/τ) · ( −r_i + S(u_i) )
S(u)    = 1 / (1 + exp(−(u − θ)/κ))
output of region i used by others = h_i · r_i
```
**Starting parameters (tune to hit the targets in 6.3):**
| Param | Value | Meaning |
|---|---|---|
| τ | 10 ms | activity time constant |
| G | 0.9 | global coupling (with W0 max-normalised) |
| θ, κ | 1.0, 0.18 | sigmoid threshold/steepness |
| b_i | chosen so rest r ≈ 0.05 (solve for it, or set −0.2 and tune) | baseline drive |
| k_ih base | 0.35 | hemispheric inhibition |
| σ | 0.03 | noise (0 in deterministic tests) |
| v | 5 mm/ms | conduction speed |
| dt | 1 ms live; 2 ms allowed in fast mode | |

### 6.2 Movement attempt + arm readout
- The **affected hemisphere** = the lesioned one (default left, prefix `l`).
- **Attempt input:** half-sine pulse, duration 250 ms, into `{xPFCDL: 1.0, xPMCM: 0.6}` with amplitude `A = 1.6 · (0.25 + 0.75·effort)` (effort 0 still gives a weak attempt).
- **Simulated window per attempt:** from onset −250 ms to onset +450 ms = 700 steps, preceded by a 100 ms warm-up from the current resting state.
- **Motor readout:** `M(t) = Σ_m c_m · h_m · (r_m(t) − r_m_rest)`, with `c = {xM1: 1.0, xPMCDL: 0.35, xPMCVL: 0.2, xPMCM: 0.25}` on the affected side. `arm_output = max_t M(t) / M_base`, where `M_base` = the same patient's **pre-stroke** peak at effort = 1, no stim. Clip to [0, 1.2].
- **arm_function (the measurement):** a **standard test attempt** (effort 1, no stim, no plasticity, noise 0) → peak M / M_base. Computed at day end (and after each live attempt, cheaply, for display).

### 6.3 Behaviour targets (write these as tests in `tests/test_model.py`)
1. Rest: all r in [0.02, 0.15], no runaway over 5 s of simulated time.
2. Pre-stroke attempt at effort 1: lM1 peak r in [0.5, 0.85]; activity returns to within 0.05 of rest by +450 ms.
3. **Sequence:** the PFCDL peak happens before the PMCDL peak, which happens before the M1 peak (shows propagation and delays).
4. Effort 0.3 gives a clearly smaller arm_output than effort 1.0 (ratio < 0.7).
5. **Stroke (card A) drops arm_function to 0.2–0.4; card B to 0.35–0.55.**
6. **Diaschisis:** after the lM1 lesion, at least 3 non-lesioned left regions show a ≥20% drop in attempt-evoked peak.
7. Overcompensation: with overcomp 1.6, calming rM1 during the attempt raises arm_output by ≥10% vs no stim.

### 6.4 Engines
- `simulate_attempt(state, patient, effort, stims, plasticity_on) -> (frames, arm_output, eligibility)`: single patient, dt = 1 ms, exact delays with a ring buffer (`buf[(t − D) % Lbuf, j]`). For live use. **Target < 40 ms per attempt** on the laptop.
- `BatchEngine` (fast mode): P patients at once, arrays `[P,76]`, per-patient `W [P,76,76]`, **4 delay bins**, dt = 2 ms, window 500 ms. Uses `np.einsum('pij,pj->pi')` per bin, or `np.matmul` with `[P,76,76] @ [P,76,1]`. **Target: P=200, one attempt in < 0.6 s.** If too slow, try `numba` `@njit(parallel=True)` loops, or reduce to P=100.

## 7. `brain/stroke.py` + `brain/patients.py`
```python
@dataclass
class Patient:
    card: str; age: int; seed: int
    health0: np.ndarray         # [76] initial health after stroke (1 = healthy)
    health_max: np.ndarray      # health ceiling for spontaneous recovery = h0 + 0.3·(1 − h0) for lesioned regions, 1 elsewhere
    traits: dict                # plasticity, overcomp, stim_sens, best_offset_ms, window_tau_days, adherence, effort_mean
    W: np.ndarray               # [76,76] this patient's current weights
    W_pre: np.ndarray           # this patient's pre-stroke weights (W0 × individual variation)
    k_ih: np.ndarray            # [76] hemispheric inhibition per region
    day: int = 0
def make_card(card: str, seed: int) -> Patient       # "A","B" from SHARED_CONTEXT §3; "R" random
def sample_random_patient(rng) -> Patient            # used by population + twins priors
```
- **Individual wiring:** `W_pre = W0 · exp(N(0, 0.15))` on existing edges only (fixed by seed).
- **Lesion:** set `health0[region] = h`; also scale outgoing **and** incoming weights of lesioned regions by `(0.5 + 0.5·h)` to represent damaged white matter.
- **Overcompensation:** `k_ih[affected side] = k_base · overcomp`; also add `+0.05·(overcomp − 1)` to the baseline drive `b` of the healthy side's motor regions (rM1, rPMCDL).
- **Random lesion distribution (R and population):** choose 1–3 regions from `{lM1 (p=0.5), lPMCDL, lPMCVL, lPMCM, lS1, lPCS, lS2}` with severities h ~ U(0.1, 0.8); lM1 is always included with probability 0.7. Traits: plasticity ~ LogNormal(0, 0.3) × age factor (age ~ U(40, 85); factor = 1.3 − 0.01·(age − 40)); overcomp ~ U(0.7, 1.8); stim_sens ~ LogNormal(0, 0.35); best_offset_ms ~ U(−150, 0); window_tau_days ~ 20 + 20·U·age-factor; adherence ~ U(0.5, 1.0); effort_mean ~ U(0.5, 0.95).

## 8. `brain/plasticity.py`

### 8.1 Eligibility (accumulated during each attempt window, only where S = True)
Traces with τ+ = τ− = 20 ms:
```
x_j ← x_j·e^(−dt/τ+) + r_j·dt            (pre-synaptic trace)
y_i ← y_i·e^(−dt/τ−) + r_i·dt            (post-synaptic trace)
E_ij += ( r_i · x_j  −  β · r_j · y_i ) · dt        β = 0.6   (causal LTP minus anti-causal LTD)
```
Subtract each region's resting rate first (use `r − r_rest`, clipped ≥ 0) so resting noise doesn't drive learning.

### 8.2 Weight update after each attempt
```
η_eff = η0 · plasticity · window(day) · effort_factor · gate
ΔW    = η_eff · E ⊙ S
W     = clip(W + ΔW, 0.2·W_pre, 3.0·W_pre)          (only existing edges; never create new ones)
η0 = 0.02 (tune so standard rehab gives ~ +15–25 percentage points over 90 days for card B)
window(day)   = 0.25 + 0.75 · exp(−day / window_tau_days)
effort_factor = 0.2 + 0.8 · effort
```
### 8.3 Stimulation timing gate (the TIME core)
For the stim nearest the attempt onset with offset Δ = t_stim − t_onset (ms):
```
if stim region is a valid motor-network target for its kind (see 8.4):
    if Δ ≤ +250:  gate = 1 + 2.5 · stim_sens · exp(−(Δ − best_offset)² / (2 · 90²))
    else:         gate = 1 ;  maladaptive: k_ih[affected motor regions] *= 1.003   (too late → strengthens wrong-side inhibition)
else: gate = 1
verdict: |Δ − best_offset| ≤ 60 → "good"; Δ < best_offset − 60 → "early"; Δ > best_offset + 60 → "late"; invalid target → "wrong_region"
```
(The verdict uses the **patient's true best_offset**. That's fine for live feedback since it's "the clinician's measurement". The twins must NOT read it.)

### 8.4 Direct stim effect on activity
- `excite`: `I_stim = +0.9 · intensity` to the target for 150 ms from t_stim.
- `calm`: `I_stim = −0.9 · intensity`.
- Valid targets: excite → affected-side `{PMCDL, PMCVL, PMCM, M1, S1}`; calm → healthy-side `{M1, PMCDL}`. Anything else: no gate (still affects activity, verdict `wrong_region`).
- So calming rM1 in an overcompensating patient releases lM1 through the dynamics, and exciting lPMCDL drives the surviving pathway, which raises eligibility. Both effects emerge from the model, plus the gate.

### 8.5 Daily updates (`end_day`)
- **Spontaneous recovery** (first ~30 days): `h ← h + 0.03·window(day)·(h_max − h)` for lesioned regions.
- **Use it or lose it:** if attempts today < 5: affected-side motor-network edges `W *= 0.996`.
- Measure `arm_function` (standard test attempt). Return `DayResult`. Push to twins (`s.twins.update`), and to Tiger through C's `storage.tiger` if a writer was injected (`Session(..., writer=None)`; never import network code at module level).

## 9. `brain/session.py`: the real `Session` (Section 7.3, exactly)
- Holds the `Patient` (true hidden traits), the live state (r, buffers), the stim buffer, counters, curve history, and a `TwinFilter`.
- `submit_attempt`: gathers stims within ±400 ms of the onset, runs `simulate_attempt` (dt = 1 ms) with plasticity on, applies 8.2/8.3, decimates frames to 20 ms, computes the cheap arm_function (reuse the cached standard-test result; recompute every 5 attempts), increments counters, auto-calls `end_day()` at 20 attempts, returns `AttemptResult`.
- `weight_change = W − W_pre_stroke_lesioned` (the post-lesion starting W) as float32.
- `fast_forward(days, plan)`: uses the **BatchEngine with P=1** (or the single engine with 3 representative attempts/day, scaling ΔW by attempts_per_day/3). Effort per attempt ~ N(effort_mean, 0.1) clipped; adherence = probability each session happens. Apply plan stims at `offset_ms` relative to each attempt. **Target: 90 days < 8 s.**
- `state()`: returns the fields of the Section 7.5 `state` that A owns: `patient, day, attempts_today, arm_function, arm_output, activity, health, timing, forecast, curve`. C merges in effort/mode/narration.
- `idle_frame(t_ms)`: resting activity + slow smooth noise (e.g. a sum of 3 low-frequency sines per region), cheap.
- AI mode: `s.planned_stim()` returns `{"region","kind","offset_ms","intensity"}` from the current plan (C schedules it relative to the coach cue).

## 10. `ai/population.py` (start it running before you sleep)
- Generates N patients with `sample_random_patient` and random plans (Section 7.4 schema: target from the valid lists ∪ {null}, kind matching the target side, offset ∈ {−200…+200 step 20}, intensity ∈ {0.5, 1.0}, sessions_per_day ∈ {0,1,2,3}, start_day ∈ {1,3,7,14}). Simulates 90 days in fast mode, **batched P=200**.
- Saves `ai/data/population.npz` (or parquet): traits, lesion vector (76 healths), plan, daily curve [90], efforts, plus derived features.
- Writes sessions + daily rows to Tiger via `storage.tiger.bulk_write_population(...)` (C provides it; if it's not available yet, save locally and upload later with a script).
- **Target:** ≥ 5,000 patients overnight (more is better). Log progress + ETA. Make it resumable (append chunks).

## 11. `ai/twins.py`: digital twins (particle filter)
```python
class TwinFilter:
    def __init__(self, session_info, n=150, seed=0)   # particles = candidate hidden traits (NOT the true ones)
    def update(self, day_result) -> None
    def forecast(self) -> dict                        # Section 7.3 format
```
- **Particles:** sample traits from the population prior (Section 7). The lesion is **known** (clinicians see the scan), so particles share the lesion and individual wiring but differ in hidden traits: plasticity, overcomp, stim_sens, best_offset_ms, window_tau_days.
- **Daily update:** replay the day's actual attempts (efforts, stim offsets/targets) on all particles with the BatchEngine (P = n, 3 representative attempts/day, scaled), get each particle's predicted arm_function ŷ_k. Weights `w_k ∝ w_k · N(y_obs; ŷ_k, σ_obs = 0.03)`. If ESS < n/2: systematic resample + jitter traits (5% log-normal). `n_alive` = ESS rounded.
- **Forecast:** project each particle (or the top 50 by weight) to day 90 under the current plan (the plan from the planner, else standard) using the **surrogate** from §12 if available (instant), else fast sim. Weighted p10/p50/p90 + curves.
- **Acceptance:** on card B with standard rehab, the band width (p90 − p10) at day 90 shrinks by ≥ 50% between day 1 and day 10, and the true outcome lies inside the band in ≥ 8/10 random seeds.
- **Performance:** one daily update with n=150 in < 2 s. During fast-forward, update only every 5 days to keep it quick.

## 12. `ai/forecaster.py`: ML forecaster + surrogate
Two models trained on the population:
1. **Forecaster (for HRT accuracy charts):** features = lesion (healths of the 7 motor-network regions + total lesion load), age, arm_function days 1–7, slope days 1–7, mean effort, adherence proxy, plan features → target = day-90 arm_function. **LightGBM quantile regression** α ∈ {0.1, 0.5, 0.9}. 80/20 split **by patient**.
2. **Surrogate (for the planner + twins projection):** features = hidden traits + lesion + plan + current day + current arm_function → day-90 arm_function (and optionally days 30/60 for curves). LightGBM regression (median) + quantiles.
- **Baselines:** proportional recovery `ŷ = f1 + 0.7·(1 − f1)` where f1 = day-1 arm_function (cap 1.0). Also "last value carried forward".
- **Metrics → `ai/models/metrics.json`:** MAE of each method, p10–p90 coverage (target ~80%), plus a calibration table. C shows these on the dashboard. **These are the numbers for the pitch.**
- Save models with `lightgbm.Booster.save_model` to `ai/models/`.

## 13. `ai/planner.py`
```python
def plan_for(session, budget_s=10.0) -> dict   # Section 7.3 format
```
1. Draw 64 trait samples from the twins (weighted).
2. Candidate plans: ~600 random from the schema + a grid around promising ones.
3. Score each with the **surrogate**: `score = mean(ŷ90) − 0.03·stim_dose − 0.5·max(0, 0.5 − p10(ŷ90))`, where stim_dose = sessions_per_day × attempts × intensity × (target ≠ null) / 40.
4. **Verify** the top 3 plans with the real fast simulator on 20 twins each; pick the best verified one.
5. Curves for `ai`, `standard`, `none` (fast sim on 20 twins → p10/p50/p90 per day).
6. `explanation`: one sentence from a template, e.g. *"Calm right M1: this patient's healthy side is over-inhibiting the damaged side; stimulating 40 ms before each movement is predicted to reach 71% vs 52% with standard rehab."* (C may send it to Gemini to rephrase.)
7. Cache results to `ai/models/plans_{card}.json` for cards A/B (demo fallback).
- **Acceptance:** card A's best plan target = rM1 (calm); card B's = lPMCDL (excite) with offset in [−120, −40]. If not, **tune the patients/model** (overcomp effect strength, stim_sens), not the planner.
- Runtime ≤ `budget_s` (default 10 s); show a progress event.

## 14. Tuning checklist (do this before 10:30)
- [ ] Rest is stable; propagation order PFCDL → PMC → M1 visible in the frames.
- [ ] Card A arm_function on day 1 ≈ 0.25–0.35; card B ≈ 0.40–0.50.
- [ ] Over 30 days: no rehab < standard < AI plan, with **visible gaps** (≥ 8 percentage points each).
- [ ] Good timing (Δ = best_offset) vs bad (Δ = +300) over 7 live-like days: good ≥ 1.5× improvement.
- [ ] Effort 0.9 vs 0.4 practice over 7 days: clearly different.
- [ ] Twin bands shrink; coverage OK.
- [ ] Forecaster beats proportional recovery on MAE (if not, report it honestly).
- [ ] Live attempt < 40 ms; fast-forward 90 days < 8 s; planner ≤ 10 s (or cached).

## 15. Pitch numbers you must hand to C by 10:30
- Forecaster MAE vs proportional rule (e.g. "0.06 vs 0.11"), and band coverage.
- Card A: AI plan vs standard vs none at day 90. Card B: same.
- "Twins narrowed the forecast from ±X to ±Y in 7 days."
- Number of simulated patients in Tiger Data.

## 16. Explaining it to judges (A's 40 seconds)
"Each patient's hidden traits (how plastic their brain is, how much the healthy side overcompensates, their best stimulation timing) are unknown. We start with 150 candidate twins and drop the ones that don't match each day's recovery, so the forecast band shrinks. Then the planner tests ~600 plans on a surrogate trained on N simulated patients, verifies the best few on the full simulator, and picks the best recovery for the least stimulation. Card A gets 'calm the healthy side', card B gets 'excite premotor 80 ms before movement'. Personalised, with uncertainty, and checked against the 70% rule."

## 17. Gotchas
- Zero the diagonal of W. Don't let rewiring create new edges.
- Ring-buffer length ≥ max delay + 1. Delays are integers ≥ 1.
- Use `float32` in batched engines for speed and memory (200×76×76×4 B ≈ 4.6 MB, fine).
- Never use wall-clock time (DST happens tonight); the server passes monotonic `t_ms`.
- Keep `Session` methods non-blocking and fast. The server calls them from an async loop; long jobs (planner, fast-forward) run via `asyncio.to_thread`, so make them thread-safe (no shared global RNG; use `np.random.Generator` per object).
- Determinism: everything seeded from `Session(seed)`.
- Twins must never read `patient.traits` (that's cheating). Add an assert or keep the twins in a separate object that only receives `info()` + `DayResult`s + attempt logs.

## 18. Stretch goals (only after CP5)
- **Brain on the Uno Q:** a numpy-only `simulate_attempt` runs on the Uno Q's Linux side (check `python3 -c "import numpy"` there with B). The laptop stays the authority; this is just a "runs on the board" demo mode.
- **Post-stroke epilepsy:** add a per-region excitability term; if rewiring pushes perilesional excitability too high (excess excite stim), trigger runaway activity → the planner penalises seizure risk. Mention in the pitch only if it works.
