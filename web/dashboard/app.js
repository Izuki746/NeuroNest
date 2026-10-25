// NeuroNest dashboard: connects to the server's /ws/state (SHARED_CONTEXT §7.5) and renders
// the live brain, arm function, effort/timing, forecast, seizures, recovery curves and treatments.
import { Brain3D } from './brain3d.js';
import { drawRecovery } from './charts.js';
import { regionName, regionShort } from './regions.js';

const $ = (id) => document.getElementById(id);
const host = location.host || 'localhost:8000';
const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';

const brain = new Brain3D($('brainStage'), { tooltip: $('tooltip'), labels: $('labels') });
let S = {};                 // latest state
let lastStim = null;        // last stim event, paired with the next attempt result
let arm = { peak: 0, t0: -1e9 };
let selected = null;
const logItems = [];

fetch('connectome.json').then((r) => r.json()).then((c) => brain.setConnectome(c)).catch(() => {});

// ---------- sockets ----------
let stateWs, inputWs, retry = 500;
function connect() {
  stateWs = new WebSocket(`${wsProto}://${host}/ws/state`);
  stateWs.onopen = () => { $('conn').classList.add('on'); retry = 500; };
  stateWs.onclose = () => { $('conn').classList.remove('on'); setTimeout(connect, retry); retry = Math.min(retry * 2, 5000); };
  stateWs.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === 'state') onState(m); else if (m.type === 'event') onEvent(m); else if (m.type === 'audio') play(m.url, m.id); };
  inputWs = new WebSocket(`${wsProto}://${host}/ws/input`);
  inputWs.onopen = () => inputWs.send(JSON.stringify({ type: 'hello', client: 'dashboard', id: 'dashboard' }));
}
connect();

function control(action, extra = {}) {
  fetch('/api/control', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type: 'control', action, ...extra }) })
    .catch(() => toast('Server not reachable'));
}
function stim(region, kind) {
  if (inputWs && inputWs.readyState === 1) inputWs.send(JSON.stringify({ type: 'stim', region, kind, intensity: 1.0, rtt_ms: 0, id: 'dashboard' }));
}
// Audio: exactly one voice at a time, from exactly one dashboard tab.
// - only the visible tab speaks (background tabs / extra windows stay silent)
// - the same clip is never played twice by different tabs (localStorage claim)
// - a new clip stops the previous one instead of talking over it
let currentAudio = null;
function play(url, id) {
  if (!url || document.visibilityState !== 'visible') return;
  const key = `nn_audio_${id ?? url}`;
  try {
    const claimed = Number(localStorage.getItem(key) || 0);
    if (Date.now() - claimed < 4000) return;               // another tab already played this one
    localStorage.setItem(key, String(Date.now()));
  } catch { /* private mode: fall back to visibility-only */ }
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  currentAudio = new Audio(url);
  currentAudio.play().catch(() => {});
}

