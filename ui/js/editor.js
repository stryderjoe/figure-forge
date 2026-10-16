// Animation editor: pose the figure with a gizmo, key bones on a timeline, and key face expressions.
//
// An animation: { name, fps, frames, loop, keys: { boneName: { frame: { q: [x,y,z,w], p: [x,y,z] } } },
//                 face: { materialKey: { frame: expressionIndex } } }
// Bones without keys stay at their rest pose. Between keys rotations are slerped and positions lerped;
// face expressions hold until the next face key.
import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

const _q = new THREE.Quaternion(), _p = new THREE.Vector3();

export function newAnimation(name = 'my_animation') {
  return { name, fps: 30, frames: 48, loop: true, keys: {}, face: {} };
}

export class AnimEditor {
  constructor(viewer, faces, onChange) {
    this.viewer = viewer;
    this.faces = faces;
    this.onChange = onChange || (() => {});
    this.anim = newAnimation();
    this.frame = 0;
    this.time = 0;
    this.playing = false;
    this.active = false;
    this.autoKey = true;
    this.selected = null;
    this.clipboard = null;
    this.handles = new THREE.Group();
    this.handles.visible = false;
    viewer.scene.add(this.handles);
    this.gizmo = new TransformControls(viewer.camera, viewer.renderer.domElement);
    this.gizmo.setSpace('local');
    this.gizmo.setSize(1.0);
    this.gizmoHelper = this.gizmo.getHelper ? this.gizmo.getHelper() : this.gizmo;
    viewer.scene.add(this.gizmoHelper);
    this.gizmo.addEventListener('dragging-changed', e => {
      viewer.controls.enabled = !e.value;
      if (!e.value && this.autoKey && this.selected) this.key(this.selected);
    });
    this.gizmo.addEventListener('objectChange', () => this.onChange('pose'));
    // pick a joint: a click (not a drag) on one of the handles
    let down = null;
    const dom = viewer.renderer.domElement;
    dom.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
    dom.addEventListener('pointerup', e => {
      if (!this.active || !down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4 || this.gizmo.dragging) return;
      const r = dom.getBoundingClientRect();
      const ray = new THREE.Raycaster();
      ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), viewer.camera);
      const hit = ray.intersectObjects(this.handles.children, false)[0];
      if (hit) this.select(hit.object.userData.bone);
    });
    viewer.tickers.push(dt => this.tick(dt));
  }

  // -- modes -----------------------------------------------------------------------------------------
  start() {
    this.active = true;
    this.viewer.stop();
    this.buildHandles();
    this.handles.visible = true;
    this.evaluate(this.frame);
    if (this.selected) this.gizmo.attach(this.selected);
  }

  stop() {
    this.active = false;
    this.playing = false;
    this.handles.visible = false;
    this.gizmo.detach();
    this.viewer.restPose();
    this.faces.showAll(0);
  }

  buildHandles() {
    this.handles.clear();
    const size = this.figureSize() * 0.0075;
    const geo = new THREE.SphereGeometry(size, 10, 8);
    for (const bone of this.viewer.bones) {
      const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x4fd1ff, depthTest: false, transparent: true, opacity: 0.85 }));
      m.renderOrder = 999;
      m.userData.bone = bone;
      this.handles.add(m);
    }
    this.colourHandles();
  }

  figureSize() {
    const box = new THREE.Box3().setFromObject(this.viewer.figure, true);
    return box.isEmpty() ? 0.4 : box.getSize(new THREE.Vector3()).length();
  }

  colourHandles() {
    for (const h of this.handles.children) {
      const b = h.userData.bone;
      const keyed = this.anim.keys[b.name] && Object.keys(this.anim.keys[b.name]).length;
      h.material.color.setHex(b === this.selected ? 0xffcf1a : keyed ? 0xff7a45 : 0x4fd1ff);
    }
  }

  select(bone) {
    this.selected = bone;
    if (bone) this.gizmo.attach(bone); else this.gizmo.detach();
    this.colourHandles();
    this.onChange('select');
  }

  setMode(mode) { this.gizmo.setMode(mode); }

  // -- keys --------------------------------------------------------------------------------------------
  key(bone, frame = this.frame) {
    const k = (this.anim.keys[bone.name] = this.anim.keys[bone.name] || {});
    const entry = { q: bone.quaternion.toArray().map(v => +v.toFixed(6)) };
    const rest = bone.userData.bind;
    if (bone.position.distanceTo(rest.p) > 1e-5) entry.p = bone.position.toArray().map(v => +v.toFixed(6));
    k[frame] = entry;
    this.colourHandles();
    this.onChange('keys');
  }

  // key every bone that is posed away from rest or already animated
  keyAll(frame = this.frame) {
    for (const b of this.viewer.bones) {
      const r = b.userData.bind;
      const moved = 1 - Math.abs(b.quaternion.dot(r.q)) > 1e-6 || b.position.distanceTo(r.p) > 1e-5;
      if (moved || this.anim.keys[b.name]) this.key(b, frame);
    }
  }

  unkey(bone, frame = this.frame) {
    const k = this.anim.keys[bone.name];
    if (!k) return;
    delete k[frame];
    if (!Object.keys(k).length) delete this.anim.keys[bone.name];
    this.evaluate(this.frame);
    this.colourHandles();
    this.onChange('keys');
  }

  resetBone(bone) {
    const r = bone.userData.bind;
    bone.position.copy(r.p); bone.quaternion.copy(r.q); bone.scale.copy(r.s);
    if (this.autoKey) this.key(bone);
  }

  copyPose() {
    this.clipboard = Object.fromEntries(this.viewer.bones.map(b => [b.name, { q: b.quaternion.toArray(), p: b.position.toArray() }]));
  }

  pastePose() {
    if (!this.clipboard) return;
    for (const b of this.viewer.bones) {
      const c = this.clipboard[b.name];
      if (!c) continue;
      b.quaternion.fromArray(c.q); b.position.fromArray(c.p);
    }
    this.keyAll();
  }

  // swap left and right: each l_ bone takes its r_ twin's turn, mirrored across the figure's middle (x)
  mirrorPose() {
    const by = {};
    for (const b of this.viewer.bones) by[b.userData.common] = b;
    const pose = Object.fromEntries(this.viewer.bones.map(b => [b.userData.common, b.quaternion.clone()]));
    for (const b of this.viewer.bones) {
      const c = b.userData.common;
      const twin = c.startsWith('l_') ? 'r_' + c.slice(2) : c.startsWith('r_') ? 'l_' + c.slice(2) : c;
      const src = pose[twin];
      if (!src || !by[twin]) continue;
      // turn relative to rest, mirrored (x stays, y and z flip), applied on this bone's rest
      const rel = by[twin].userData.bind.q.clone().invert().multiply(src);
      rel.y = -rel.y; rel.z = -rel.z;
      b.quaternion.copy(b.userData.bind.q).multiply(rel);
    }
    this.keyAll();
  }

  keyFrames(bone) {
    return bone && this.anim.keys[bone.name] ? Object.keys(this.anim.keys[bone.name]).map(Number).sort((a, b) => a - b) : [];
  }

  allKeyFrames() {
    const s = new Set();
    for (const k of Object.values(this.anim.keys)) for (const f of Object.keys(k)) s.add(+f);
    for (const k of Object.values(this.anim.face)) for (const f of Object.keys(k)) s.add(+f);
    return [...s].sort((a, b) => a - b);
  }

  // -- face ----------------------------------------------------------------------------------------------
  keyFace(materialKey, index, frame = this.frame) {
    (this.anim.face[materialKey] = this.anim.face[materialKey] || {})[frame] = index;
    this.evaluate(this.frame);
    this.onChange('keys');
  }

  unkeyFace(materialKey, frame = this.frame) {
    const k = this.anim.face[materialKey];
    if (!k) return;
    delete k[frame];
    if (!Object.keys(k).length) delete this.anim.face[materialKey];
    this.evaluate(this.frame);
    this.onChange('keys');
  }

  faceAt(materialKey, frame) {
    const k = this.anim.face[materialKey];
    if (!k) return 0;
    let best = null;
    for (const f of Object.keys(k).map(Number)) if (f <= frame + 1e-6 && (best === null || f > best)) best = f;
    if (best === null) return 0;
    return k[best];
  }

  // -- evaluation and playback -----------------------------------------------------------------------
  evaluate(frame) {
    this.viewer.restPose();
    for (const b of this.viewer.bones) {
      const k = this.anim.keys[b.name];
      if (!k) continue;
      const fs = Object.keys(k).map(Number).sort((a, c) => a - c);
      let i = fs.findIndex(f => f > frame);
      let a, c, t;
      if (i === -1) { a = c = fs[fs.length - 1]; t = 0; }
      else if (i === 0) { a = c = fs[0]; t = 0; }
      else { a = fs[i - 1]; c = fs[i]; t = (frame - a) / (c - a); }
      const ka = k[a], kc = k[c];
      _q.fromArray(ka.q).slerp(new THREE.Quaternion().fromArray(kc.q), t);
      b.quaternion.copy(_q);
      const pa = ka.p ? _p.fromArray(ka.p) : _p.copy(b.userData.bind.p);
      const pc = kc.p ? new THREE.Vector3().fromArray(kc.p) : b.userData.bind.p;
      b.position.copy(pa).lerp(pc, t);
    }
    this.viewer.figure.updateMatrixWorld(true);
    for (const key of Object.keys(this.anim.face)) this.faces.show(key, this.faceAt(key, frame));
  }

  setFrame(f) {
    this.frame = Math.max(0, Math.min(this.anim.frames - 1, Math.round(f)));
    this.time = this.frame / this.anim.fps;
    this.evaluate(this.frame);
    this.onChange('frame');
  }

  tick(dt) {
    if (!this.active) return;
    if (this.playing) {
      const len = this.anim.frames - 1;
      this.time += dt * (this.viewer.speed || 1);
      let f = this.time * this.anim.fps;
      if (f > len) {
        if (this.anim.loop && len > 0) { f %= len; this.time = f / this.anim.fps; }
        else { f = len; this.playing = false; }
      }
      this.frame = Math.floor(f);
      this.evaluate(f);
      this.onChange('play');
    }
    // the handles follow the bones
    const v = new THREE.Vector3();
    for (const h of this.handles.children) h.position.copy(v.setFromMatrixPosition(h.userData.bone.matrixWorld));
  }

  // -- clips -------------------------------------------------------------------------------------------
  // copy a playing classic clip into the editor: one key every `step` frames (and the last)
  fromViewerClip(step = 2) {
    const v = this.viewer;
    if (!v.clip) return false;
    const fps = v.clip.userData.fps, frames = v.clip.userData.frames;
    const anim = newAnimation(v.clip.name);
    anim.fps = fps; anim.frames = frames;
    const action = v.action;
    for (let f = 0; f < frames; f += step) sample(f);
    if ((frames - 1) % step) sample(frames - 1);
    function sample(f) {
      action.time = f / fps;
      v.mixer.update(0);
      for (const b of v.bones) {
        const k = (anim.keys[b.name] = anim.keys[b.name] || {});
        const e = { q: b.quaternion.toArray().map(x => +x.toFixed(6)) };
        if (b.position.distanceTo(b.userData.bind.p) > 1e-5) e.p = b.position.toArray().map(x => +x.toFixed(6));
        k[f] = e;
      }
    }
    v.stop();
    this.anim = anim;
    this.frame = 0;
    this.start();
    this.onChange('keys');
    return true;
  }

  // a three.js clip of the animation, addressed by bone name (for exporters)
  buildClip() {
    const tracks = [];
    const dt = 1 / this.anim.fps;
    for (const [name, k] of Object.entries(this.anim.keys)) {
      const bone = this.viewer.bones.find(b => b.name === name);
      if (!bone) continue;
      const fs = Object.keys(k).map(Number).sort((a, b) => a - b);
      const times = fs.map(f => f * dt);
      tracks.push(new THREE.QuaternionKeyframeTrack(`${name}.quaternion`, times, fs.flatMap(f => k[f].q)));
      if (fs.some(f => k[f].p)) {
        tracks.push(new THREE.VectorKeyframeTrack(`${name}.position`, times,
          fs.flatMap(f => k[f].p || bone.userData.bind.p.toArray())));
      }
    }
    return new THREE.AnimationClip(this.anim.name, (this.anim.frames - 1) * dt, tracks);
  }

  serialise() { return JSON.parse(JSON.stringify(this.anim)); }

  load(anim) {
    this.anim = Object.assign(newAnimation(), JSON.parse(JSON.stringify(anim)));
    this.frame = 0;
    if (this.active) this.evaluate(0);
    this.colourHandles();
    this.onChange('keys');
  }
}
