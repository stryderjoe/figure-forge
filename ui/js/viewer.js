// The 3D view: builds a figure from a base skeleton and parts, renders it in a chosen style and plays clips.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export const STYLES = {
  classic: { label: 'Classic game', bg: [0x1d2a44, 0x0b0f1a], shininess: 70, specular: 0x3a3a3a,
             hemi: [0xdfe8ff, 0x3a3328, 1.2], key: [0xffffff, 2.2], rim: [0x9fc4ff, 1.4], fill: [0xffe6c8, 0.5] },
  studio:  { label: 'Studio', bg: [0xe9ecf1, 0xc9ced8], shininess: 40, specular: 0x222222,
             hemi: [0xffffff, 0x888888, 1.6], key: [0xffffff, 1.8], rim: [0xffffff, 0.6], fill: [0xffffff, 0.6] },
  flat:    { label: 'Flat (textures)', bg: [0x2a2d33, 0x2a2d33], flat: true },
};

export class Viewer {
  constructor(canvas) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
    this.camera.position.set(0.55, 0.32, 1.25);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.set(0, 0.2, 0);
    this.controls.enableDamping = true;
    this.figure = new THREE.Group();
    this.figure.name = 'Figure';
    this.scene.add(this.figure);
    this.lights = new THREE.Group();
    this.scene.add(this.lights);
    this.grid = new THREE.GridHelper(2, 20, 0x556070, 0x334050);
    this.grid.material.opacity = 0.35;
    this.grid.material.transparent = true;
    this.scene.add(this.grid);
    this.clock = new THREE.Clock();
    this.mixer = null;
    this.action = null;
    this.speed = 1;
    this.parts = new Map();          // part id -> [Object3D]
    this.materials = new Map();      // material key -> THREE.Material (shared by every mesh using it)
    this.textures = new Map();       // url -> THREE.Texture (original game textures)
    this.styleName = 'classic';
    this.setStyle('classic');
    this.onFrame = null;
    this.tickers = [];               // functions called every frame with the time step
    const resize = () => {
      const r = canvas.parentElement.getBoundingClientRect();
      this.renderer.setSize(r.width, r.height, false);
      this.camera.aspect = r.width / Math.max(1, r.height);
      this.camera.updateProjectionMatrix();
    };
    new ResizeObserver(resize).observe(canvas.parentElement);
    resize();
    const loop = () => {
      requestAnimationFrame(loop);
      const dt = this.clock.getDelta();
      if (this.mixer && this.playing) this.mixer.update(dt * this.speed);
      for (const t of this.tickers) t(dt);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      if (this.onFrame) this.onFrame();
    };
    loop();
  }

  setStyle(name) {
    const s = STYLES[name] || STYLES.classic;
    this.styleName = name;
    this.lights.clear();
    const c = document.createElement('canvas');
    c.width = 2; c.height = 256;
    const g = c.getContext('2d');
    const grad = g.createLinearGradient(0, 0, 0, 256);
    grad.addColorStop(0, '#' + s.bg[0].toString(16).padStart(6, '0'));
    grad.addColorStop(1, '#' + s.bg[1].toString(16).padStart(6, '0'));
    g.fillStyle = grad; g.fillRect(0, 0, 2, 256);
    const bg = new THREE.CanvasTexture(c);
    bg.colorSpace = THREE.SRGBColorSpace;
    this.scene.background = bg;
    if (!s.flat) {
      this.lights.add(new THREE.HemisphereLight(s.hemi[0], s.hemi[1], s.hemi[2]));
      const key = new THREE.DirectionalLight(s.key[0], s.key[1]);
      key.position.set(-1.2, 2.2, 2.0);
      const rim = new THREE.DirectionalLight(s.rim[0], s.rim[1]);
      rim.position.set(1.5, 1.0, -2.0);
      const fill = new THREE.DirectionalLight(s.fill[0], s.fill[1]);
      fill.position.set(2.0, 0.3, 1.0);
      this.lights.add(key, rim, fill);
    }
    for (const m of this.materials.values()) this.styleMaterial(m);
  }

  styleMaterial(mat) {
    const s = STYLES[this.styleName];
    const want = s.flat ? 'MeshBasicMaterial' : 'MeshPhongMaterial';
    if (mat.type !== want) return false;    // the caller rebuilds the material
    if (!s.flat) { mat.shininess = s.shininess; mat.specular.setHex(s.specular); }
    mat.needsUpdate = true;
    return true;
  }

  // -- figure --------------------------------------------------------------------------------------------
  clear() {
    this.stop();
    this.figure.clear();
    this.parts.clear();
    this.bones = [];
    this.skeleton = null;
  }

  setBase(skel) {
    this.clear();
    this.base = skel;
    const bones = skel.bones.map(b => {
      const bone = new THREE.Bone();
      bone.name = b.name;
      bone.userData.common = b.common;
      new THREE.Matrix4().fromArray(b.matrix).decompose(bone.position, bone.quaternion, bone.scale);
      bone.userData.bind = { p: bone.position.clone(), q: bone.quaternion.clone(), s: bone.scale.clone() };
      // newer era: the pose the figure stands in when no clip plays (its idle's first frame)
      const r = skel.rest && skel.rest.bones[b.name];
      if (r) bone.userData.rest = { p: new THREE.Vector3().fromArray(r.t), q: new THREE.Quaternion().fromArray(r.q),
                                    s: new THREE.Vector3().fromArray(r.s) };
      return bone;
    });
    skel.bones.forEach((b, i) => (b.parent >= 0 ? bones[b.parent] : this.figure).add(bones[i]));
    this.figure.updateMatrixWorld(true);
    this.bones = bones;
    this.skeleton = new THREE.Skeleton(bones);           // inverse binds from the true bind pose
    this.showRest();
    this.helper = new THREE.SkeletonHelper(this.figure);
    this.helper.visible = !!this.showBones;
    this.scene.children.filter(o => o.isSkeletonHelper).forEach(o => this.scene.remove(o));
    this.scene.add(this.helper);
  }

  // a new bone under `parentName` at `offset` (in the parent's frame); skinned meshes are re-bound to the
  // grown skeleton with their own bind matrices, so nothing already skinned moves
  addBone(name, parentName, offset = [0, 0.05, 0]) {
    if (this.bones.some(b => b.name === name)) return null;
    this.restPose();
    const parent = this.bones.find(b => b.name === parentName) || this.bones[0];
    const bone = new THREE.Bone();
    bone.name = name;
    bone.userData.common = name;
    bone.userData.custom = true;
    bone.position.fromArray(offset);
    bone.userData.bind = { p: bone.position.clone(), q: bone.quaternion.clone(), s: bone.scale.clone() };
    parent.add(bone);
    this.figure.updateMatrixWorld(true);
    const inverses = this.skeleton.boneInverses.map(m => m.clone());
    inverses.push(bone.matrixWorld.clone().invert());
    this.bones.push(bone);
    this.skeleton = new THREE.Skeleton(this.bones, inverses);
    this.figure.traverse(o => { if (o.isSkinnedMesh) o.bind(this.skeleton, o.bindMatrix); });
    this.scene.children.filter(o => o.isSkeletonHelper).forEach(o => this.scene.remove(o));
    this.helper = new THREE.SkeletonHelper(this.figure);
    this.helper.visible = !!this.showBones;
    this.scene.add(this.helper);
    return bone;
  }

  boneByCommon(common) {
    return this.bones.find(b => b.userData.common === common) || this.bones.find(b => b.name === common);
  }

  // the standing pose: the rest pose where the game has one, else the bind pose
  showRest() {
    for (const b of this.bones || []) {
      const r = b.userData.rest || b.userData.bind;
      b.position.copy(r.p); b.quaternion.copy(r.q); b.scale.copy(r.s);
    }
    this.figure.updateMatrixWorld(true);
  }

  restPose() {
    for (const b of this.bones || []) {
      const r = b.userData.bind;
      b.position.copy(r.p); b.quaternion.copy(r.q); b.scale.copy(r.s);
    }
    this.figure.updateMatrixWorld(true);
  }

  texture(url) {
    if (!url) return null;
    if (!this.textures.has(url)) {
      let done;
      const ready = new Promise(r => { done = r; });
      const t = new THREE.TextureLoader().load(url, () => done(), undefined, () => done());
      t.userData.ready = ready;
      t.colorSpace = THREE.SRGBColorSpace;
      t.flipY = true;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;       // the games wrap (some UVs run past 0..1)
      this.textures.set(url, t);
    }
    return this.textures.get(url);
  }

  // every texture in use has finished loading (exporters need the pixels)
  whenTexturesReady() {
    return Promise.all([...this.textures.values()].map(t => t.userData.ready).filter(Boolean));
  }

  makeMaterial(info) {
    const flat = STYLES[this.styleName].flat;
    const params = { color: new THREE.Color().setRGB(info.colour[0], info.colour[1], info.colour[2], THREE.SRGBColorSpace),
                     map: info.mapOverride || this.texture(info.texture), side: THREE.FrontSide };
    if (info.alpha) Object.assign(params, { transparent: true, alphaTest: 0.05, depthWrite: true });
    if (info.vertexColours) params.vertexColors = true;
    // writes depth only, pulled slightly toward the camera: hides what lies behind it, also pieces in its
    // own plane (LEGO Marvel's teeth sit exactly on it)
    if (info.occluder) Object.assign(params, { colorWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -8 });
    const m = flat ? new THREE.MeshBasicMaterial(params) : new THREE.MeshPhongMaterial(params);
    m.name = info.key || 'material';
    m.userData.info = info;
    this.styleMaterial(m);
    return m;
  }

  materialFor(info) {
    const key = info.key || JSON.stringify(info);
    if (!this.materials.has(key)) this.materials.set(key, this.makeMaterial({ ...info, key }));
    return this.materials.get(key);
  }

  // rebuild every material in the current style, keeping colours and edited maps
  restyleAll(state) {
    for (const [key, old] of this.materials) {
      const m = this.makeMaterial({ ...old.userData.info, colour: state(key, old), mapOverride: old.userData.info.mapOverride });
      this.materials.set(key, m);
      this.figure.traverse(o => { if (o.isMesh && o.material === old) o.material = m; });
      old.dispose();
    }
  }

  addPart(part) {
    this.removePart(part.id);
    const objs = [];
    for (const m of part.meshes) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(m.position, 3));
      if (m.normal) g.setAttribute('normal', new THREE.Float32BufferAttribute(m.normal, 3));
      if (m.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(m.uv, 2));
      if (m.color) g.setAttribute('color', new THREE.Float32BufferAttribute(m.color, 3));
      g.setIndex(m.index);
      if (!m.normal) g.computeVertexNormals();
      const mat = this.materialFor(m.material);
      let obj;
      if (m.skinIndex) {
        g.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(m.skinIndex, 4));
        g.setAttribute('skinWeight', new THREE.Float32BufferAttribute(m.skinWeight, 4));
        obj = new THREE.SkinnedMesh(g, mat);
        this.figure.add(obj);
        obj.bind(this.skeleton, new THREE.Matrix4());
      } else {
        obj = new THREE.Mesh(g, mat);
        const bone = this.bones.find(b => b.name === m.boneName) || this.boneByCommon(m.bone) || this.figure;
        bone.add(obj);
      }
      obj.name = `${part.label} ${m.mesh}`;
      obj.userData = { part: part.id, slot: part.slot, materialKey: m.material.key };   // exported as glTF extras
      if (m.material.occluder) obj.userData.occluder = true;
      obj.frustumCulled = false;
      objs.push(obj);
    }
    // a part with an occluder (the newer games' faces): the rest of the figure first, then the occluder (depth
    // only), then the part's other pieces, so the inside of the mouth shows only where the mouth is open
    if (objs.some(o => o.userData.occluder)) for (const o of objs) o.renderOrder = o.userData.occluder ? 1 : 2;
    this.parts.set(part.id, objs);
    return objs;
  }

  removePart(id) {
    for (const o of this.parts.get(id) || []) {
      o.parent && o.parent.remove(o);
      o.geometry && o.geometry.dispose();
    }
    this.parts.delete(id);
  }

  // an uploaded object: rigid on a bone, or skinned to the nearest bones
  addCustom(id, object, bone, mode) {
    this.removePart(id);
    const objs = [];
    object.updateMatrixWorld(true);
    const target = this.boneByCommon(bone) || this.figure;
    this.restPose();
    if (mode === 'skin') {
      const joints = this.bones.map(b => new THREE.Vector3().setFromMatrixPosition(b.matrixWorld));
      object.traverse(o => {
        if (!o.isMesh) return;
        const g = o.geometry.clone().applyMatrix4(o.matrixWorld);
        const pos = g.attributes.position, n = pos.count;
        const si = new Uint16Array(n * 4), sw = new Float32Array(n * 4), v = new THREE.Vector3();
        for (let i = 0; i < n; i++) {
          // the two nearest joints, weighted by inverse square distance (a vertex on a joint follows it alone)
          v.fromBufferAttribute(pos, i);
          let b1 = 0, d1 = Infinity, b2 = 0, d2 = Infinity;
          joints.forEach((j, k) => {
            const d = j.distanceToSquared(v);
            if (d < d1) { b2 = b1; d2 = d1; b1 = k; d1 = d; } else if (d < d2) { b2 = k; d2 = d; }
          });
          const w1 = 1 / Math.max(d1, 1e-10), w2 = d2 < Infinity ? 1 / Math.max(d2, 1e-10) : 0;
          si[i * 4] = b1; si[i * 4 + 1] = b2;
          sw[i * 4] = w1 / (w1 + w2); sw[i * 4 + 1] = w2 / (w1 + w2);
        }
        g.setAttribute('skinIndex', new THREE.BufferAttribute(si, 4));
        g.setAttribute('skinWeight', new THREE.BufferAttribute(sw, 4));
        const mesh = new THREE.SkinnedMesh(g, this.customMaterial(o.material));
        this.figure.add(mesh);
        mesh.bind(this.skeleton, new THREE.Matrix4());
        mesh.userData = { part: id, slot: 'custom', materialKey: mesh.material.name };
        mesh.frustumCulled = false;
        objs.push(mesh);
      });
    } else {
      const holder = new THREE.Group();
      holder.add(object);
      object.traverse(o => { if (o.isMesh) { o.material = this.customMaterial(o.material); o.userData.part = id; o.userData.materialKey = o.material.name; } });
      target.add(holder);
      holder.userData = { part: id, slot: 'custom' };
      objs.push(holder);
    }
    this.parts.set(id, objs);
    return objs;
  }

  customMaterial(src) {
    const s = Array.isArray(src) ? src[0] : src;
    const key = 'custom:' + (s && s.uuid || THREE.MathUtils.generateUUID());
    const colour = s && s.color ? [s.color.r, s.color.g, s.color.b, 1] : [0.8, 0.8, 0.8, 1];
    return this.materialFor({ key, colour, texture: null, alpha: false, mapOverride: s && s.map || null });
  }

  // -- animation -------------------------------------------------------------------------------------------
  playClip(clipData, loop = true) {
    this.stop();
    const tracks = [];
    const dt = 1 / (clipData.fps || 30);
    const times = Array.from({ length: clipData.frames }, (_, i) => i * dt);
    for (const t of clipData.tracks) {
      const bone = this.bones.find(b => b.userData.common === t.bone);
      if (!bone) continue;
      const name = bone.uuid;
      tracks.push(new THREE.VectorKeyframeTrack(`${name}.position`, times, t.t));
      tracks.push(new THREE.QuaternionKeyframeTrack(`${name}.quaternion`, times, t.q));
      tracks.push(new THREE.VectorKeyframeTrack(`${name}.scale`, times, t.s));
    }
    const clip = new THREE.AnimationClip(clipData.name, times[times.length - 1] || 0, tracks);
    clip.userData = { fps: clipData.fps, frames: clipData.frames, byName: {} };
    // a copy addressed by bone name, for exporters
    clip.userData.named = new THREE.AnimationClip(clipData.name, clip.duration, tracks.map(tr => {
      const bone = this.bones.find(b => tr.name.startsWith(b.uuid));
      const c = tr.clone(); c.name = tr.name.replace(bone.uuid, bone.name); return c;
    }));
    this.mixer = new THREE.AnimationMixer(this.figure);
    this.action = this.mixer.clipAction(clip);
    this.action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    this.action.clampWhenFinished = true;
    this.action.play();
    this.playing = true;
    this.clip = clip;
    return clip;
  }

  stop() {
    if (this.mixer) { this.mixer.stopAllAction(); this.mixer.uncacheRoot(this.figure); }
    this.mixer = null; this.action = null; this.clip = null; this.playing = false;
    this.showRest();
  }

  seek(fraction) {
    if (!this.action) return;
    this.action.time = fraction * this.clip.duration;
    this.mixer.update(0);
  }

  frameFigure() {
    const box = new THREE.Box3().setFromObject(this.figure, true);
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length();
    this.controls.target.copy(c);
    const dir = this.camera.position.clone().sub(c).normalize();
    this.camera.position.copy(c).addScaledVector(dir, size * 1.9);
    this.camera.near = size / 100; this.camera.far = size * 50; this.camera.updateProjectionMatrix();
  }

  snapshot(width, height) {
    const old = this.renderer.getSize(new THREE.Vector2());
    if (width) {
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height; this.camera.updateProjectionMatrix();
    }
    const grid = this.grid.visible, helper = this.helper && this.helper.visible;
    this.grid.visible = false; if (this.helper) this.helper.visible = false;
    this.renderer.render(this.scene, this.camera);
    const url = this.renderer.domElement.toDataURL('image/png');
    this.grid.visible = grid; if (this.helper) this.helper.visible = helper;
    if (width) {
      this.renderer.setSize(old.x, old.y, false);
      this.camera.aspect = old.x / old.y; this.camera.updateProjectionMatrix();
    }
    return url;
  }
}
