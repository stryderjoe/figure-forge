// Print editor: compose a part's texture from the original print, a fill colour and uploaded images,
// with the part's UV layout drawn on top so a picture can be fitted to the model.
import * as THREE from 'three';

export class PrintEditor {
  constructor(canvas, viewer, onChange) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.viewer = viewer;
    this.onChange = onChange;
    this.prints = new Map();       // material key -> print state
    this.key = null;
    this.selected = -1;
    this.showUV = true;
    let drag = null;
    canvas.addEventListener('pointerdown', e => {
      const s = this.state(); if (!s) return;
      const p = this.toCanvas(e);
      this.selected = this.hit(s, p);
      if (this.selected >= 0) drag = { p, x: s.layers[this.selected].x, y: s.layers[this.selected].y };
      canvas.setPointerCapture(e.pointerId);
      this.draw(); this.onChange && this.onChange('select');
    });
    canvas.addEventListener('pointermove', e => {
      const s = this.state(); if (!drag || !s) return;
      const p = this.toCanvas(e), L = s.layers[this.selected];
      L.x = drag.x + (p.x - drag.p.x); L.y = drag.y + (p.y - drag.p.y);
      this.apply();
    });
    canvas.addEventListener('pointerup', () => { drag = null; this.onChange && this.onChange('select'); });
    canvas.addEventListener('wheel', e => {
      const s = this.state(); if (!s || this.selected < 0) return;
      e.preventDefault();
      const L = s.layers[this.selected];
      if (e.shiftKey) L.rot += Math.sign(e.deltaY) * 2;
      else L.scale *= e.deltaY > 0 ? 0.95 : 1.05;
      this.apply(); this.onChange && this.onChange('select');
    }, { passive: false });
  }

  toCanvas(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width * this.canvas.width, y: (e.clientY - r.top) / r.height * this.canvas.height };
  }

  state() { return this.key ? this.prints.get(this.key) : null; }

  // open the editor on one material; the original texture (if any) is the starting print
  async open(key) {
    this.key = key;
    if (!this.prints.has(key)) {
      const mat = this.viewer.materials.get(key);
      const info = mat.userData.info;
      let base = null;
      if (info.texture) base = await loadImage(info.texture);
      const w = base ? base.width : 256, h = base ? base.height : 256;
      this.prints.set(key, { w, h, base, showBase: !!base, fill: null, layers: [], texture: null });
    }
    const s = this.state();
    this.canvas.width = s.w; this.canvas.height = s.h;
    this.selected = s.layers.length - 1;
    this.draw();
  }

  async addImage(file) {
    const s = this.state(); if (!s) return;
    const url = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(file); });
    const img = await loadImage(url);
    const fit = Math.min(s.w / img.width, s.h / img.height) * 0.5;
    s.layers.push({ src: url, img, name: file.name, x: s.w / 2, y: s.h / 2, scale: fit, rot: 0, opacity: 1, flip: false });
    this.selected = s.layers.length - 1;
    this.apply();
  }

  hit(s, p) {
    for (let i = s.layers.length - 1; i >= 0; i--) {
      const L = s.layers[i];
      const dx = p.x - L.x, dy = p.y - L.y, a = -L.rot * Math.PI / 180;
      const lx = (dx * Math.cos(a) - dy * Math.sin(a)) / L.scale, ly = (dx * Math.sin(a) + dy * Math.cos(a)) / L.scale;
      if (Math.abs(lx) <= L.img.width / 2 && Math.abs(ly) <= L.img.height / 2) return i;
    }
    return -1;
  }

  // the print itself (no guides): fill, original, then each image
  compose(target) {
    const s = this.state();
    const c = target.getContext('2d');
    target.width = s.w; target.height = s.h;
    c.clearRect(0, 0, s.w, s.h);
    if (s.fill) { c.fillStyle = s.fill; c.fillRect(0, 0, s.w, s.h); }
    if (s.base && s.showBase) c.drawImage(s.base, 0, 0, s.w, s.h);
    for (const L of s.layers) {
      c.save();
      c.globalAlpha = L.opacity;
      c.translate(L.x, L.y);
      c.rotate(L.rot * Math.PI / 180);
      c.scale(L.scale * (L.flip ? -1 : 1), L.scale);
      c.drawImage(L.img, -L.img.width / 2, -L.img.height / 2);
      c.restore();
    }
  }

  draw() {
    const s = this.state();
    const c = this.ctx;
    if (!s) { c.clearRect(0, 0, this.canvas.width, this.canvas.height); return; }
    // checkerboard, so transparency shows
    const k = Math.max(8, Math.round(s.w / 32));
    for (let y = 0; y < s.h; y += k) for (let x = 0; x < s.w; x += k) {
      c.fillStyle = ((x + y) / k) % 2 ? '#3a3f48' : '#2c3038'; c.fillRect(x, y, k, k);
    }
    const tmp = document.createElement('canvas');
    this.compose(tmp);
    c.drawImage(tmp, 0, 0);
    if (this.showUV) this.drawUV(c, s);
    if (this.selected >= 0 && s.layers[this.selected]) {
      const L = s.layers[this.selected];
      c.save();
      c.translate(L.x, L.y); c.rotate(L.rot * Math.PI / 180);
      c.strokeStyle = '#ffd400'; c.lineWidth = Math.max(1, s.w / 256); c.setLineDash([6, 4]);
      c.strokeRect(-L.img.width * L.scale / 2, -L.img.height * L.scale / 2, L.img.width * L.scale, L.img.height * L.scale);
      c.restore();
    }
  }

  drawUV(c, s) {
    c.save();
    c.strokeStyle = 'rgba(60, 230, 255, 0.9)';
    c.lineWidth = Math.max(1, s.w / 200);
    c.beginPath();
    this.viewer.figure.traverse(o => {
      if (!o.isMesh || o.userData.materialKey !== this.key) return;
      const uv = o.geometry.attributes.uv, idx = o.geometry.index;
      if (!uv || !idx) return;
      for (let i = 0; i < idx.count; i += 3) {
        for (let k = 0; k < 3; k++) {
          const a = idx.getX(i + k), b = idx.getX(i + (k + 1) % 3);
          c.moveTo(uv.getX(a) * s.w, (1 - uv.getY(a)) * s.h);
          c.lineTo(uv.getX(b) * s.w, (1 - uv.getY(b)) * s.h);
        }
      }
    });
    c.stroke();
    c.restore();
  }

  // put the composed print on the material
  apply() {
    const s = this.state(); if (!s) return;
    this.draw();
    const out = document.createElement('canvas');
    this.compose(out);
    const tex = new THREE.CanvasTexture(out);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.flipY = true;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    const mat = this.viewer.materials.get(this.key);
    if (s.texture) s.texture.dispose();
    s.texture = tex;
    mat.map = tex;
    mat.userData.info.mapOverride = tex;
    if (s.layers.length || s.fill) { mat.transparent = true; mat.alphaTest = 0.05; }
    mat.needsUpdate = true;
  }

  reset() {
    const s = this.state(); if (!s) return;
    const mat = this.viewer.materials.get(this.key);
    s.layers = []; s.fill = null; s.showBase = !!s.base;
    mat.map = s.base ? this.viewer.texture(mat.userData.info.texture) : null;
    mat.userData.info.mapOverride = null;
    mat.needsUpdate = true;
    this.selected = -1;
    this.draw();
  }

  // for saving a project
  serialise() {
    const out = {};
    for (const [key, s] of this.prints) {
      if (!s.layers.length && !s.fill && s.showBase === !!s.base) continue;
      out[key] = { showBase: s.showBase, fill: s.fill,
                   layers: s.layers.map(L => ({ src: L.src, name: L.name, x: L.x, y: L.y, scale: L.scale, rot: L.rot, opacity: L.opacity, flip: L.flip })) };
    }
    return out;
  }

  async restore(saved) {
    for (const [key, p] of Object.entries(saved || {})) {
      if (!this.viewer.materials.has(key)) continue;
      await this.open(key);
      const s = this.state();
      s.showBase = p.showBase; s.fill = p.fill;
      s.layers = [];
      for (const L of p.layers) s.layers.push({ ...L, img: await loadImage(L.src) });
      this.apply();
    }
  }
}

export function loadImage(src) {
  return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; });
}
