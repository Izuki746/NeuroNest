// Recovery chart: observed arm function by day, the closing recovery window, the twins'
// forecast cone, and treatment comparison curves (AI plan vs standard rehab vs no rehab).
const COL = {
  grid: 'rgba(140,170,220,0.10)', axis: 'rgba(170,190,230,0.55)', text: 'rgba(200,215,240,0.75)',
  obs: '#ffffff', fc: '#38d9f5', ai: '#34d399', standard: '#f5a524', none: '#ff6b6b',
};

export function drawRecovery(canvas, s) {
  const dpr = window.devicePixelRatio || 1;
  const W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  if (canvas.width !== W * dpr || canvas.height !== H * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
  const g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const L = 44, R = 14, T = 14, B = 26, pw = W - L - R, ph = H - T - B;
  const X = (d) => L + (Math.min(Math.max(d, 0), 90) / 90) * pw;
  const Y = (v) => T + (1 - Math.min(Math.max(v, 0), 1.05) / 1.05) * ph;

  // recovery window: strongest early, fading (TIME)
  const grad = g.createLinearGradient(X(0), 0, X(75), 0);
  grad.addColorStop(0, 'rgba(52,211,153,0.16)'); grad.addColorStop(1, 'rgba(52,211,153,0)');
  g.fillStyle = grad; g.fillRect(X(0), T, X(75) - X(0), ph);
  g.fillStyle = 'rgba(52,211,153,0.55)'; g.font = '11px ui-sans-serif, system-ui';
  g.fillText('recovery window closing →', X(1.5), T + 12);

  // grid + axes
  g.strokeStyle = COL.grid; g.lineWidth = 1; g.fillStyle = COL.text; g.font = '11px ui-monospace, monospace';
  for (const v of [0, 0.25, 0.5, 0.75, 1]) {
    g.beginPath(); g.moveTo(L, Y(v)); g.lineTo(W - R, Y(v)); g.stroke();
    g.fillText(`${Math.round(v * 100)}%`, 6, Y(v) + 4);
  }
  for (const d of [0, 15, 30, 45, 60, 75, 90]) {
    g.beginPath(); g.moveTo(X(d), T); g.lineTo(X(d), T + ph); g.stroke();
    g.fillText(d === 0 ? 'day 0' : `${d}`, X(d) - (d === 0 ? 0 : 7), H - 8);
  }

  const band = (c, color, alpha) => {
    if (!c || !c.days || !c.days.length) return;
    g.fillStyle = color; g.globalAlpha = alpha; g.beginPath();
    c.days.forEach((d, i) => (i ? g.lineTo(X(d), Y(c.p90[i])) : g.moveTo(X(d), Y(c.p90[i]))));
    for (let i = c.days.length - 1; i >= 0; i--) g.lineTo(X(c.days[i]), Y(c.p10[i]));
    g.closePath(); g.fill(); g.globalAlpha = 1;
  };
  const line = (days, vals, color, width, dash = []) => {
    if (!days || !days.length) return;
    g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dash); g.beginPath();
    days.forEach((d, i) => (i ? g.lineTo(X(d), Y(vals[i])) : g.moveTo(X(d), Y(vals[i]))));
    g.stroke(); g.setLineDash([]);
  };

  // treatment comparison curves
  const pc = s.plan_curves;
  if (pc) {
    for (const k of ['none', 'standard', 'ai']) if (pc[k]) band(pc[k], COL[k], 0.10);
    for (const k of ['none', 'standard', 'ai']) if (pc[k]) line(pc[k].days, pc[k].p50, COL[k], k === 'ai' ? 2.5 : 1.8, [6, 5]);
  }

  // forecast cone from today to day 90 (twins), only when no plan comparison is shown
  const obs = s.curve || { days: [], arm: [] };
  const today = s.day || (obs.days.length ? obs.days[obs.days.length - 1] : 0);
  const nowArm = s.arm_function ?? (obs.arm.length ? obs.arm[obs.arm.length - 1] : 0);
  const fc = s.forecast;
  if (fc && !pc && today < 90) {
    const days = [], p10 = [], p50 = [], p90 = [];
    for (let d = today; d <= 90; d++) {
      const f = (d - today) / Math.max(90 - today, 1), w = Math.sqrt(f);
      days.push(d); p50.push(nowArm + (fc.p50 - nowArm) * f);
      p10.push(nowArm + (fc.p10 - nowArm) * w); p90.push(nowArm + (fc.p90 - nowArm) * w);
    }
    band({ days, p10, p90 }, COL.fc, 0.16); line(days, p50, COL.fc, 1.8, [5, 5]);
  }

  // observed recovery
  if (obs.days.length) {
    line(obs.days, obs.arm, COL.obs, 2.6);
  }
  // today marker
  g.fillStyle = COL.obs; g.beginPath(); g.arc(X(today), Y(nowArm), 4.5, 0, Math.PI * 2); g.fill();
  g.strokeStyle = 'rgba(255,255,255,0.35)'; g.lineWidth = 6; g.beginPath(); g.arc(X(today), Y(nowArm), 8, 0, Math.PI * 2); g.stroke();
}