// ---------- state ----------
function onState(s) {
  S = s;
  brain.update(s);
  if (s.edges) brain.setRewiring(s.edges);
  $('brainKind').textContent = s.brain ? `${s.brain} brain` : 'live';
  $('boardConn').classList.toggle('on', !!s.device_online);
  if (s.patient) {
    $('patientCard').textContent = s.patient.card;
    const les = Object.entries(s.patient.lesion || {}).map(([r, h]) => `${regionShort(r)} ${Math.round((1 - h) * 100)}% damaged`).join(', ');
    $('patientText').innerHTML = `${s.patient.age} years · ${les} · <span class="dim">${s.patient.affected_arm} arm affected</span>`;
  }
  $('day').textContent = s.day ?? '—';
  $('attempts').textContent = `${s.attempts_today ?? 0}/20`;
  $('mode').textContent = (s.mode || 'human').toUpperCase();
  document.querySelector('[data-mode]').textContent = `Mode: ${s.mode === 'ai' ? 'AI' : 'Human'}`;
  document.querySelector('[data-mode]').classList.toggle('active', s.mode === 'ai');
  if (s.dev) { $('autoBtn').classList.toggle('on', !!s.dev.auto); $('autoTreatBtn').classList.toggle('on', !!s.dev.auto_treat); }

  // arm function
  const af = s.arm_function ?? 0;
  $('armPct').textContent = Math.round(af * 100);
  $('armBar').style.width = `${Math.min(100, af * 100)}%`;
  $('armBar').style.backgroundPosition = `${100 - Math.min(100, af * 100)}% 0`;
  const c = s.curve && s.curve.arm && s.curve.arm.length > 1 ? s.curve.arm : null;
  $('armDelta').textContent = c ? `${(c[c.length - 1] - c[0]) >= 0 ? '+' : ''}${Math.round((c[c.length - 1] - c[0]) * 100)} pts since day ${s.curve.days[0]}` : '';

  // effort
  const ef = s.effort || {};
  $('effortPct').textContent = ef.last ? `${Math.round(ef.last * 100)}%` : '—';
  $('effortArc').style.strokeDashoffset = 157 * (1 - Math.min(1, ef.last || 0));
  $('effortRolling').textContent = ef.rolling ? `average ${Math.round(ef.rolling * 100)}%` : 'average —';
  $('fatigue').hidden = !ef.fatigue;

  // timing
  const tm = s.timing;
  const mark = $('timingMark');
  if (tm && tm.offset_ms != null) {
    const off = Math.max(-400, Math.min(400, tm.offset_ms));
    mark.style.left = `${50 + (off / 400) * 50}%`; mark.style.opacity = 1;
    mark.classList.toggle('calm', !!(lastStim && lastStim.kind === 'calm'));
    const txt = { good: 'Perfect timing ✓', early: `${Math.abs(tm.offset_ms)} ms too early`, late: `${Math.abs(tm.offset_ms)} ms too late`, wrong_region: 'That region doesn\'t help movement' }[tm.verdict] || '';
    $('timingText').textContent = txt;
    $('timingText').className = `t-text ${tm.verdict || ''}`;
  } else { mark.style.opacity = 0.25; }

  // forecast
  const fc = s.forecast;
  if (fc) {
    $('fcP50').textContent = `${Math.round(fc.p50 * 100)}%`;
    $('fcRange').textContent = `range ${Math.round(fc.p10 * 100)}–${Math.round(fc.p90 * 100)}%`;
    $('twins').textContent = fc.n_alive != null ? `${fc.n_alive} twins left` : '';
    $('fcBand').style.left = `${Math.max(0, fc.p10 * 100)}%`;
    $('fcBand').style.width = `${Math.max(1, (fc.p90 - fc.p10) * 100)}%`;
    $('fcMid').style.left = `${Math.min(100, fc.p50 * 100)}%`;
  }

  renderSeizure(s.seizure);
  seizureStory(s.seizure);
  if (s.busy && !onState.wasBusy) setStory(s.busy.startsWith('Fast') ? 'Skipping ahead in time: watch the recovery curve grow day by day.' : 'The AI is testing hundreds of treatments on copies of this patient\'s brain…', '', 'WORKING', 1500, true);
  onState.wasBusy = !!s.busy;
  renderPlan(s.plan, s.plan_curves, s.plan_explanation);
  drawRecovery($('chart'), s);

  // busy + narration
  $('busy').hidden = !s.busy; if (s.busy) $('busy').textContent = s.busy;
  if (!onState.started) {                                     // page just loaded: whatever narration exists is old, don't replay it
    onState.started = true;
    onState.narrT = s.narration ? s.narration.t_ms : null;
  }
  if (s.narration && s.narration.t_ms !== onState.narrT) {
    onState.narrT = s.narration.t_ms;
    const n = $('narration'); n.textContent = s.narration.text; n.classList.add('show');
    clearTimeout(onState.narrTimer); onState.narrTimer = setTimeout(() => n.classList.remove('show'), 9000);
    if (s.narration.audio_url) play(s.narration.audio_url, `n${s.narration.t_ms}`);
  }
}

function renderSeizure(sz) {
  const card = $('szCard');
  if (!sz) {
    card.dataset.phase = 'none'; $('szBadge').textContent = 'stable';
    $('szText').textContent = 'No abnormal activity'; $('szRisk').style.width = '3%';
    $('szRegions').innerHTML = ''; $('szTx').textContent = 'Watching for post-stroke seizures near the damaged area.';
    return;
  }
  card.dataset.phase = sz.phase;
  $('szRisk').style.width = `${Math.round(sz.risk * 100)}%`;
  if (sz.phase === 'pre') {
    $('szBadge').textContent = 'predicted';
    $('szText').textContent = `Seizure likely in ~${sz.eta_s ?? '?'} s · risk ${Math.round(sz.risk * 100)}%`;
    $('szRegions').innerHTML = `<span>near ${regionName(sz.onset)}</span>`;
    $('szTx').textContent = 'Predicted from rising activity next to the stroke.';
  } else if (sz.phase === 'ictal') {
    $('szBadge').textContent = 'SEIZURE';
    const regs = Object.entries(sz.regions || {}).sort((a, b) => b[1] - a[1]);
    $('szText').textContent = `Seizure · ${regs.length} region${regs.length === 1 ? '' : 's'} · ${sz.elapsed_s ?? 0}s`;
    $('szRegions').innerHTML = regs.map(([r]) => `<span title="${regionName(r)}">${regionShort(r)}</span>`).join('');
    $('szTx').textContent = sz.treated ? `Treatment: ${sz.treatment} with yellow light (${sz.treated_by === 'ai' ? 'AI' : 'care team'})` : `Started in ${regionName(sz.onset)}, spreading along connections. Calm a red region to stop it.`;
  } else {
    $('szBadge').textContent = 'stopped';
    $('szText').textContent = 'Seizure stopped';
    $('szRegions').innerHTML = '';
    $('szTx').textContent = sz.treatment ? `Stopped by ${sz.treated_by === 'ai' ? 'the AI' : 'the care team'}: ${sz.treatment}.` : '';
  }
}

