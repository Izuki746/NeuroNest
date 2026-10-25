// Progress report page: GET /api/report → summary, recovery chart, day-by-day notes (Gemini or template).
import { regionShort } from './regions.js';

const $ = (id) => document.getElementById(id);
const pct = (x) => `${Math.round((x || 0) * 100)}%`;

async function load(force = false) {
  $('loading').hidden = false; $('error').hidden = true;
  ['top', 'chartBox', 'daysBox'].forEach((id) => { $(id).hidden = true; });
  try {
    const r = await fetch(`/api/report${force ? '?force=1' : ''}`);
    render(await r.json());
  } catch (e) {
    $('error').textContent = 'Could not reach the server. Is the dashboard server running?';
    $('error').hidden = false;
  } finally { $('loading').hidden = true; }
}

function render(rep) {
  const src = $('source');
  src.className = `pill ${rep.source}`;
  src.textContent = rep.source === 'gemini' ? `Written by Gemini (${rep.model})` : 'Automatic notes (no AI)';
  $('who').textContent = rep.source === 'gemini' ? 'Gemini' : 'the simulator';
  if (rep.error) { $('error').textContent = rep.error; $('error').hidden = false; }

  const p = rep.patient || {};
  const les = Object.entries(p.lesion || {}).map(([r, h]) => `${regionShort(r)} ${Math.round((1 - h) * 100)}% damaged`).join(', ');
  $('pTitle').textContent = `Patient ${p.card ?? '—'} · ${p.age ?? '—'} years`;
  $('pSub').textContent = `${les}${p.affected_arm ? ` · ${p.affected_arm} arm affected` : ''}`;
  const days = rep.days || [];
  $('summary').textContent = rep.summary || '';
  $('highlights').innerHTML = (rep.highlights || []).map((h) => `<li>${h}</li>`).join('');
  $('rec').textContent = rep.recommendation || '';
  $('top').hidden = false;
  if (!days.length) return;

  const first = days[0].arm, last = days[days.length - 1].arm;
  $('sStart').textContent = pct(first); $('sNow').textContent = pct(last);
  $('sGain').textContent = `${last >= first ? '+' : ''}${Math.round((last - first) * 100)}`;
  $('sDays').textContent = days[days.length - 1].day;

  $('days').innerHTML = days.map((d) => {
    const ch = d.change == null ? null : Math.round(d.change * 1000) / 10;
    const cls = ch == null ? 'flat' : ch > 0.05 ? 'up' : ch < -0.05 ? 'down' : 'flat';
    const tags = [];
    if (d.attempts) tags.push(`<span>${d.attempts} squeezes · effort ${pct(d.effort)}</span>`);
    if (d.stims) tags.push(`<span class="${d.good ? 'good' : ''}">${d.stims} stim${d.stims === 1 ? '' : 's'}${d.good ? ` · ${d.good} well-timed` : ''}</span>`);
    if (d.seizures) tags.push(`<span class="sz">${d.seizures} seizure${d.seizures === 1 ? '' : 's'}</span>`);
    if (d.partial) tags.push('<span>in progress</span>');
    return `<li class="${d.partial ? 'partial' : ''}">
      <div class="dn">DAY<b>${d.day}</b></div>
      <div class="arm">${pct(d.arm)}<small class="${cls}">${ch == null ? 'start' : `${ch > 0 ? '+' : ''}${ch} pts`}</small></div>
      <div class="note">${d.note || ''}</div>
      <div class="tags">${tags.join('')}</div></li>`;
  }).join('');
  $('daysBox').hidden = false;
  $('chartBox').hidden = false;
  requestAnimationFrame(() => drawChart(days));
}

function drawChart(days) {
  const cv = $('repChart'), dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const L = 44, R = 12, T = 12, B = 24, pw = W - L - R, ph = H - T - B;
  const maxD = Math.max(90, days[days.length - 1].day);
  const X = (d) => L + (d / maxD) * pw, Y = (v) => T + (1 - Math.min(1.05, v) / 1.05) * ph;
  const css = getComputedStyle(document.body);
  const print = window.matchMedia('print').matches;
  g.strokeStyle = 'rgba(30,42,50,0.08)'; g.fillStyle = 'rgba(30,42,50,0.65)';
  g.font = '11px ui-monospace, monospace';
  for (const v of [0, 0.25, 0.5, 0.75, 1]) { g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - R, Y(v)); g.stroke(); g.fillText(`${v * 100}%`, 6, Y(v) + 4); }
  for (const d of [0, 15, 30, 45, 60, 75, 90]) g.fillText(`${d}`, X(d) - 6, H - 6);
  const grad = g.createLinearGradient(0, T, 0, T + ph);
  grad.addColorStop(0, 'rgba(31,111,120,0.30)'); grad.addColorStop(1, 'rgba(31,111,120,0)');
  g.beginPath(); days.forEach((d, i) => (i ? g.lineTo(X(d.day), Y(d.arm)) : g.moveTo(X(d.day), Y(d.arm))));
  g.lineTo(X(days[days.length - 1].day), T + ph); g.lineTo(X(days[0].day), T + ph); g.closePath();
  g.fillStyle = grad; g.fill();
  g.beginPath(); days.forEach((d, i) => (i ? g.lineTo(X(d.day), Y(d.arm)) : g.moveTo(X(d.day), Y(d.arm))));
  g.strokeStyle = '#1F6F78'; g.lineWidth = 2.5; g.stroke();
  for (const d of days) if (d.seizures) { g.fillStyle = '#E07A5F'; g.beginPath(); g.arc(X(d.day), Y(d.arm), 4, 0, Math.PI * 2); g.fill(); }
}

$('refresh').addEventListener('click', () => load(true));
window.addEventListener('resize', () => { /* redraw on next load */ });
load();
