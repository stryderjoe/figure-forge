// Figure Forge: the steps (body, parts, colours, prints, render, rig, animate, export) around one figure.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { LDrawLoader } from 'three/addons/loaders/LDrawLoader.js';
import { Viewer, STYLES } from './viewer.js';
import { PrintEditor } from './prints.js';
import { LEGO_COLOURS, hexToRgb, rgbToHex } from './palette.js';
import { FORMATS, exportFigure } from './exporters.js';

const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
};
const status = text => { $('#status').textContent = text || ''; };

const viewer = new Viewer($('#view'));
const S = {
  settings: null, ready: [], titles: {}, index: {}, base: null, skeleton: null, parts: [], colours: {},
  customs: [], step: 'body', clipSource: null, clipName: null, slotFilter: 'head', gameFilter: 'all',
  search: '', exportName: 'my_figure', lastSaved: '',
};
let prints = null;
const MULTI = new Set(['held', 'accessory', 'back', 'custom', 'neck', 'face']);
const SLOT_ORDER = ['head', 'face', 'hair', 'headgear', 'neck', 'torso', 'arms', 'hands', 'hips', 'legs', 'cape', 'back', 'held', 'accessory'];

// -- data ------------------------------------------------------------------------------------------------
async function loadSettings() {
  const r = await api('/api/settings');
  S.settings = r.settings;
  S.titles = Object.fromEntries(Object.entries(r.games).map(([k, v]) => [k, v.title]));
  S.ready = Object.entries(r.games).filter(([, v]) => v.ready).map(([k]) => k);
  S.blender = r.blender;
  for (const gid of S.ready) {
    status(`Indexing ${S.titles[gid]} ...`);
    S.index[gid] = (await api(`/api/characters?game=${gid}`)).characters;
  }
  status('');
}

function allChars(humanoidOnly) {
  const out = [];
  for (const gid of S.ready) for (const c of S.index[gid] || []) if (!humanoidOnly || c.humanoid) out.push({ ...c, game: gid });
  return out;
}

function partEntry(id) {
  const cid = id.split('|')[0], gid = cid.split(':')[0];
  const c = (S.index[gid] || []).find(x => x.id === cid);
  const p = c && c.parts.find(x => x.id === id);
  return p ? { ...p, char: c } : null;
}

async function setBase(cid, { keepParts = false } = {}) {
  status('Loading body ...');
  const keep = keepParts ? S.parts.map(p => p.id) : null;
  S.skeleton = await api(`/api/base?char=${encodeURIComponent(cid)}`);
  S.base = cid;
  viewer.setBase(S.skeleton);
  S.parts = [];
  const gid = cid.split(':')[0];
  const char = S.index[gid].find(c => c.id === cid);
  const ids = keep || char.parts.filter(p => p.default).map(p => p.id);
  for (const id of ids) await addPart(id, { replace: false });
  for (const c of S.customs) await attachCustom(c);
  applyColours();
  viewer.frameFigure();
  S.clipSource = S.clipSource || cid;
  status('');
}

async function addPart(id, { replace = true } = {}) {
  if (!S.base) return;
  const e = partEntry(id);
  if (!e) return;
  if (replace && !MULTI.has(e.slot)) {
    for (const old of S.parts.filter(p => p.slot === e.slot)) removePart(old.id);
  }
  if (S.parts.some(p => p.id === id)) return;
  const part = await api(`/api/part?id=${encodeURIComponent(id)}&base=${encodeURIComponent(S.base)}`);
  viewer.addPart(part);
  S.parts.push({ id, slot: e.slot, label: `${e.char.name} · ${e.label}` });
  applyColours();
}

function removePart(id) {
  viewer.removePart(id);
  S.parts = S.parts.filter(p => p.id !== id);
}

function applyColours() {
  for (const [key, rgb] of Object.entries(S.colours)) {
    const m = viewer.materials.get(key);
    if (m) m.color.setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace);
  }
}

