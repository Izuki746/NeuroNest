// 3D brain: 76 regions at their real positions, holographic hemisphere shells,
// structural wiring, rewiring edges, stroke lesion, seizure spread, stimulation rings,
// AI treatment target. All animation is smooth and slow (no strobing, ≤3 Hz).
import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { regionName, regionShort, KEY_REGIONS } from './regions.js';

const C = {
  low: new THREE.Color('#123a5a'), mid: new THREE.Color('#38d9f5'), high: new THREE.Color('#ffffff'),
  lesion: new THREE.Color('#3a3f4b'), seizure: new THREE.Color('#ff3b5c'),
  excite: new THREE.Color('#4d8dff'), calm: new THREE.Color('#ffd23f'),
  grow: new THREE.Color('#34d399'), weaken: new THREE.Color('#ff6b6b'), shell: new THREE.Color('#2d5b8f'),
};

function glowTexture() {
  const s = 128, cv = document.createElement('canvas');
  cv.width = cv.height = s;
  const g = cv.getContext('2d'), grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  grd.addColorStop(0, 'rgba(255,255,255,1)');
  grd.addColorStop(0.25, 'rgba(255,255,255,0.55)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd; g.fillRect(0, 0, s, s);
  return new THREE.CanvasTexture(cv);
}

function ringTexture() {
  const s = 128, cv = document.createElement('canvas');
  cv.width = cv.height = s;
  const g = cv.getContext('2d');
  g.strokeStyle = 'white'; g.lineWidth = 7;
  g.beginPath(); g.arc(s / 2, s / 2, s / 2 - 8, 0, Math.PI * 2); g.stroke();
  return new THREE.CanvasTexture(cv);
}

// data axes: x = front(+)/back, y = left(+)/right, z = up  →  three: X = -y, Y = z, Z = -x
const toThree = ([x, y, z]) => new THREE.Vector3(-y, z, -x);

export class Brain3D {
  constructor(container, { tooltip, labels }) {
    this.container = container; this.tooltip = tooltip; this.labelsEl = labels;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 1, 2000);
    this.camera.position.set(0, 105, 165);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.autoRotate = true; this.controls.autoRotateSpeed = 0.35;
    this.controls.minDistance = 80; this.controls.maxDistance = 400; this.controls.enablePan = false;
    this.scene.add(new THREE.AmbientLight(0x8fb4ff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.0); key.position.set(80, 140, 120); this.scene.add(key);
    this.glowTex = glowTexture(); this.ringTex = ringTexture();
    this.nodes = []; this.labels = new Map(); this.selected = null; this.onSelect = null;
    this.state = {}; this.edgeObj = null; this.seizureLines = null;
    this.raycaster = new THREE.Raycaster(); this.mouse = new THREE.Vector2(-9, -9);
    this.renderer.domElement.addEventListener('pointermove', (e) => this._pointer(e));
    this.renderer.domElement.addEventListener('pointerleave', () => { this.mouse.set(-9, -9); this.tooltip.hidden = true; });
    this.renderer.domElement.addEventListener('click', () => this._click());
    new ResizeObserver(() => this._resize()).observe(container);
    this._resize();
    this.clock = new THREE.Clock();
    this.renderer.setAnimationLoop(() => this._frame());
  }

  setConnectome({ names, centres, edges }) {
    for (const n of this.nodes) { this.scene.remove(n.mesh, n.glow, n.ring); }
    this.nodes = []; this.labelsEl.innerHTML = ''; this.labels.clear();
    if (this.root) this.scene.remove(this.root);
    this.root = new THREE.Group(); this.scene.add(this.root);
    const pts = centres.map(toThree);
    const centre = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
    pts.forEach((p) => p.sub(centre));
    this.names = names; this.index = new Map(names.map((n, i) => [n, i])); this.pos = pts;
    this._buildShells(pts, names);
    this._buildStructure(edges || []);
    const geo = new THREE.SphereGeometry(2.1, 24, 16);
    names.forEach((name, i) => {
      const mat = new THREE.MeshStandardMaterial({ color: C.low.clone(), emissive: C.low.clone(), emissiveIntensity: 0.6, roughness: 0.35, metalness: 0.1 });
      const mesh = new THREE.Mesh(geo, mat); mesh.position.copy(pts[i]); mesh.userData.i = i;
      const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glowTex, color: C.mid.clone(), transparent: true, opacity: 0.0, depthWrite: false, blending: THREE.AdditiveBlending }));
      glow.position.copy(pts[i]); glow.scale.setScalar(10);
      const ring = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.ringTex, color: C.excite.clone(), transparent: true, opacity: 0, depthWrite: false }));
      ring.position.copy(pts[i]); ring.scale.setScalar(8);
      this.root.add(mesh, glow, ring);
      this.nodes.push({ name, mesh, glow, ring, act: 0.05, health: 1, ringT: -1, ringKind: null });
    });
  }

  _buildShells(pts, names) {
    for (const side of ['l', 'r']) {
      const idx = names.map((n, i) => (n[0] === side ? i : -1)).filter((i) => i >= 0);
      const box = new THREE.Box3().setFromPoints(idx.map((i) => pts[i]));
      const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3());
      const rad = new THREE.Vector3(s.x / 2 + 9, s.y / 2 + 10, s.z / 2 + 10);
      const N = 1600, arr = new Float32Array(N * 3), gold = Math.PI * (3 - Math.sqrt(5));
      for (let k = 0; k < N; k++) {
        const y = 1 - (k / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = gold * k;
        arr.set([c.x + Math.cos(th) * r * rad.x, c.y + y * rad.y, c.z + Math.sin(th) * r * rad.z], k * 3);
      }
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
      this.root.add(new THREE.Points(g, new THREE.PointsMaterial({ color: C.shell, size: 0.7, transparent: true, opacity: 0.35, depthWrite: false })));
    }
  }

  _buildStructure(edges) {
    const pos = [], col = [];
    for (const [a, b, w] of edges) {
      pos.push(...this.pos[a].toArray(), ...this.pos[b].toArray());
      const k = 0.12 + 0.25 * w; col.push(0.25 * k, 0.55 * k, 0.9 * k, 0.25 * k, 0.55 * k, 0.9 * k);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    this.root.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending, depthWrite: false })));
  }

  setRewiring(edges) {
    if (!this.root || !edges) return;
    if (this.edgeObj) this.root.remove(this.edgeObj);
    const max = Math.max(1e-6, ...edges.map((e) => Math.abs(e[2])));
    const pos = [], col = [];
    for (const [a, b, d] of edges) {
      if (!this.pos[a] || !this.pos[b]) continue;
      pos.push(...this.pos[a].toArray(), ...this.pos[b].toArray());
      const c = d >= 0 ? C.grow : C.weaken, k = 0.35 + 0.65 * Math.abs(d) / max;
      col.push(c.r * k, c.g * k, c.b * k, c.r * k, c.g * k, c.b * k);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    this.edgeObj = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.root.add(this.edgeObj);
  }

  update(s) {
    this.state = s;
    if (!this.nodes.length) return;
    if (s.activity) s.activity.forEach((a, i) => { if (this.nodes[i]) this.nodes[i].act = a; });
    if (s.health) s.health.forEach((h, i) => { if (this.nodes[i]) this.nodes[i].health = h; });
    if (s.stim && s.stim.region && this.index.has(s.stim.region)) {
      const n = this.nodes[this.index.get(s.stim.region)];
      if (n.ringKind !== s.stim.kind || this.clock.elapsedTime - n.ringT > 0.7) { n.ringT = this.clock.elapsedTime; n.ringKind = s.stim.kind; }
    }
    this._updateSeizureLines(s.seizure);
  }

  _updateSeizureLines(sz) {
    if (this.seizureLines) { this.root.remove(this.seizureLines); this.seizureLines = null; }
    if (!sz || !sz.regions) return;
    const ids = Object.keys(sz.regions).filter((r) => this.index.has(r));
    if (ids.length < 2) return;
    const on = this.pos[this.index.get(sz.onset)] || this.pos[this.index.get(ids[0])];
    const pos = [];
    for (const r of ids) if (r !== sz.onset) pos.push(...on.toArray(), ...this.pos[this.index.get(r)].toArray());
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    this.seizureLines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: C.seizure, transparent: true, opacity: 0.7, blending: THREE.AdditiveBlending, depthWrite: false }));
    this.root.add(this.seizureLines);
  }

  _frame() {
    const t = this.clock.getElapsedTime();
    const s = this.state || {};
    const sz = s.seizure && s.seizure.regions ? s.seizure.regions : {};
    const planTarget = s.plan && s.plan.target;
    for (const n of this.nodes) {
      const lesioned = n.health < 0.6;
      const szv = sz[n.name] || 0;
      const a = Math.max(0, Math.min(1, (n.act - 0.04) / 0.6));
      let col;
      if (szv > 0.02) col = C.seizure.clone().lerp(C.high, 0.15 * szv);
      else if (lesioned) col = C.lesion.clone().lerp(C.mid, a * 0.35);
      else col = a < 0.5 ? C.low.clone().lerp(C.mid, a * 2) : C.mid.clone().lerp(C.high, (a - 0.5) * 2);
      n.mesh.material.color.copy(col); n.mesh.material.emissive.copy(col);
      n.mesh.material.emissiveIntensity = lesioned ? 0.25 : 0.45 + 0.9 * a;
      const sc = lesioned ? 0.8 : 0.9 + 0.7 * a + (szv > 0.02 ? 0.5 * szv : 0);
      n.mesh.scale.setScalar(n.mesh.scale.x + (sc - n.mesh.scale.x) * 0.25);
      n.glow.material.color.copy(szv > 0.02 ? C.seizure : lesioned ? new THREE.Color('#7a1f2b') : col);
      const gOp = szv > 0.02 ? 0.35 + 0.5 * szv : lesioned ? 0.22 + 0.06 * Math.sin(t * 1.2) : 0.08 + 0.75 * a * a;
      n.glow.material.opacity += (gOp - n.glow.material.opacity) * 0.25;
      n.glow.scale.setScalar(8 + (szv > 0.02 ? 16 * szv : 12 * a) + (lesioned ? 6 : 0));
      // stimulation ring (expands and fades over 0.8 s), AI plan target ring (slow breathing)
      const rt = t - n.ringT;
      if (n.ringT >= 0 && rt < 0.8) {
        n.ring.material.color.copy(n.ringKind === 'calm' ? C.calm : C.excite);
        n.ring.material.opacity = 1 - rt / 0.8; n.ring.scale.setScalar(6 + 22 * rt);
      } else if (n.name === planTarget) {
        n.ring.material.color.copy(s.plan.kind === 'calm' ? C.calm : C.excite);
        n.ring.material.opacity = 0.45 + 0.25 * Math.sin(t * 2.0); n.ring.scale.setScalar(9);
      } else n.ring.material.opacity = 0;
    }
    this._hover();
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
    this._labels(planTarget);
  }

  _labels(planTarget) {
    const s = this.state || {};
    const want = new Map();
    for (const id of KEY_REGIONS) want.set(id, '');
    if (s.patient && s.patient.lesion) for (const id of Object.keys(s.patient.lesion)) want.set(id, 'lesion');
    if (planTarget) want.set(planTarget, 'plan');
    if (s.seizure && s.seizure.onset) want.set(s.seizure.onset, 'seizure');
    if (s.stim && s.stim.region) want.set(s.stim.region, s.stim.kind);
    if (this.selected) want.set(this.selected, 'selected');
    for (const [id, el] of this.labels) if (!want.has(id)) { el.remove(); this.labels.delete(id); }
    const w = this.container.clientWidth, h = this.container.clientHeight;
    for (const [id, kind] of want) {
      if (!this.index || !this.index.has(id)) continue;
      let el = this.labels.get(id);
      if (!el) { el = document.createElement('div'); el.className = 'blabel'; this.labelsEl.appendChild(el); this.labels.set(id, el); }
      const tag = { lesion: ' · stroke', plan: ' · AI target', seizure: ' · seizure', excite: ' · excite', calm: ' · calm', selected: '' }[kind] || '';
      el.textContent = regionShort(id) + tag;
      el.dataset.kind = kind;
      const p = this.nodes[this.index.get(id)].mesh.getWorldPosition(new THREE.Vector3()).project(this.camera);
      el.style.transform = `translate(${(p.x * 0.5 + 0.5) * w}px, ${(-p.y * 0.5 + 0.5) * h}px)`;
      el.style.opacity = p.z < 1 ? 1 : 0;
    }
  }

  _pointer(e) {
    const r = this.renderer.domElement.getBoundingClientRect();
    this.mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.tooltip.style.transform = `translate(${e.clientX - r.left + 14}px, ${e.clientY - r.top + 10}px)`;
  }

  _hit() {
    this.raycaster.setFromCamera(this.mouse, this.camera);
    const hit = this.raycaster.intersectObjects(this.nodes.map((n) => n.mesh))[0];
    return hit ? this.nodes[hit.object.userData.i] : null;
  }

  _hover() {
    if (this.mouse.x < -2) return;
    const n = this._hit();
    if (!n) { this.tooltip.hidden = true; this.controls.autoRotate = true; return; }
    this.controls.autoRotate = false;
    const s = this.state || {};
    const szv = s.seizure && s.seizure.regions ? s.seizure.regions[n.name] : 0;
    this.tooltip.hidden = false;
    this.tooltip.innerHTML = `<b>${regionName(n.name)}</b> <span class="mono">${n.name}</span><br>
      activity ${(n.act * 100).toFixed(0)}% · health ${(n.health * 100).toFixed(0)}%${szv ? ` · <span class="sz">seizure ${(szv * 100).toFixed(0)}%</span>` : ''}
      <br><span class="dim">click to select for stimulation</span>`;
  }

  _click() {
    const n = this._hit();
    this.selected = n ? n.name : null;
    if (this.onSelect) this.onSelect(this.selected);
  }

  _resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h); this.camera.aspect = w / h; this.camera.updateProjectionMatrix();
  }
}