function renderPlan(plan, curves, expl) {
  const key = JSON.stringify([plan, expl, curves && curves.ai && curves.ai.p50.slice(-1)]);
  if (key === renderPlan.key) return; renderPlan.key = key;
  const el = $('plan');
  if (!plan) { el.innerHTML = '<div class="plan-empty">No plan yet. Press "Find best treatment".</div>'; return; }
  const end = (k) => (curves && curves[k] ? Math.round(curves[k].p50[curves[k].p50.length - 1] * 100) : '—');
  const col = plan.kind === 'calm' ? 'var(--calm)' : 'var(--excite)';
  const what = plan.target ? `${plan.kind === 'calm' ? 'Calm' : 'Excite'} ${regionName(plan.target)}` : 'Rehab without stimulation';
  const when = plan.target ? `${Math.abs(plan.offset_ms)} ms ${plan.offset_ms <= 0 ? 'before' : 'after'} each movement` : '';
  el.innerHTML = `
    <div class="plan-title"><span class="sw" style="background:${col};box-shadow:0 0 10px ${col}"></span>AI plan: ${what}</div>
    <div class="plan-meta">${when ? `<span>${when}</span>` : ''}<span>${plan.sessions_per_day ?? 1} session${plan.sessions_per_day === 1 ? '' : 's'}/day × ${plan.attempts_per_session ?? 20}</span><span>from day ${plan.start_day ?? 1}</span>${plan.target ? `<span>${plan.kind === 'calm' ? 'yellow light' : 'blue light'}</span>` : ''}</div>
    ${expl ? `<p class="plan-expl">${expl}</p>` : ''}
    <div class="outcomes"><div class="ai"><b>${end('ai')}%</b><small>AI plan · day 90</small></div>
      <div class="st"><b>${end('standard')}%</b><small>standard rehab</small></div>
      <div class="no"><b>${end('none')}%</b><small>no rehab</small></div></div>`;
}

