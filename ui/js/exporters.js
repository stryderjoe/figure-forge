// Exports: GLB / glTF (rigged, with the playing clip), OBJ+MTL+textures (zip), STL, PLY (posed),
// PNG render, and FBX / .blend / Collada / USD through Blender on the server.
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { OBJExporter } from 'three/addons/exporters/OBJExporter.js';
import { STLExporter } from 'three/addons/exporters/STLExporter.js';
import { PLYExporter } from 'three/addons/exporters/PLYExporter.js';
import { zipSync, strToU8 } from 'three/addons/libs/fflate.module.js';

export const FORMATS = [
  { id: 'glb', label: 'GLB (rigged + animation)', ext: 'glb' },
  { id: 'gltf', label: 'glTF (rigged + animation)', ext: 'gltf' },
  { id: 'fbx', label: 'FBX (via Blender)', ext: 'fbx', blender: true },
  { id: 'blend', label: 'Blender .blend (via Blender)', ext: 'blend', blender: true },
  { id: 'usd', label: 'USD (via Blender)', ext: 'usd', blender: true },
  { id: 'obj', label: 'OBJ + MTL + textures (posed, zip)', ext: 'zip' },
  { id: 'stl', label: 'STL (posed, for printing)', ext: 'stl' },
  { id: 'ply', label: 'PLY (posed, vertex colours)', ext: 'ply' },
  { id: 'png', label: 'PNG render', ext: 'png' },
];

function gltf(viewer, binary) {
  // export from the bind pose, so the skin's rest pose is right; the clip carries the motion
  const playing = viewer.action, time = playing ? playing.time : 0;
  if (playing) { viewer.mixer.stopAllAction(); viewer.restPose(); }
  return new Promise((resolve, reject) => {
    new GLTFExporter().parse(viewer.figure, result => {
      if (playing) { playing.play(); playing.time = time; viewer.mixer.update(0); }
      resolve(binary ? new Uint8Array(result) : strToU8(JSON.stringify(result, null, 1)));
    }, reject, { binary, animations: viewer.clip ? [viewer.clip.userData.named] : [], onlyVisible: true });
  });
}

// the figure as it stands now: skinning applied, one plain mesh per part mesh, world space
function posed(viewer) {
  const group = new THREE.Group();
  viewer.figure.updateMatrixWorld(true);
  const v = new THREE.Vector3();
  viewer.figure.traverse(o => {
    if (!o.isMesh || !o.visible) return;
    const src = o.geometry, n = src.attributes.position.count;
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      o.getVertexPosition(i, v);
      v.applyMatrix4(o.matrixWorld);
      pos[i * 3] = v.x; pos[i * 3 + 1] = v.y; pos[i * 3 + 2] = v.z;
    }
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    if (src.attributes.uv) g.setAttribute('uv', src.attributes.uv.clone());
    if (src.index) g.setIndex(src.index.clone());
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, o.material);
    m.name = o.name.replace(/\s+/g, '_');
    group.add(m);
  });
  return group;
}

async function imageBytes(tex) {
  const img = tex.image;
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  c.getContext('2d').drawImage(img, 0, 0);
  const blob = await new Promise(r => c.toBlob(r, 'image/png'));
  return new Uint8Array(await blob.arrayBuffer());
}

async function objZip(viewer, name) {
  const group = posed(viewer);
  const files = {};
  const mats = new Map();
  let mtl = '';
  for (const m of group.children) {
    const mat = m.material;
    if (!mats.has(mat)) {
      const id = `mat${mats.size}`;
      mats.set(mat, id);
      const c = mat.color;
      mtl += `newmtl ${id}\nKd ${c.r.toFixed(4)} ${c.g.toFixed(4)} ${c.b.toFixed(4)}\nKs 0.2 0.2 0.2\nNs 60\n`;
      if (mat.map && mat.map.image) {
        files[`${id}.png`] = await imageBytes(mat.map);
        mtl += `map_Kd ${id}.png\n`;
        if (mat.transparent) mtl += `map_d ${id}.png\n`;
      }
      mtl += '\n';
    }
    m.material = new THREE.MeshPhongMaterial({ name: mats.get(mat) });
  }
  const obj = `mtllib ${name}.mtl\n` + new OBJExporter().parse(group);
  files[`${name}.obj`] = strToU8(obj);
  files[`${name}.mtl`] = strToU8(mtl);
  return zipSync(files);
}

export async function exportFigure(viewer, format, name, save) {
  let bytes;
  if (format === 'glb') bytes = await gltf(viewer, true);
  else if (format === 'gltf') bytes = await gltf(viewer, false);
  else if (format === 'obj') bytes = await objZip(viewer, name);
  else if (format === 'stl') bytes = new Uint8Array(new STLExporter().parse(posed(viewer), { binary: true }).buffer);
  else if (format === 'ply') bytes = strToU8(new PLYExporter().parse(posed(viewer), () => {}, {}));
  else if (format === 'png') {
    const url = viewer.snapshot(1600, 2000);
    bytes = Uint8Array.from(atob(url.split(',')[1]), c => c.charCodeAt(0));
  } else {
    const glb = await gltf(viewer, true);
    const r = await fetch('/api/convert', { method: 'POST', headers: { 'X-Format': format }, body: glb });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.error || r.statusText); }
    bytes = new Uint8Array(await r.arrayBuffer());
  }
  const ext = FORMATS.find(f => f.id === format).ext;
  return save(`${name}.${ext}`, bytes);
}