function figureMaterials() {
  const used = new Map();
  viewer.figure.traverse(o => {
    if (!o.isMesh) return;
    const key = o.userData.materialKey;
    if (!key || !viewer.materials.has(key)) return;
    if (!used.has(key)) used.set(key, { key, mat: viewer.materials.get(key), parts: new Set() });
    used.get(key).parts.add((S.parts.find(p => p.id === o.userData.part) || S.customs.find(c => c.id === o.userData.part) || { label: o.userData.slot }).label);
  });
  return [...used.values()];
}

// -- uploads ---------------------------------------------------------------------------------------------
const UNIT = { m: 1, cm: 0.01, mm: 0.001, ldu: 0.00388 };    // the games' minifig: 0.388 cm per LDraw unit

async function loadUpload(c) {
  const ext = c.ext;
  const buf = Uint8Array.from(atob(c.data.split(',')[1]), ch => ch.charCodeAt(0)).buffer;
  let obj;
  if (ext === 'glb' || ext === 'gltf') obj = (await new GLTFLoader().parseAsync(ext === 'glb' ? buf : new TextDecoder().decode(buf), '')).scene;
  else if (ext === 'obj') obj = new OBJLoader().parse(new TextDecoder().decode(buf));
  else if (ext === 'stl') obj = new THREE.Mesh(new STLLoader().parse(buf), new THREE.MeshPhongMaterial({ color: 0xcccccc }));
  else if (ext === 'fbx') obj = new FBXLoader().parse(buf, '');
  else if (['ldr', 'mpd', 'dat'].includes(ext)) {
    const loader = new LDrawLoader();
    if (S.settings.ldraw) loader.setPartsLibraryPath('/ldraw/');
    obj = await new Promise((res, rej) => loader.parse(new TextDecoder().decode(buf), res, rej));
    obj.rotation.x = Math.PI;                      // LDraw is Y-down
  } else throw new Error(`.${ext} files cannot be read`);
  const wrap = new THREE.Group();
  wrap.add(obj);
  const unit = UNIT[c.unit] || 1;
  wrap.scale.setScalar(unit * c.t.s);
  wrap.position.set(c.t.px, c.t.py, c.t.pz);
  wrap.rotation.set(THREE.MathUtils.degToRad(c.t.rx), THREE.MathUtils.degToRad(c.t.ry), THREE.MathUtils.degToRad(c.t.rz));
  obj.traverse(o => { if (o.isLineSegments || o.isLine) o.visible = false; });
  return wrap;
}

async function attachCustom(c) {
  try {
    const obj = await loadUpload(c);
    if (c.mode === 'skin') {
      // skin from the figure's own space: put the object where the chosen bone is
      viewer.restPose();
      const bone = viewer.boneByCommon(c.bone);
      if (bone) obj.applyMatrix4(bone.matrixWorld);
    }
    viewer.addCustom(c.id, obj, c.bone, c.mode);
  } catch (e) {
    status(`Could not load ${c.name}: ${e.message}`);
  }
}

// -- panels ----------------------------------------------------------------------------------------------
const PANELS = {};

PANELS.body = () => {
  const list = el('div', { class: 'list' });
  const fill = () => {
    list.innerHTML = '';
    const q = S.search.toLowerCase();
    for (const c of allChars(true)) {
      if (S.gameFilter !== 'all' && c.game !== S.gameFilter) continue;
      if (q && !c.name.toLowerCase().includes(q) && !c.folder.toLowerCase().includes(q)) continue;
      list.append(el('div', { class: 'item' + (c.id === S.base ? ' on' : ''), onclick: async () => { await setBase(c.id); render(); } },
        el('span', {}, c.name), el('small', {}, `${c.game.toUpperCase()} · ${c.bones} bones`)));
    }
  };
  fill();
  return [
    el('h2', {}, 'Body'),
    el('p', { class: 'hint' }, 'Pick the character whose skeleton and body your figure is built on. Its own look comes with it; change anything in the next steps. Parts and animations from the other games are fitted to this body.'),
    gameSelect(fill),
    el('input', { type: 'search', placeholder: 'Search characters', value: S.search, oninput: e => { S.search = e.target.value; fill(); } }),
    el('div', { style: 'height:6px' }), list,
    S.ready.length ? null : el('p', { class: 'bad' }, 'No game is set up yet: open Settings (the gear, top right).'),
  ];
};