// ---------- events → treatment log ----------
function onEvent(m) {
  const d = m.data || {};
  switch (m.kind) {
    case 'patient':
      logItems.length = 0; lastStim = null;
      addLog('note', `New patient ${d.card}: ${d.age} years, stroke in ${Object.keys(d.lesion || {}).map(regionShort).join(' + ')}`);
      setStory(`New patient ${d.card}: ${d.age} years old with a stroke in the <b>left motor area</b> (dark dots), so their <b>right arm is weak</b>. Squeeze your hand to start rehab.`, '', 'NEW PATIENT', 7000, true);
      break;
    case 'stim': lastStim = { ...d, t: performance.now() }; break;
    case 'attempt': {
      arm = { peak: Math.min(90, (d.arm_output || 0) * 90), t0: performance.now() };
      $('hud').classList.add('live');                                  // effort/timing panel: bright after a squeeze,
      clearTimeout(onEvent.hudTimer);                                  // fades back when idle so the brain stays the focus
      onEvent.hudTimer = setTimeout(() => $('hud').classList.remove('live'), 9000);
      const pct = Math.round((d.arm_output || 0) * 100);
      const through = (d.arm_output || 0) < 0.45 ? 'The stroke blocked most of it' : (d.arm_output || 0) < 0.7 ? 'More of it is getting through now' : 'Most of it gets through now: the brain has rewired';
      const stimTxt = { good: ' The stimulation was <b>perfectly timed</b>, so the brain rewires faster (white lines).',
        early: ` The stimulation came <b>${Math.abs(Math.round(d.offset_ms || 0))} ms too early</b>, so it helped less.`,
        late: ` The stimulation came <b>${Math.abs(Math.round(d.offset_ms || 0))} ms too late</b>, so it helped less.`,
        wrong_region: ' The stimulated region isn\'t part of the movement pathway, so it didn\'t help.' }[d.verdict] || '';
      setStory(`Squeeze (effort ${Math.round((d.effort || 0) * 100)}%): the "move" signal travelled planning → premotor → motor. ${through}, so the arm reached <b>${pct}%</b>.${stimTxt}`,
        d.verdict === 'good' ? 'good' : (d.verdict === 'early' || d.verdict === 'late') ? 'warn' : '', 'REHAB', 4500);
      if (d.offset_ms != null && lastStim) {
        const who = lastStim.actor === 'ai' ? '<span class="v ai">AI</span>' : '';
        addLog(lastStim.kind, `${lastStim.kind === 'calm' ? 'Calm' : 'Excite'} ${regionShort(lastStim.region)} · ${Math.abs(Math.round(d.offset_ms))} ms ${d.offset_ms < 0 ? 'before' : 'after'}${who}<span class="v ${d.verdict}">${(d.verdict || '').replace('_', ' ')}</span>`, d.day);
        lastStim = null;
      }
      break;
    }
    case 'treatment': addLog('calm', `${d.label || `${d.kind} ${d.region}`}${d.actor === 'ai' ? '<span class="v ai">AI</span>' : ''}`); break;
    case 'seizure':
      if (d.phase === 'start') addLog('seizure', `Seizure started in ${regionShort(d.onset)}`);
      else addLog('seizure', `Seizure stopped after ${d.duration_s}s${d.treatment ? ` · ${d.treatment}` : ''}`);
      break;
    case 'plan_ready':
      addLog('plan', `AI tested ${d.candidates_tested ?? 'many'} treatments → ${d.plan && d.plan.target ? `${d.plan.kind} ${regionShort(d.plan.target)}, ${Math.abs(d.plan.offset_ms)} ms before each squeeze` : 'normal rehab'}`);
      setStory(`The AI tested <b>${d.candidates_tested ?? 'hundreds of'} treatments</b> on copies of this brain. ${d.explanation || ''} Try <b>Skip ahead 90 days → AI plan</b> to see it.`, 'good', 'BEST TREATMENT FOUND', 9000, true);
      break;
    case 'fast_forward_done': {
      const name = { ai: 'the AI plan', standard: 'normal rehab', none: 'no rehab' }[d.plan] || d.plan;
      addLog('note', `Skipped ${d.days} days with ${name} → arm ${Math.round((d.arm_function || 0) * 100)}%`);
      setStory(`After ${d.days} more days with <b>${name}</b>, the right arm is at <b>${Math.round((d.arm_function || 0) * 100)}%</b>. Pick a patient again to compare another treatment.`, 'good', 'TIME SKIPPED', 9000, true);
      break;
    }
    case 'info': setStory(d.text || '', 'warn', 'NOTE', 5000, true); break;
    case 'day_end': addLog('note', `Day ${d.day} done · arm ${Math.round((d.arm_function || 0) * 100)}%`, d.day); break;
    case 'fatigue':
      addLog('note', 'Effort dropping: the patient is getting tired');
      setStory('Effort is dropping: the patient is getting tired. Weaker squeezes mean slower recovery, so a short break helps.', 'warn', 'FATIGUE', 6000, true);
      break;
    default: break;
  }
}

function addLog(kind, html, day) {
  logItems.unshift({ kind, html, day: day ?? S.day });
  logItems.length = Math.min(logItems.length, 40);
  $('log').innerHTML = logItems.slice(0, 5).map((x) => `<li class="${x.kind}"><i></i><span>${x.html}</span><span class="when">day ${x.day ?? '—'}</span></li>`).join('');
}

// ---------- "What's happening" caption ----------
// The caption appears, stays for a few seconds, then fades into a small "Latest update" pill.
// × hides it; clicking the pill brings the last message back. A dismissed message type
// (e.g. an ongoing SEIZURE) isn't forced back on screen until something new happens.
let storyHoldUntil = 0, storyHideTimer = 0, storyLast = '', dismissedLabel = '';
function showStory(on) {
  $('story').classList.toggle('show', on);
  $('storyPill').classList.toggle('show', !on && !!storyLast);
}
function setStory(text, tone = '', label = "WHAT'S HAPPENING", holdMs = 6000, force = false) {
  if (!force && performance.now() < storyHoldUntil) return;
  if (force && label === dismissedLabel && text === storyLast) return;
  const isNew = text !== storyLast;
  if (label !== dismissedLabel) dismissedLabel = '';
  $('story').dataset.tone = tone;
  $('storyPill').dataset.tone = tone;
  $('storyK').textContent = label;
  $('storyText').innerHTML = text;
  storyLast = text;
  storyHoldUntil = performance.now() + holdMs;
  if (isNew) {
    showStory(true);
    clearTimeout(storyHideTimer);
    storyHideTimer = setTimeout(() => showStory(false), Math.max(6500, holdMs + 4000));
  }
}
$('storyClose').addEventListener('click', () => { dismissedLabel = $('storyK').textContent; clearTimeout(storyHideTimer); showStory(false); });
$('storyPill').addEventListener('click', () => {
  dismissedLabel = ''; showStory(true);
  clearTimeout(storyHideTimer); storyHideTimer = setTimeout(() => showStory(false), 8000);
});
function toast(text) { setStory(text, '', "WHAT'S HAPPENING", 2500); }

