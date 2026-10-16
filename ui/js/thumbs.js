// Small pictures of parts and characters for the pickers, drawn on demand with one shared off-screen renderer.
import * as THREE from 'three';

const SIZE = 160;

export class Thumbs {
  constructor() {
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.canvas.height = SIZE;
    this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setSize(SIZE, SIZE, false);
    this.scene = new THREE.Scene();
    this.scene.add(new THREE.HemisphereLight(0xe8eeff, 0x3a3328, 1.3));
    const key = new THREE.DirectionalLight(0xffffff, 2.0);
    key.position.set(-1, 2, 3);
    const rim = new THREE.DirectionalLight(0x9fc4ff, 1.0);
    rim.position.set(2, 1, -2);
    this.scene.add(key, rim);
    this.camera = new THREE.PerspectiveCamera(28, 1, 0.001, 100);
    this.cache = new Map();        // key -> data URL
    this.waiting = new Map();      // key -> [img]
    this.queue = [];
    this.busy = false;
    this.textures = new Map();
    this.observer = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        this.observer.unobserve(e.target);
        const img = e.target;
        this.request(img.dataset.key, JSON.parse(img.dataset.parts), img.dataset.owner, img);
      }
    }, { rootMargin: '200px' });
  }

  // an <img> that fills in when it scrolls into view
  image(key, partIds, owner) {
    const img = document.createElement('img');
    img.className = 'thumb';
    img.alt = '';
    img.dataset.key = key;
    img.dataset.parts = JSON.stringify(partIds);
    img.dataset.owner = owner;
    if (this.cache.has(key)) img.src = this.cache.get(key);
    else this.observer.observe(img);
    return img;
  }

  request(key, partIds, owner, img) {
    if (this.cache.has(key)) { img.src = this.cache.get(key); return; }
    if (this.waiting.has(key)) { this.waiting.get(key).push(img); return; }
    this.waiting.set(key, [img]);
    this.queue.push({ key, partIds, owner });
    this.pump();
  }

  async pump() {
    if (this.busy) return;
    this.busy = true;
    while (this.queue.length) {
      // the newest request first: what the user scrolled to last is what they are looking at
      const job = this.queue.pop();
      let url = '';
      try { url = await this.draw(job.partIds, job.owner); } catch (e) { console.warn('thumbnail', job.key, e); }
      this.cache.set(job.key, url);
      for (const img of this.waiting.get(job.key) || []) if (url) img.src = url;
      this.waiting.delete(job.key);
    }
    this.busy = false;
  }

  texture(url) {
    if (!this.textures.has(url)) {
      this.textures.set(url, new THREE.TextureLoader().loadAsync(url).then(t => {
        t.colorSpace = THREE.SRGBColorSpace; t.wrapS = t.wrapT = THREE.RepeatWrapping; return t;
      }).catch(() => null));
      if (this.textures.size > 300) this.textures.delete(this.textures.keys().next().value);
    }
    return this.textures.get(url);
  }

  async draw(partIds, owner) {
    const group = new THREE.Group();
    for (const id of partIds) {
      const r = await fetch(`/api/part?id=${encodeURIComponent(id)}&base=${encodeURIComponent(owner)}&space=model`);
      if (!r.ok) continue;
      const part = await r.json();
      for (const m of part.meshes) {
        const g = new THREE.BufferGeometry();
        g.setAttribute('position', new THREE.Float32BufferAttribute(m.position, 3));
        if (m.normal) g.setAttribute('normal', new THREE.Float32BufferAttribute(m.normal, 3));
        if (m.uv) g.setAttribute('uv', new THREE.Float32BufferAttribute(m.uv, 2));
        g.setIndex(m.index);
        if (!m.normal) g.computeVertexNormals();
        const info = m.material;
        const map = info.texture ? await this.texture(info.texture) : null;
        const mat = new THREE.MeshPhongMaterial({
          color: new THREE.Color().setRGB(info.colour[0], info.colour[1], info.colour[2], THREE.SRGBColorSpace),
          map, shininess: 70, specular: 0x333333, transparent: !!info.alpha, alphaTest: info.alpha ? 0.05 : 0 });
        group.add(new THREE.Mesh(g, mat));
      }
    }
    this.scene.add(group);
    const box = new THREE.Box3().setFromObject(group);
    let url = '';
    if (!box.isEmpty()) {
      const c = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3());
      const r = Math.max(size.x, size.y, size.z) * 0.62 + 1e-4;
      const dir = new THREE.Vector3(0.45, 0.25, 1).normalize();       // front, a little from the right and above
      this.camera.position.copy(c).addScaledVector(dir, r / Math.tan(THREE.MathUtils.degToRad(14)));
      this.camera.near = r / 50; this.camera.far = r * 50;
      this.camera.lookAt(c);
      this.camera.updateProjectionMatrix();
      this.renderer.setClearColor(0x000000, 0);
      this.renderer.render(this.scene, this.camera);
      url = this.canvas.toDataURL('image/png');
    }
    this.scene.remove(group);
    group.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
    return url;
  }
}