function gameSelect(onchange) {
  return el('div', { class: 'row' }, el('select', { onchange: e => { S.gameFilter = e.target.value; onchange(); } },
    el('option', { value: 'all' }, 'All games'),
    ...S.ready.map(g => el('option', { value: g, selected: S.gameFilter === g }, S.titles[g]))));
}

PANELS.parts = () => {
  if (!S.base) return needBase();
  const slots = el('div');
  const present = new Set(S.parts.map(p => p.slot));
  for (const slot of SLOT_ORDER) {
    const mine = S.parts.filter(p => p.slot === slot);
    if (!mine.length && !['head', 'hair', 'headgear', 'torso', 'legs', 'held', 'back', 'cape'].includes(slot) && !present.has(slot)) continue;
    slots.append(el('div', { class: 'slot' },
      el('div', { class: 'head' }, el('span', { class: 'name' }, slot),
        el('button', { onclick: () => { S.slotFilter = slot; render(); setTimeout(() => $('#picker-search') && $('#picker-search').focus()); } }, mine.length && !MULTI.has(slot) ? 'Change' : 'Add')),
      ...mine.map(p => el('span', { class: 'chip' }, p.label, el('button', { title: 'Remove', onclick: () => { removePart(p.id); render(); } }, '×')))));
  }
  for (const c of S.customs) {
    slots.append(el('div', { class: 'slot' }, el('div', { class: 'head' }, el('span', { class: 'name' }, 'Your part'),
      el('span', { class: 'chip' }, `${c.name} on ${c.bone}`, el('button', { onclick: () => { viewer.removePart(c.id); S.customs = S.customs.filter(x => x !== c); render(); } }, '×')))));
  }
  const list = el('div', { class: 'list' });
  const fill = () => {
    list.innerHTML = '';
    const q = (S.partSearch || '').toLowerCase();
    let n = 0;
    for (const c of allChars(false)) {
      if (S.gameFilter !== 'all' && c.game !== S.gameFilter) continue;
      for (const p of c.parts) {
        if (p.slot !== S.slotFilter) continue;
        const text = `${c.name} ${p.label}`.toLowerCase();
        if (q && !text.includes(q)) continue;
        if (++n > 400) break;
        const on = S.parts.some(x => x.id === p.id);
        list.append(el('div', { class: 'item' + (on ? ' on' : ''), onclick: async () => {
          if (on) removePart(p.id); else { status('Fitting part ...'); await addPart(p.id); status(''); }
          render();
        } }, el('span', {}, c.name, el('small', {}, ' · ' + p.label)), el('small', {}, c.game.toUpperCase())));
      }
    }
    if (!n) list.append(el('div', { class: 'item' }, el('small', {}, 'Nothing matches.')));
  };
  fill();
  return [
    el('h2', {}, 'Parts'), el('p', { class: 'hint' }, 'Each slot holds one piece (held items, back items and accessories can hold several). Pieces from any character and any of the three games are fitted to your body.'),
    slots,
    el('h3', {}, 'Choose a part'),
    el('div', { class: 'row' },
      el('select', { onchange: e => { S.slotFilter = e.target.value; fill(); } }, ...SLOT_ORDER.map(s => el('option', { value: s, selected: s === S.slotFilter }, s))),
      gameSelect(fill)),
    el('input', { type: 'search', id: 'picker-search', placeholder: 'Search parts (character or piece name)', value: S.partSearch || '', oninput: e => { S.partSearch = e.target.value; fill(); } }),
    el('div', { style: 'height:6px' }), list,
    el('h3', {}, 'Upload your own part'),
    uploadForm(),
  ];
};

