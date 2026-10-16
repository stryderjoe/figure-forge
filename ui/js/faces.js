// Face expressions. The classic games print faces as textures, so an expression is a whole face print:
// expression 0 is the face as it is (the original or edited print), the others are pictures the user
// captures from the print editor or uploads. The animation editor keys which one shows.
import * as THREE from 'three';
import { loadImage } from './prints.js';

export class Faces {
  constructor(viewer) {
    this.viewer = viewer;
    this.sets = new Map();          // material key -> [{ name, src, texture }]  (index 0 = the face as it is)
  }

  list(key) {
    if (!this.sets.has(key)) this.sets.set(key, [{ name: 'Default', src: null, texture: null }]);
    return this.sets.get(key);
  }

  // the material's own map: an edited print, or the game's texture
  baseMap(key) {
    const m = this.viewer.materials.get(key);
    if (!m) return null;
    return m.userData.info.mapOverride || this.viewer.texture(m.userData.info.texture);
  }

  textureFor(key, index) {
    const e = this.list(key)[index];
    if (!e || index === 0) return this.baseMap(key);
    return e.texture;
  }

  async add(key, name, src) {
    const img = await loadImage(src);
    const t = new THREE.Texture(img);
    t.colorSpace = THREE.SRGBColorSpace;
    t.flipY = true;
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.needsUpdate = true;
    this.list(key).push({ name, src, texture: t });
    return this.list(key).length - 1;
  }

  // the surface's current print, as a picture
  snapshot(key) {
    const map = this.baseMap(key);
    const img = map && map.image;
    if (!img) return null;
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    c.getContext('2d').drawImage(img, 0, 0);
    return c.toDataURL('image/png');
  }

  remove(key, index) {
    if (index <= 0) return;
    const e = this.list(key).splice(index, 1)[0];
    if (e && e.texture) e.texture.dispose();
  }

  show(key, index) {
    const m = this.viewer.materials.get(key);
    if (!m) return;
    const t = this.textureFor(key, index) || null;
    if (m.map !== t) { m.map = t; m.needsUpdate = true; }
  }

  showAll(index) { for (const key of this.sets.keys()) this.show(key, index); }

  serialise() {
    const out = {};
    for (const [key, list] of this.sets) {
      const extra = list.slice(1).map(e => ({ name: e.name, src: e.src }));
      if (extra.length) out[key] = extra;
    }
    return out;
  }

  async restore(saved) {
    for (const [key, list] of Object.entries(saved || {})) {
      for (const e of list) await this.add(key, e.name, e.src);
    }
  }

  clear() {
    for (const list of this.sets.values()) for (const e of list) if (e.texture) e.texture.dispose();
    this.sets.clear();
  }
}
