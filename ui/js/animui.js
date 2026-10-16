// The Animate step's Create tab, the editor's timeline bar, bone tools for Rig, and the export hook for
// editor animations. Everything here works through the context `ctx` that main.js hands over.
import * as THREE from 'three';
import { newAnimation } from './editor.js';

export function setupAnimUI(ctx) {
  const { el, $, S, viewer, editor, faces, render, api, status } = ctx;

  // -- timeline bar (create mode) ---------------------------------------------------------------------
  const bar = $('#editbar');
  const slider = $('#eb-frame'), label = $('#eb-label'), ticks = $('#eb-ticks');
  $('#eb-play').onclick = () => { editor.playing = !editor.playing; if (editor.playing) editor.time = editor.frame / editor.anim.fps; drawBar(); };
  $('#eb-prev').onclick = () => { const k = editor.allKeyFrames().filter(f => f < editor.frame); editor.setFrame(k.length ? k[k.length - 1] : 0); };
  $('#eb-next').onclick = () => { const k = editor.allKeyFrames().filter(f => f > editor.frame); editor.setFrame(k.length ? k[0] : editor.anim.frames - 1); };
  $('#eb-key').onclick = () => { if (editor.selected) editor.key(editor.selected); };
  $('#eb-keyall').onclick = () => editor.keyAll();
  $('#eb-unkey').onclick = () => { if (editor.selected) editor.unkey(editor.selected); };
  slider.oninput = e => { editor.playing = false; editor.setFrame(+e.target.value); };

  function drawBar() {
    if (!editor.active) { bar.hidden = true; return; }
    bar.hidden = false;
    $('#timeline').hidden = true;      // the classic clip bar is replaced by the editor's
    slider.max = String(editor.anim.frames - 1);
    slider.value = String(editor.frame);
    label.textContent = `frame ${editor.frame} / ${editor.anim.frames - 1}` + (editor.selected ? `  ·  ${editor.selected.name}` : '');
    $('#eb-play').innerHTML = editor.playing ? '&#10074;&#10074;' : '&#9654;';
    ticks.innerHTML = '';
    const len = Math.max(1, editor.anim.frames - 1);
    const mine = new Set(editor.keyFrames(editor.selected));
    for (const f of editor.allKeyFrames()) {
      ticks.append(el('i', { class: mine.has(f) ? 'mine' : '', style: `left:${(f / len) * 100}%`, title: `key at frame ${f}` }));
    }
  }

  editor.onChange = what => {
    drawBar();
    if (what === 'select' || what === 'keys') {
      if (S.step === 'animate' && S.animMode === 'create' && !ctx.busy) render();
    }
  };

  // -- export hook: the editor's animation, faces as switched pieces on the head bone --------------------
  viewer.exportHook = () => {
    if (S.animMode !== 'create' || !editor.active) return null;
    const anim = editor.anim;
    if (!Object.keys(anim.keys).length && !Object.keys(anim.face).length) return null;
    viewer.restPose();
    faces.showAll(0);
    const clip = editor.buildClip();
    const tracks = [...clip.tracks];
    const added = [], hidden = [];
    const dt = 1 / anim.fps;
    const head = viewer.boneByCommon('head') || viewer.figure;
    viewer.figure.updateMatrixWorld(true);
    const toHead = head.matrixWorld.clone().invert();
    let n = 0;
    for (const [key, k] of Object.entries(anim.face)) {
      const list = faces.list(key);
      if (list.length < 2) continue;
      const meshes = [];
      viewer.figure.traverse(o => { if (o.isMesh && o.visible && o.userData.materialKey === key) meshes.push(o); });
      if (!meshes.length) continue;
      const keyed = [...new Set([0, ...Object.keys(k).map(Number)])].sort((a, b) => a - b);
      const times = keyed.map(f => f * dt);
      list.forEach((entry, idx) => {
        const g = new THREE.Group();
        g.name = `face${n}_${idx}_${entry.name.replace(/[^\w]/g, '')}`;
        for (const m of meshes) g.add(new THREE.Mesh(bakeTo(m, toHead), Object.assign(m.material.clone(), { map: faces.textureFor(key, idx) })));
        head.add(g);
        added.push(g);
        const vals = [];
        for (const f of keyed) { const s = editor.faceAt(key, f) === idx ? 1 : 0; vals.push(s, s, s); }
        tracks.push(new THREE.VectorKeyframeTrack(`${g.name}.scale`, times, vals, THREE.InterpolateDiscrete));
      });
      for (const m of meshes) { m.visible = false; hidden.push(m); }
      n++;
    }
    return {
      animations: [new THREE.AnimationClip(anim.name, clip.duration, tracks)],
      cleanup() {
        for (const g of added) g.parent && g.parent.remove(g);
        for (const m of hidden) m.visible = true;
        editor.evaluate(editor.frame);
      },
    };
  };

  function bakeTo(mesh, matrix) {
    const src = mesh.geometry, n = src.attributes.position.count, v = new THREE.Vector3();
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      mesh.getVertexPosition(i, v);
      v.applyMatrix4(mesh.matrixWorld).applyMatrix4(matrix);
      pos.set([v.x, v.y, v.z], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    if (src.attributes.uv) g.setAttribute('uv', src.attributes.uv.clone());
    if (src.index) g.setIndex(src.index.clone());
    g.computeVertexNormals();
    return g;
  }

  // -- panels ----------------------------------------------------------------------------------------
  function createPanel() {
    const a = editor.anim;
    const sel = editor.selected;
    const numberIn = (title, key, min, max) => el('label', {}, title, el('input', { type: 'number', min, max, value: a[key],
      onchange: e => { a[key] = Math.max(min, Math.min(max, +e.target.value || min)); editor.setFrame(Math.min(editor.frame, a.frames - 1)); render(); } }));
    const clipPick = el('select', { class: 'wide' }, el('option', { value: '' }, 'Copy a classic clip to edit ...'));
    api(`/api/clips?char=${encodeURIComponent(S.base)}`).then(r => {
      for (const c of r.clips) clipPick.append(el('option', { value: c.name }, `${c.name}${c.action ? ' (' + c.action + ')' : ''}`));
    });
    clipPick.onchange = async e => {
      if (!e.target.value) return;
      ctx.busy = true;
      await ctx.playClip(S.base, e.target.value);
      editor.fromViewerClip(2);
      S.animMode = 'create';
      ctx.busy = false;
      render();
    };
    const boneSelect = el('select', { class: 'wide', onchange: e => editor.select(viewer.bones.find(b => b.name === e.target.value) || null) },
      el('option', { value: '' }, 'Choose a bone (or click a joint)'),
      ...viewer.bones.map(b => el('option', { value: b.name, selected: b === sel }, `${b.name}${b.userData.common !== b.name.toLowerCase() ? ' · ' + b.userData.common : ''}`)));
    return [
      el('p', { class: 'hint' }, 'Click a joint (blue dot) to pick a bone and turn it with the rings; drag the timeline to another frame and pose again. Orange dots are animated bones, yellow the chosen one.'),
      el('label', {}, 'Name', el('input', { value: a.name, oninput: e => { a.name = e.target.value.replace(/[^\w\- ]/g, '_') || 'animation'; } })),
      el('div', { class: 'row' }, numberIn('Frames', 'frames', 2, 3600), numberIn('Frames a second', 'fps', 1, 120),
        el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: a.loop, onchange: e => { a.loop = e.target.checked; } }), 'Loop')),
      el('div', { class: 'row' },
        el('button', { onclick: () => { editor.load(newAnimation()); render(); } }, 'New empty'), clipPick),
      el('h3', {}, 'Pose'),
      boneSelect,
      el('div', { class: 'row' },
        el('button', { onclick: () => editor.setMode('rotate') }, 'Turn'),
        el('button', { onclick: () => editor.setMode('translate') }, 'Move'),
        el('button', { disabled: !sel, onclick: () => editor.resetBone(sel) }, 'Reset bone'),
        el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: editor.autoKey, onchange: e => { editor.autoKey = e.target.checked; } }), 'Auto-key')),
      el('div', { class: 'row' },
        el('button', { onclick: () => editor.copyPose() }, 'Copy pose'),
        el('button', { onclick: () => editor.pastePose() }, 'Paste pose'),
        el('button', { onclick: () => editor.mirrorPose() }, 'Mirror pose')),
      facePanel(),
      savedPanel(),
    ];
  }

  function faceSurfaces() {
    const out = new Map();
    viewer.figure.traverse(o => {
      if (!o.isMesh || !o.userData.materialKey || !viewer.materials.has(o.userData.materialKey)) return;
      const key = o.userData.materialKey;
      const isFace = o.userData.slot === 'face' || o.userData.slot === 'head';
      if (isFace || faces.sets.has(key)) out.set(key, (out.get(key) || []).concat(o.userData.slot));
    });
    return [...out.keys()];
  }

  function surfaceName(key) {
    let name = key;
    viewer.figure.traverse(o => { if (o.isMesh && o.userData.materialKey === key) name = o.name.replace(/ \d+$/, ''); });
    return name;
  }

  function facePanel() {
    const keys = faceSurfaces();
    if (!keys.length) return el('p', { class: 'hint' }, 'This figure has no face surface.');
    S.faceKey = keys.includes(S.faceKey) ? S.faceKey : (keys.find(k => viewer.materials.get(k).map) || keys[0]);
    const key = S.faceKey;
    const list = faces.list(key);
    const current = editor.faceAt(key, editor.frame);
    const nameIn = el('input', { placeholder: 'Expression name (Blink, Talk, Angry ...)', style: 'flex:1' });
    const file = el('input', { type: 'file', accept: 'image/*', hidden: true, onchange: async e => {
      const f = e.target.files[0]; if (!f) return;
      const src = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(f); });
      await faces.add(key, nameIn.value || f.name.replace(/\.\w+$/, ''), src);
      render();
    } });
    return el('div', {},
      el('h3', {}, 'Face expressions'),
      el('p', { class: 'hint' }, 'A classic face is a print, so an expression is a whole face picture. Make one in Prints (edit the face, then come back and add it as an expression), or upload one. Key an expression to show it from this frame on.'),
      keys.length > 1 ? el('select', { class: 'wide', onchange: e => { S.faceKey = e.target.value; render(); } },
        ...keys.map(k => el('option', { value: k, selected: k === key }, surfaceName(k)))) : null,
      el('div', { class: 'exprs' }, ...list.map((e, i) => el('div', { class: 'expr' + (i === current ? ' on' : '') },
        el('img', { src: e.src || faces.snapshot(key) || '' }),
        el('span', {}, e.name),
        el('button', { title: 'Show this expression from this frame on', onclick: () => editor.keyFace(key, i) }, 'Key'),
        i ? el('button', { title: 'Remove', onclick: () => { faces.remove(key, i); render(); } }, '×') : null))),
      el('div', { class: 'row' }, nameIn),
      el('div', { class: 'row' },
        el('button', { onclick: async () => { const src = faces.snapshot(key); if (src) { await faces.add(key, nameIn.value || `Expression ${list.length}`, src); render(); } } }, 'Add the current print'),
        el('button', { onclick: () => file.click() }, 'Add an image'), file,
        el('button', { onclick: () => editor.unkeyFace(key) }, 'Remove face key here')));
  }

  function savedPanel() {
    S.anims = S.anims || [];
    return el('div', {},
      el('h3', {}, 'Your animations'),
      el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => {
        const i = S.anims.findIndex(x => x.name === editor.anim.name);
        if (i >= 0) S.anims[i] = editor.serialise(); else S.anims.push(editor.serialise());
        status(`Saved animation ${editor.anim.name} in this project`); render();
      } }, 'Save in project')),
      ...S.anims.map((x, i) => el('div', { class: 'row' }, el('span', { style: 'flex:1' }, `${x.name} · ${x.frames} frames`),
        el('button', { onclick: () => { editor.load(x); render(); } }, 'Edit'),
        el('button', { onclick: () => { S.anims.splice(i, 1); render(); } }, '×'))));
  }

  // -- rig: add bones -----------------------------------------------------------------------------------
  function bonePanel() {
    const f = { name: '', parent: 'head', x: 0, y: 0.05, z: 0 };
    const commons = viewer.bones.map(b => b.name);
    return el('div', {},
      el('h3', {}, 'Add a bone'),
      el('p', { class: 'hint' }, 'For tails, wings, antennae or anything that should move on its own. Place it relative to its parent (metres; a figure is about 0.4 tall), then attach or skin your parts to it and animate it under Animate.'),
      el('label', {}, 'Name', el('input', { placeholder: 'tail_1', oninput: e => { f.name = e.target.value.replace(/[^\w]/g, '_'); } })),
      el('label', {}, 'Parent', el('select', { onchange: e => { f.parent = e.target.value; } }, ...commons.map(n => el('option', { value: n, selected: n.toLowerCase() === 'head' }, n)))),
      el('div', { class: 'row' }, ...['x', 'y', 'z'].map(k => el('label', { style: 'flex:1' }, k, el('input', { type: 'number', step: 0.005, value: f[k], oninput: e => { f[k] = +e.target.value; } })))),
      el('div', { class: 'row' }, el('button', { class: 'primary', onclick: () => {
        if (!f.name) { status('Give the bone a name'); return; }
        const parent = commons.find(n => n.toLowerCase() === f.parent.toLowerCase()) || f.parent;
        if (!viewer.addBone(f.name, parent, [f.x, f.y, f.z])) { status('A bone of that name exists'); return; }
        S.customBones = (S.customBones || []).concat([{ name: f.name, parent, offset: [f.x, f.y, f.z] }]);
        editor.active && editor.buildHandles();
        render();
      } }, 'Add bone')));
  }

  return { createPanel, bonePanel, drawBar };
}