function uploadForm() {
  const f = { unit: 'auto', bone: 'head', mode: 'rigid' };
  const bones = (S.skeleton ? S.skeleton.bones : []).map(b => b.common);
  const input = el('input', { type: 'file', accept: '.glb,.gltf,.obj,.stl,.fbx,.ldr,.mpd,.dat' });
  return el('div', {},
    el('p', { class: 'hint' }, 'OBJ, glTF/GLB, STL, FBX or LDraw (.ldr / .mpd / .dat; set the LDraw library in Settings for parts by number). Attach it to a bone, or skin it to the nearest bones. Adjust it under Rig.'),
    input,
    el('label', {}, 'Units', el('select', { onchange: e => { f.unit = e.target.value; } },
      ...[['auto', 'Automatic'], ['ldu', 'LDraw units'], ['m', 'Metres'], ['cm', 'Centimetres'], ['mm', 'Millimetres']].map(([v, t]) => el('option', { value: v }, t)))),
    el('label', {}, 'Attach to bone', el('select', { onchange: e => { f.bone = e.target.value; } }, ...bones.map(b => el('option', { value: b, selected: b === 'head' }, b)))),
    el('label', {}, 'Binding', el('select', { onchange: e => { f.mode = e.target.value; } }, el('option', { value: 'rigid' }, 'Rigid on the bone'), el('option', { value: 'skin' }, 'Skin to nearest bones'))),
    el('div', { class: 'row' }, el('button', { class: 'primary', onclick: async () => {
      const file = input.files[0]; if (!file) return;
      const ext = file.name.split('.').pop().toLowerCase();
      const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(file); });
      const unit = f.unit === 'auto' ? (['ldr', 'mpd', 'dat'].includes(ext) ? 'ldu' : ext === 'stl' ? 'mm' : 'm') : f.unit;
      const c = { id: 'custom:' + Date.now(), name: file.name, ext, data, unit, bone: f.bone, mode: f.mode, t: { px: 0, py: 0, pz: 0, rx: 0, ry: 0, rz: 0, s: 1 } };
      S.customs.push(c);
      await attachCustom(c);
      render();
    } }, 'Add to figure')));
}

PANELS.colours = () => {
  if (!S.base) return needBase();
  const mats = figureMaterials();
  S.colourKey = S.colourKey && mats.some(m => m.key === S.colourKey) ? S.colourKey : (mats[0] && mats[0].key);
  const cur = mats.find(m => m.key === S.colourKey);
  const set = rgb => {
    S.colours[S.colourKey] = rgb;
    applyColours();
    render();
  };
  return [
    el('h2', {}, 'Colours'), el('p', { class: 'hint' }, 'Pick a surface, then a LEGO colour (or any colour). A print is tinted by its surface colour, as in the games; use white to show a print as drawn.'),
    el('div', {}, ...mats.map(m => el('div', { class: 'mat' + (m.key === S.colourKey ? ' on' : ''), onclick: () => { S.colourKey = m.key; render(); } },
      el('div', { class: 'sw', style: `background:#${m.mat.color.getHexString(THREE.SRGBColorSpace)}` }),
      el('div', {}, [...m.parts].slice(0, 2).join(', '), el('small', {}, m.mat.map ? 'printed' : 'plain'))))),
    cur ? el('div', {},
      el('h3', {}, 'LEGO colours'),
      el('div', { class: 'swatches' }, ...LEGO_COLOURS.map(([name, hex]) => el('div', { title: name, style: `background:#${hex}`, onclick: () => set(hexToRgb(hex)) }))),
      el('div', { class: 'row' }, el('label', { class: 'inline' }, 'Any colour', el('input', { type: 'color', value: rgbToHex(S.colours[S.colourKey] || [1, 1, 1]), oninput: e => set(hexToRgb(e.target.value)) })),
        el('button', { onclick: () => { delete S.colours[S.colourKey]; const m = viewer.materials.get(S.colourKey); const c = m.userData.info.colour; m.color.setRGB(c[0], c[1], c[2], THREE.SRGBColorSpace); render(); } }, 'Original colour'))) : null,
  ];
};

