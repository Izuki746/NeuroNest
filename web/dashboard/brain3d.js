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

  // Brain-shaped holographic outline: two folded hemispheres (flat medial wall, temporal lobes),
  // cerebellum and brain stem, drawn with a rim-glow shader so the regions inside stay visible.
  // three.js axes here: X = right(+)/left(-), Y = up, Z = back(+)/front(-).
  _buildShells(pts, names) {
    const all = new THREE.Box3().setFromPoints(pts);
    const size = all.getSize(new THREE.Vector3()), ctr = all.getCenter(new THREE.Vector3());
    const shellMat = (opacity, color = '#4fa8ff') => new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(color) }, uOpacity: { value: opacity } },
      vertexShader: `varying vec3 vN; varying vec3 vV;
        void main() { vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `uniform vec3 uColor; uniform float uOpacity; varying vec3 vN; varying vec3 vV;
        void main() { float rim = 1.0 - abs(dot(normalize(vN), normalize(vV)));
          float a = uOpacity * (0.03 + 0.97 * pow(rim, 3.0));
          gl_FragColor = vec4(uColor * (0.55 + 0.9 * rim), a); }`,
      transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
    });
    // gyri/sulci: a few overlapping waves give a folded-cortex look
    const folds = (x, y, z) => 0.022 * Math.sin(0.16 * z + 1.1 * Math.sin(0.09 * y)) * Math.cos(0.14 * y + 0.6 * Math.sin(0.08 * z))
      + 0.016 * Math.sin(0.11 * x + 0.13 * z + 1.7 * Math.cos(0.07 * y))
      + 0.010 * Math.sin(0.23 * (y - z) + 0.4);

    for (const side of [-1, 1]) {                        // -1 = left hemisphere, +1 = right
      const hx = size.x / 4 + 6, hy = size.y / 2 + 10, hz = size.z / 2 + 11;   // half-sizes
      const cx = ctr.x + side * (hx * 0.9 + 0.8);                     // hemispheres nearly touch in the middle
      const geo = new THREE.SphereGeometry(1, 120, 80);
      const p = geo.attributes.position, v = new THREE.Vector3();
      for (let i = 0; i < p.count; i++) {
        v.fromBufferAttribute(p, i);
        let x = v.x, y = v.y, z = v.z;
        if (x * side < 0) x *= 0.9;                     // flatten the medial wall (where hemispheres meet)
        if (y < 0) y *= 0.8;                            // flatter underside
        const temporal = Math.exp(-((z - 0.05) ** 2) / 0.12) * Math.max(0, -y) * Math.max(0, x * side);
        y -= 0.28 * temporal; x += side * 0.08 * temporal;          // temporal lobe bulges down and out
        const frontTaper = z < 0 ? 1 - 0.12 * (-z) : 1 - 0.05 * z;  // slightly narrower at the front
        let X = x * hx * frontTaper, Y = y * hy, Z = z * hz;
        const f = 1 + folds(X + side * 3, Y, Z);
        p.setXYZ(i, cx + X * f, ctr.y + Y * f, ctr.z + Z * f);
      }
      geo.computeVertexNormals();
      this.root.add(new THREE.Mesh(geo, shellMat(0.32)));
    }

    // cerebellum (back, underneath) and brain stem
    const cere = new THREE.SphereGeometry(1, 80, 48), cp = cere.attributes.position, w = new THREE.Vector3();
    const cy = ctr.y - size.y / 2 - 2, cz = ctr.z + size.z / 2 - 4;
    for (let i = 0; i < cp.count; i++) {
      w.fromBufferAttribute(cp, i);
      const X = w.x * (size.x / 2.6), Y = w.y * 9, Z = w.z * 13;
      const f = 1 + 0.05 * Math.sin(1.6 * Y + 0.15 * X);            // fine horizontal folds
      cp.setXYZ(i, ctr.x + X * f, cy + Y * f, cz + Z * f);
    }
    cere.computeVertexNormals();
    this.root.add(new THREE.Mesh(cere, shellMat(0.28, '#6f8dff')));
    const stem = new THREE.CylinderGeometry(5.5, 4, 26, 32, 1, true);
    stem.rotateX(0.25);
    stem.translate(ctr.x, ctr.y - size.y / 2 - 14, ctr.z + size.z / 2 - 22);
    this.root.add(new THREE.Mesh(stem, shellMat(0.24, '#6f8dff')));
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