function seizureStory(sz) {
  if (!sz) { if (seizureStory.was) { seizureStory.was = false; storyHoldUntil = 0; } return; }
  seizureStory.was = true;
  const where = regionName(sz.onset);
  if (sz.phase === 'pre') setStory(`The AI predicts a seizure in about <b>${sz.eta_s ?? '?'} s</b>, next to the stroke (${where}). Catching it early means it can be stopped before it spreads.`, 'warn', 'SEIZURE WARNING', 800, true);
  else if (sz.phase === 'ictal' && !sz.treated) setStory(`<b>Seizure!</b> Abnormal activity is spreading from ${where} along the brain's wiring (red). Click a red region and press <b>Calm</b> to stop it.`, 'seizure', 'SEIZURE', 800, true);
  else if (sz.phase === 'ictal') setStory(`${sz.treated_by === 'ai' ? 'The AI' : 'The care team'} is calming ${where} with <b>yellow light</b>. Watch the red fade as the seizure stops.`, 'seizure', 'TREATING SEIZURE', 800, true);
  else setStory(`Seizure stopped${sz.treatment ? ` by calming ${where}` : ''}. The brain is back to normal activity.`, 'good', 'SEIZURE STOPPED', 3000, true);
}

// ---------- "How it works" ----------
function showHelp(on) { $('help').hidden = !on; }
$('helpBtn').addEventListener('click', () => showHelp(true));
$('helpClose').addEventListener('click', () => showHelp(false));
$('help').addEventListener('click', (e) => { if (e.target.id === 'help') showHelp(false); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') showHelp(false); });
try { if (location.hash === '#help') showHelp(true); else if (location.hash !== '#nohelp' && !localStorage.getItem('nn_help_seen')) { showHelp(true); localStorage.setItem('nn_help_seen', '1'); } } catch { showHelp(true); }

// ---------- arm animation (mirrors the servo: rise, hold 0.3 s, ease down over 0.8 s) ----------
function animateArm() {
  const dt = performance.now() - arm.t0;
  const a = dt < 300 ? arm.peak : dt < 1100 ? arm.peak * (1 - (dt - 300) / 800) : 0;
  $('forearm').style.transform = `rotate(${-a}deg)`;
  requestAnimationFrame(animateArm);
}
animateArm();
window.addEventListener('resize', () => drawRecovery($('chart'), S));

// ---------- controls ----------
document.addEventListener('click', (e) => {
  // close any open dock menu when clicking outside it, or after choosing an option in it
  document.querySelectorAll('details.menu[open]').forEach((d) => { if (!d.contains(e.target) || e.target.closest('.menu-pop')) d.removeAttribute('open'); });
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.patient) control('new_patient', { card: b.dataset.patient });
  else if (b.dataset.ff) control('fast_forward', { days: 90, plan: b.dataset.ff });
  else if (b.dataset.ctl) control(b.dataset.ctl);
  else if (b.dataset.dev) control(b.dataset.dev, { on: !b.classList.contains('on') });
  else if (b.hasAttribute('data-mode')) control('set_mode', { mode: S.mode === 'ai' ? 'human' : 'ai' });
  else if (b.dataset.stim && selected) { stim(selected, b.dataset.stim); toast(`${b.dataset.stim === 'calm' ? 'Calm' : 'Excite'} ${regionShort(selected)}`); }
});
brain.onSelect = (name) => {
  selected = name;
  $('selectBar').hidden = !name;
  if (name) $('selName').textContent = regionName(name);
};
document.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); control('dev_attempt'); }
  if (selected && e.key.toLowerCase() === 'b') stim(selected, 'excite');
  if (selected && e.key.toLowerCase() === 'y') stim(selected, 'calm');
});