PANELS.prints = () => {
  if (!S.base) return needBase();
  const mats = figureMaterials();
  const canvas = el('canvas', { id: 'print-canvas' });
  // one editor per panel drawing, sharing the prints made so far
  const kept = prints ? prints.prints : null;
  prints = new PrintEditor(canvas, viewer, () => drawLayerControls());
  if (kept) prints.prints = kept;
  S.printKey = S.printKey && mats.some(m => m.key === S.printKey) ? S.printKey : (mats.find(m => m.mat.map) || mats[0] || {}).key;
  const controls = el('div');
  const drawLayerControls = () => {
    controls.innerHTML = '';
    const s = prints.state(); if (!s) return;
    controls.append(el('div', { class: 'row' },
      el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: s.showBase, disabled: !s.base, onchange: e => { s.showBase = e.target.checked; prints.apply(); } }), 'Original print'),
      el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: prints.showUV, onchange: e => { prints.showUV = e.target.checked; prints.draw(); } }), 'UV guide'),
      el('label', { class: 'inline' }, 'Fill', el('input', { type: 'color', value: s.fill || '#ffffff', oninput: e => { s.fill = e.target.value; prints.apply(); } })),
      el('button', { onclick: () => { s.fill = null; prints.apply(); drawLayerControls(); } }, 'No fill')));
    const L = s.layers[prints.selected];
    if (L) {
      const slider = (name, key, min, max, step) => el('label', {}, `${name}: ${(+L[key]).toFixed(2)}`,
        el('input', { type: 'range', min, max, step, value: L[key], class: 'wide', oninput: e => { L[key] = +e.target.value; prints.apply(); e.target.previousSibling.textContent = `${name}: ${(+L[key]).toFixed(2)}`; } }));
      controls.append(el('h3', {}, `Image: ${L.name}`),
        slider('X', 'x', -s.w * 0.5, s.w * 1.5, 1), slider('Y', 'y', -s.h * 0.5, s.h * 1.5, 1),
        slider('Scale', 'scale', 0.01, 4, 0.005), slider('Rotation', 'rot', -180, 180, 1), slider('Opacity', 'opacity', 0, 1, 0.01),
        el('div', { class: 'row' },
          el('button', { onclick: () => { L.flip = !L.flip; prints.apply(); } }, 'Mirror'),
          el('button', { onclick: () => { L.x = s.w / 2; L.y = s.h / 2; L.scale = Math.min(s.w / L.img.width, s.h / L.img.height); L.rot = 0; prints.apply(); drawLayerControls(); } }, 'Fill the print'),
          el('button', { onclick: () => { s.layers.splice(prints.selected, 1); prints.selected = s.layers.length - 1; prints.apply(); drawLayerControls(); } }, 'Remove image')));
    }
  };
  const file = el('input', { type: 'file', accept: 'image/*', onchange: async e => { if (e.target.files[0]) { await prints.addImage(e.target.files[0]); drawLayerControls(); } } });
  queueMicrotask(async () => { if (S.printKey) { await prints.open(S.printKey); drawLayerControls(); } });
  return [
    el('h2', {}, 'Prints'), el('p', { class: 'hint' }, 'Choose a surface, then add your own picture. Drag it on the print, scroll to scale, Shift+scroll to rotate; the blue lines show where the part\'s faces take their print from.'),
    el('select', { class: 'wide', onchange: async e => { S.printKey = e.target.value; await prints.open(S.printKey); drawLayerControls(); } },
      ...mats.map(m => el('option', { value: m.key, selected: m.key === S.printKey }, `${[...m.parts].slice(0, 2).join(', ')}${m.mat.map ? ' (printed)' : ''}`))),
    el('div', { style: 'height:8px' }), canvas,
    el('div', { class: 'row' }, file, el('button', { onclick: () => { prints.reset(); drawLayerControls(); } }, 'Reset print')),
    controls,
  ];
};

