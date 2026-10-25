// NeuroNest phone controller. Plain http/ws only (works on iOS Safari + Android Chrome over LAN).
(function () {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const base = proto + location.host;
  const id = "phone-" + Math.random().toString(36).slice(2, 7);
  const REGIONS = [
    ["lPMCDL", "excite"], ["lPMCVL", "excite"], ["lPMCM", "excite"], ["lM1", "excite"], ["lS1", "excite"],
    ["rM1", "calm"], ["rPMCDL", "calm"],
  ];
  let region = "lPMCDL";
  let rtt = 0;          // EMA of round trip, ms
  let inWs = null, stWs = null;

  const $ = (s) => document.querySelector(s);
  const conn = $("#conn"), timingEl = $("#timing"), armEl = $("#arm"), flash = $("#flash");

  // ---- region chips ----
  const chips = $("#chips");
  function renderChips() {
    chips.innerHTML = "";
    REGIONS.forEach(([r, k]) => {
      const b = document.createElement("button");
      b.className = "chip" + (r === region ? " sel" : "");
      b.innerHTML = r + " <small>" + (k === "excite" ? "+" : "-") + "</small>";
      b.addEventListener("click", () => { region = r; renderChips(); });
      chips.appendChild(b);
    });
    const o = document.createElement("button");
    o.className = "chip" + (REGIONS.some(([r]) => r === region) ? "" : " sel");
    o.textContent = REGIONS.some(([r]) => r === region) ? "Other" : "Other: " + region;
    o.addEventListener("click", () => {
      const v = prompt("Region name (e.g. lPMCDL)", region);
      if (v && /^[lr][A-Za-z0-9]+$/.test(v.trim())) { region = v.trim(); renderChips(); }
    });
    chips.appendChild(o);
  }
  renderChips();

  // ---- sockets ----
  function sendIn(msg) { if (inWs && inWs.readyState === 1) inWs.send(JSON.stringify(msg)); }

  function openInput() {
    inWs = new WebSocket(base + "/ws/input");
    inWs.onopen = () => { sendIn({ type: "hello", client: "phone", id }); setConn(true); };
    inWs.onclose = () => { setConn(false); setTimeout(openInput, 1000); };
    inWs.onerror = () => { try { inWs.close(); } catch (e) {} };
    inWs.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "pong" && typeof m.client_t === "number") {
        const sample = performance.now() - m.client_t;
        rtt = rtt === 0 ? sample : 0.7 * rtt + 0.3 * sample;
      }
    };
  }

  function openState() {
    stWs = new WebSocket(base + "/ws/state");
    stWs.onclose = () => setTimeout(openState, 1000);
    stWs.onerror = () => { try { stWs.close(); } catch (e) {} };
    stWs.onmessage = (ev) => {
      let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === "state") onState(m);
      else if (m.type === "event" && m.kind === "attempt_onset") flashOnce();
    };
  }

  function setConn(ok) {
    conn.textContent = ok ? "connected" + (rtt ? " " + Math.round(rtt) + " ms" : "") : "reconnecting...";
    conn.className = ok ? "ok" : "";
  }

  setInterval(() => {
    sendIn({ type: "ping", client_t: performance.now(), rtt_ms: Math.round(rtt) });
    setConn(inWs && inWs.readyState === 1);
  }, 2000);

  // ---- feedback ----
  let lastTimingKey = "";
  function onState(m) {
    if (m.arm_function != null) armEl.textContent = "Arm " + Math.round(m.arm_function * 100) + "%  ·  Day " + (m.day ?? "-");
    const t = m.timing;
    const key = t ? t.offset_ms + "|" + t.verdict : "";
    if (key === lastTimingKey) return;
    lastTimingKey = key;
    if (!t) return;
    const off = t.offset_ms;
    let txt;
    if (t.verdict === "wrong_region") txt = "Wrong region";
    else if (off == null) txt = "No stim";
    else {
      const ms = Math.abs(Math.round(off));
      txt = ms + " ms " + (off < 0 ? "EARLY" : "LATE");
      if (t.verdict === "good") txt += " ✓ good";
      else if (t.verdict === "late") txt += " ✗";
    }
    timingEl.textContent = txt;
    timingEl.className = t.verdict === "good" ? "good" : (t.verdict ? "bad" : "");
  }

  function flashOnce() {
    flash.classList.add("on");
    setTimeout(() => flash.classList.remove("on"), 120);  // single soft flash, fades over ~450 ms
  }

  // ---- buttons: touchstart for lowest latency ----
  function stim(kind) {
    sendIn({ type: "stim", region, kind, intensity: 1.0, client_t: performance.now(), rtt_ms: Math.round(rtt), id });
    if (navigator.vibrate) navigator.vibrate(30);
  }
  function bind(el, fn) {
    let touched = false;
    el.addEventListener("touchstart", (e) => { e.preventDefault(); touched = true; fn(); }, { passive: false });
    el.addEventListener("mousedown", () => { if (!touched) fn(); touched = false; });
  }
  bind($("#excite"), () => stim("excite"));
  bind($("#calm"), () => stim("calm"));
  bind($("#squeeze"), () => {
    sendIn({ type: "attempt", effort: 0.7, depth: 0.7, speed: 0.7, hold_ms: 200, source: "phone" });
    if (navigator.vibrate) navigator.vibrate(30);
  });

  openInput();
  openState();
})();