PANELS.render = () => [
  el('h2', {}, 'Render'), el('p', { class: 'hint' }, '"Classic game" copies the 2008 games\' look: shiny plastic under a key, fill and rim light.'),
  ...Object.entries(STYLES).map(([k, s]) => el('label', { class: 'inline', style: 'display:flex;margin:6px 0' },
    el('input', { type: 'radio', name: 'style', checked: viewer.styleName === k, onchange: () => { viewer.setStyle(k); viewer.restyleAll((key, old) => S.colours[key] || old.userData.info.colour); applyColours(); } }), s.label)),
  el('h3', {}, 'View'),
  el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: viewer.grid.visible, onchange: e => { viewer.grid.visible = e.target.checked; } }), 'Floor grid'),
  el('div', { class: 'row' }, el('button', { onclick: () => viewer.frameFigure() }, 'Frame figure'),
    el('button', { class: 'primary', onclick: () => doExport('png') }, 'Save render (PNG)')),
];

PANELS.rig = () => {
  if (!S.base) return needBase();
  const tree = S.skeleton.bones.map((b, i) => {
    let d = 0, p = b.parent; while (p >= 0) { d++; p = S.skeleton.bones[p].parent; }
    return `${'  '.repeat(d)}${b.name}${b.common !== b.name.toLowerCase() ? '  (' + b.common + ')' : ''}`;
  }).join('\n');
  return [
    el('h2', {}, 'Rig'), el('p', { class: 'hint' }, `Your figure is rigged on ${S.skeleton.name}'s skeleton (${S.skeleton.bones.length} bones, scheme ${S.skeleton.scheme}). Game parts are skinned with the games' own weights.`),
    el('label', { class: 'inline' }, el('input', { type: 'checkbox', checked: !!viewer.showBones, onchange: e => { viewer.showBones = e.target.checked; viewer.helper.visible = e.target.checked; } }), 'Show bones'),
    S.customs.length ? el('h3', {}, 'Your parts') : null,
    ...S.customs.map(c => customControls(c)),
    el('h3', {}, 'Skeleton'), el('div', { class: 'tree' }, tree),
  ];
};

function customControls(c) {
  const bones = S.skeleton.bones.map(b => b.common);
  const re = async () => { await attachCustom(c); };
  const slider = (name, key, min, max, step) => el('label', {}, `${name}: ${(+c.t[key]).toFixed(3)}`,
    el('input', { type: 'range', min, max, step, value: c.t[key], class: 'wide', onchange: async e => { c.t[key] = +e.target.value; await re(); render(); } }));
  return el('div', { class: 'slot' }, el('div', { class: 'name' }, c.name),
    el('label', {}, 'Bone', el('select', { onchange: async e => { c.bone = e.target.value; await re(); } }, ...bones.map(b => el('option', { value: b, selected: b === c.bone }, b)))),
    el('label', {}, 'Binding', el('select', { onchange: async e => { c.mode = e.target.value; await re(); } },
      el('option', { value: 'rigid', selected: c.mode === 'rigid' }, 'Rigid on the bone'), el('option', { value: 'skin', selected: c.mode === 'skin' }, 'Skin to nearest bones'))),
    slider('Offset X', 'px', -0.3, 0.3, 0.001), slider('Offset Y', 'py', -0.3, 0.3, 0.001), slider('Offset Z', 'pz', -0.3, 0.3, 0.001),
    slider('Turn X', 'rx', -180, 180, 1), slider('Turn Y', 'ry', -180, 180, 1), slider('Turn Z', 'rz', -180, 180, 1),
    slider('Scale', 's', 0.05, 5, 0.01));
}

PANELS.animate = () => {
  if (!S.base) return needBase();
  const src = S.clipSource || S.base;
  const list = el('div', { class: 'list' }, el('div', { class: 'item' }, el('small', {}, 'Loading clips ...')));
  const srcList = el('select', { class: 'wide', onchange: e => { S.clipSource = e.target.value; render(); } },
    ...allChars(true).map(c => el('option', { value: c.id, selected: c.id === src }, `${c.name} (${c.game.toUpperCase()})${c.id === S.base ? ' · your body' : ''}`)));
  (async () => {
    const r = await api(`/api/clips?char=${encodeURIComponent(src)}`);
    list.innerHTML = '';
    const q = (S.clipSearch || '').toLowerCase();
    for (const c of r.clips) {
      if (q && !`${c.name} ${c.action || ''}`.toLowerCase().includes(q)) continue;
      list.append(el('div', { class: 'item' + (c.name === S.clipName && src === S.playingSource ? ' on' : ''), onclick: () => playClip(src, c.name) },
        el('span', {}, c.name), el('small', {}, c.action || '')));
    }
    if (!r.clips.length) list.append(el('div', { class: 'item' }, el('small', {}, 'This character declares no clips.')));
  })();
  return [
    el('h2', {}, 'Animate'), el('p', { class: 'hint' }, 'Play the classic animations on your figure. Clips from another character (any game) are retargeted to your body.'),
    el('label', {}, 'Clips of', srcList),
    el('input', { type: 'search', placeholder: 'Search clips', value: S.clipSearch || '', oninput: e => { S.clipSearch = e.target.value; render(); } }),
    el('div', { style: 'height:6px' }), list,
  ];
};

async function playClip(src, name) {
  status(`Loading ${name} ...`);
  const q = `/api/clip?char=${encodeURIComponent(src)}&name=${encodeURIComponent(name)}` + (src !== S.base ? `&target=${encodeURIComponent(S.base)}` : '');
  const data = await api(q);
  viewer.playClip(data);
  S.clipName = name; S.playingSource = src;
  $('#timeline').hidden = false;
  $('#tl-play').innerHTML = '&#10074;&#10074;';
  status(`${name} · ${data.frames} frames at ${data.fps} fps${data.fpsAssumed ? ' (assumed)' : ''}${data.retargeted ? ' · retargeted' : ''}`);
  if (S.step === 'animate') render();
}

PANELS.export = () => [
  el('h2', {}, 'Export'), el('p', { class: 'hint' }, 'GLB and glTF keep the rig and the playing animation. OBJ, STL and PLY are the figure as it stands. FBX, .blend and USD go through Blender in the background' + (S.blender ? '.' : ' (not found: set it in Settings).')),
  el('label', {}, 'File name', el('input', { value: S.exportName, oninput: e => { S.exportName = e.target.value.replace(/[^\w\- ]/g, '_') || 'figure'; } })),
  ...FORMATS.map(f => el('div', { class: 'row' }, el('button', { class: 'wide', style: 'width:100%;text-align:left', disabled: f.blender && !S.blender, onclick: () => doExport(f.id) }, f.label))),
  S.lastSaved ? el('p', { class: 'hint ok' }, `Saved: ${S.lastSaved}`) : null,
];

async function doExport(fmt) {
  try {
    status(`Exporting ${fmt.toUpperCase()} ...`);
    await exportFigure(viewer, fmt, S.exportName, saveFile);
    status('');
  } catch (e) {
    status(`Export failed: ${e.message}`);
  }
  if (S.step === 'export') render();
}

async function saveFile(name, bytes) {
  const r = await fetch(`/api/save?name=${encodeURIComponent(name)}`, { method: 'POST', body: bytes });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error);
  S.lastSaved = data.path;
  status(`Saved ${data.path}`);
  return data.path;
}

function needBase() { return [el('h2', {}, 'No body yet'), el('p', { class: 'hint' }, 'Pick a body in step 1 first.')]; }

function render() {
  for (const b of document.querySelectorAll('#steps button')) b.classList.toggle('active', b.dataset.step === S.step);
  const panel = $('#panel');
  const scroll = panel.scrollTop;
  panel.innerHTML = '';
  panel.append(...[].concat(PANELS[S.step]()).filter(Boolean));
  panel.scrollTop = scroll;
}

// -- project files ---------------------------------------------------------------------------------------
function projectJson() {
  return { app: 'Figure Forge', version: 1, base: S.base, parts: S.parts.map(p => p.id), colours: S.colours,
           prints: prints ? prints.serialise() : {}, customs: S.customs, style: viewer.styleName,
           clip: S.clipName ? { source: S.playingSource, name: S.clipName } : null };
}

async function openProject(p) {
  S.customs = p.customs || [];
  S.colours = p.colours || {};
  S.parts = (p.parts || []).map(id => ({ id }));
  await setBase(p.base, { keepParts: true });
  if (p.style) { viewer.setStyle(p.style); viewer.restyleAll((key, old) => S.colours[key] || old.userData.info.colour); applyColours(); }
  if (p.prints && Object.keys(p.prints).length) {
    prints = prints || new PrintEditor(el('canvas'), viewer, () => {});
    await prints.restore(p.prints);
  }
  if (p.clip) await playClip(p.clip.source, p.clip.name);
  render();
}

// -- wiring ----------------------------------------------------------------------------------------------
for (const b of document.querySelectorAll('#steps button')) b.onclick = () => { S.step = b.dataset.step; render(); };
$('#tl-play').onclick = () => { viewer.playing = !viewer.playing; $('#tl-play').innerHTML = viewer.playing ? '&#10074;&#10074;' : '&#9654;'; };
$('#tl-stop').onclick = () => { viewer.stop(); $('#timeline').hidden = true; S.clipName = null; status(''); };
$('#tl-speed').onchange = e => { viewer.speed = +e.target.value; };
$('#tl-scrub').oninput = e => { viewer.playing = false; $('#tl-play').innerHTML = '&#9654;'; viewer.seek(e.target.value / 1000); };
viewer.onFrame = () => {
  if (!viewer.action) return;
  const f = viewer.action.time / Math.max(1e-6, viewer.clip.duration);
  if (viewer.playing) $('#tl-scrub').value = Math.round(f * 1000);
  const fps = viewer.clip.userData.fps;
  $('#tl-label').textContent = `${S.clipName}  ${Math.round(viewer.action.time * fps)} / ${viewer.clip.userData.frames - 1}`;
};
$('#btn-settings').onclick = () => {
  const f = $('#settings form');
  for (const g of ['lb1', 'indy', 'tcs']) f.elements[g].value = (S.settings.games || {})[g] || '';
  f.elements.ldraw.value = S.settings.ldraw || '';
  f.elements.blender.value = S.settings.blender || '';
  $('#settings-msg').textContent = S.blender ? `Blender: ${S.blender}` : 'Blender not found.';
  $('#settings').showModal();
};
$('#settings-save').onclick = async e => {
  e.preventDefault();
  const f = $('#settings form');
  const games = Object.fromEntries(['lb1', 'indy', 'tcs'].map(g => [g, f.elements[g].value.trim()]));
  const r = await api('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ games, ldraw: f.elements.ldraw.value.trim(), blender: f.elements.blender.value.trim() }) });
  $('#settings-msg').textContent = `Ready: ${r.ready.map(g => S.titles[g]).join(', ') || 'none'}`;
  await loadSettings();
  render();
};
$('#btn-new').onclick = async () => { S.customs = []; S.colours = {}; prints = null; if (S.base) await setBase(S.base); render(); };
$('#btn-save').onclick = async () => {
  if (!S.base) return;
  await saveFile(`${S.exportName}.forge.json`, new TextEncoder().encode(JSON.stringify(projectJson())));
  render();
};
$('#btn-open').onclick = () => $('#open-file').click();
$('#open-file').onchange = async e => { const f = e.target.files[0]; if (f) await openProject(JSON.parse(await f.text())); };

window.FF = { S, viewer, setBase, addPart, removePart, playClip, doExport, openProject, projectJson, render,
              setColour: (key, rgb) => { S.colours[key] = rgb; applyColours(); }, figureMaterials,
              get prints() { return prints; }, set prints(p) { prints = p; }, PrintEditor, attachCustom };

(async () => {
  await loadSettings();
  render();
  const params = new URLSearchParams(location.search);
  if (params.get('base')) {
    await setBase(params.get('base'));
    if (params.get('clip')) await playClip(params.get('source') || params.get('base'), params.get('clip'));
    if (params.get('step')) { S.step = params.get('step'); render(); }
  }
  document.title = 'Figure Forge';
  document.body.dataset.ready = '1';
  if (!S.ready.length) $('#btn-settings').click();
})().catch(e => { status('Error: ' + e.message); document.body.dataset.ready = 'error'; console.error(e); });
