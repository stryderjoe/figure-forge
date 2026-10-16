"""Characters of the three classic TT LEGO games: LEGO Star Wars: The Complete Saga, LEGO Indiana Jones:
The Original Adventures and LEGO Batman (2008), PC. Read-only.

Model (`<name>_pc.ghg`, NU20; header words relative to the scene header `g`, see nu20.py):
    g+0x164 bone count, g+0x168 bone records (0x60: name at +0x4c, parent at +0x50), then one bind matrix
    per bone (local to its parent, row vectors) and one inverse bind per bone.
    g+0x18c layer count, g+0x190 layer table. Layer record, 20 bytes of self-relative pointers:
        +0 name, +4 per-bone array of rigid objects, +8 skinned object, +12 a second per-bone array,
        +16 a second skinned object (faces).
    Object (12 bytes): +8 a placed record (0xd0 bytes: matrix at +0, model at +0xb0).
    Rigid pieces sit in their bone's space; skinned pieces in model space, three weight bytes (sum 255)
    and three indices into the mesh's palette of up to eight bones.
Measured: every character model of the three games reads this way (LEGO Batman 586, Indiana Jones 210,
Star Wars 344 files, 2026-10-15).

The character's .txt picks the layers shown (layers_high=0,3,4,...) and declares its animations
(anim_start="name" ... anim_end, anim_include="file.txt").
"""
import os
import re
import struct

from .an3 import An3, euler_matrix
from .nu20 import NU20, _cstr

FLIP = (2, 6, 8, 9, 14)      # elements that change sign under Z (.) Z for a row-major 4x4


class FormatError(ValueError):
    pass


def mat_mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def z_mirror(m):
    m = list(m)
    for k in FLIP:
        m[k] = -m[k]
    return m


def channel_matrix(values):
    """Local matrix (row-major, row vectors) of one animation node's channels (t, r[, s]), in the
    animation files' own space: the model's space mirrored along Z."""
    tx, ty, tz, rx, ry, rz = values[:6]
    sx, sy, sz = values[6:9] if len(values) >= 9 else (1.0, 1.0, 1.0)
    r = euler_matrix(rx, ry, rz, 'xyz')
    return [r[0][0] * sx, r[0][1] * sx, r[0][2] * sx, 0.0,
            r[1][0] * sy, r[1][1] * sy, r[1][2] * sy, 0.0,
            r[2][0] * sz, r[2][1] * sz, r[2][2] * sz, 0.0,
            tx, ty, tz, 1.0]


def model_stem(path):
    base = os.path.basename(path)
    return base[:-7] if base.lower().endswith('_pc.ghg') else os.path.splitext(base)[0]


def read_nu20(path):
    try:
        return NU20(path)
    except (ValueError, struct.error, IndexError, OSError) as e:
        raise FormatError(f'{os.path.basename(path)}: {e}') from None


class Character:
    """Skeleton, layers and geometry of one character model."""

    def __init__(self, path):
        self.nu = nu = read_nu20(path)
        self.path = path
        self.name = model_stem(path)
        self.bones = nu.bones
        for b in self.bones:
            b['world'] = (b['bind_local'] if b['parent'] < 0
                          else mat_mul(b['bind_local'], self.bones[b['parent']]['world']))
        self.layers = self._layers()

    def _record(self, obj):
        nu = self.nu
        rec = nu.ptr(obj + 8)
        if not rec:
            return None
        model_at = nu.ptr(rec + 0xb0)
        model = next((m for m in nu.models if m['offset'] == model_at), None)
        if model is None:
            return None
        return {'matrix': list(struct.unpack_from('<16f', nu.d, rec)), 'model': model['index'],
                'meshes': [i for i in model['meshes'] if i >= 0]}

    def _layers(self):
        nu = self.nu
        g = nu.gsnh
        count = nu.i32(g + 0x18c)
        table = nu.ptr(g + 0x190)
        if not (0 < count < 256 and table):
            return []
        layers = []
        for i in range(count):
            o = table + i * 20
            name_at = nu.ptr(o)
            layer = {'index': i, 'name': _cstr(nu.d, name_at) if name_at else f'layer{i}', 'parts': []}
            for k in (4, 12):
                arr = nu.ptr(o + k)
                if not arr:
                    continue
                for b in range(len(self.bones)):
                    obj = nu.ptr(arr + b * 4)
                    rec = self._record(obj) if obj else None
                    if rec:
                        rec.update(kind='rigid', bone=b)
                        layer['parts'].append(rec)
            for k in (8, 16):
                obj = nu.ptr(o + k)
                rec = self._record(obj) if obj else None
                if rec:
                    rec.update(kind='skinned', bone=-1)
                    layer['parts'].append(rec)
            layers.append(layer)
        return layers

    def geometry(self, mesh_index):
        return geometry(self.nu, mesh_index)

    def material(self, mesh_index):
        mi = self.nu.meshes[mesh_index]['material']
        return self.nu.materials[mi] if 0 <= mi < len(self.nu.materials) else None

    def has_texture(self, index):
        return 0 <= index < len(self.nu.textures) and bool(self.nu.textures[index].get('size'))

    def texture_format(self, index):
        return self.nu.textures[index].get('format', '') if self.has_texture(index) else ''

    def texture_bytes(self, index):
        t = self.nu.textures[index]
        return self.nu.d[t['data_offset']:t['data_offset'] + t['size']]


def geometry(nu, mesh_index):
    """(positions, normals, uvs, colours, triangles, weights) of one mesh, or None.
    weights: per vertex a list of (bone index, weight) for a mesh with a bone palette, else None."""
    mesh = nu.meshes[mesh_index]
    got = nu.mesh_geometry(mesh)
    if got is None:
        return None
    pos, nrm, uv, col, tris = got
    fmt, _ = nu.mesh_layout(mesh)
    weights = None
    if 'blend_indices' in fmt and 'blend_weights' in fmt and fmt['blend_weights'][0] == 'byte4':
        vb, _ = nu.vertex_buffers[mesh['vertex_buffer']]
        stride = mesh['stride']
        v0 = vb + mesh['vertex_offset'] * stride
        wo, io = fmt['blend_weights'][1], fmt['blend_indices'][1]
        palette = mesh['bones']
        weights = []
        for i in range(mesh['vertex_count']):
            o = v0 + i * stride
            slots = {}
            for wt, s in zip(nu.d[o + wo:o + wo + 3], nu.d[o + io:o + io + 3]):
                if wt and s < 8 and palette[s] >= 0:
                    slots[palette[s]] = slots.get(palette[s], 0) + wt / 255.0
            weights.append(sorted(slots.items()))
    return pos, nrm, uv, col, tris, weights


# -- text files ----------------------------------------------------------------------------------

def listing(folder):
    try:
        return {n.lower(): n for n in os.listdir(folder)}
    except OSError:
        return {}


def character_txt(model_path):
    folder = os.path.dirname(model_path)
    hit = listing(folder).get(model_stem(model_path).lower() + '.txt')
    return os.path.join(folder, hit) if hit else None


def read_text(path):
    with open(path, encoding='latin-1') as f:
        return f.read()


def chosen_layers(txt_path, detail='high'):
    """Layer numbers the game shows (layers_<detail>=...), or None when the file says nothing."""
    if not txt_path:
        return None
    text = read_text(txt_path)
    for key in (f'layers_{detail}', 'layers_high', 'layers_special'):
        m = re.search(rf'^\s*{key}\s*=\s*([\d,\s]+)$', text, re.M | re.I)
        if m:
            return [int(x) for x in re.findall(r'\d+', m.group(1))]
    return None


def read_anim_blocks(txt_path, chars_root, blocks=None, seen=None):
    """anim_start .. anim_end blocks of a character .txt in file order, following anim_include."""
    blocks = [] if blocks is None else blocks
    seen = set() if seen is None else seen
    if not txt_path:
        return blocks
    key = os.path.normcase(os.path.abspath(txt_path))
    if key in seen or not os.path.exists(txt_path):
        return blocks
    seen.add(key)
    current = None
    for line in read_text(txt_path).splitlines():
        line = re.split(r'//|;', line)[0].strip()
        m = re.match(r'anim_include\s*=\s*"?([^"]+)"?', line, re.I)
        if m:
            inc = listing(chars_root).get(m.group(1).strip().lower())
            if inc:
                read_anim_blocks(os.path.join(chars_root, inc), chars_root, blocks, seen)
            continue
        m = re.match(r'anim_start\s*=\s*"?([^"]+)"?', line, re.I)
        if m:
            current = {'name': m.group(1).strip(), 'action': None, 'fpsec': None}
            blocks.append(current)
            continue
        if re.match(r'anim_end', line, re.I):
            current = None
        elif current is not None:
            m = re.match(r'action\s*=\s*"?([^"]+)"?', line, re.I)
            if m and current['action'] is None:
                current['action'] = m.group(1).strip()
            m = re.match(r'fpsec\s*=\s*([\d.]+)', line, re.I)
            if m:
                current['fpsec'] = float(m.group(1))
    return blocks


def find_anim(folders, name):
    """<name>_pc.an3 (LEGO Batman, Indiana Jones) or <name>.an3 (Star Wars), any case."""
    for folder in folders:
        files = listing(folder)
        hit = files.get(name.lower() + '_pc.an3') or files.get(name.lower() + '.an3')
        if hit:
            return os.path.join(folder, hit)
    return None


def declared_clips(model_path):
    """[(clip name, path, fpsec or None, game action)] the character's .txt declares, found beside the
    model or in chars/commonanims; first declaration wins."""
    folder = os.path.dirname(os.path.abspath(model_path))
    chars_root = os.path.dirname(folder)
    common = os.path.join(chars_root, listing(chars_root).get('commonanims', 'commonanims'))
    out, seen = [], set()
    for blk in read_anim_blocks(character_txt(model_path), chars_root):
        key = blk['name'].lower()
        if key in seen:
            continue
        seen.add(key)
        path = find_anim((folder, common), blk['name'])
        if path:
            out.append((blk['name'], path, blk['fpsec'], blk['action']))
    return out


def load_clip(path):
    try:
        return An3(path)
    except (ValueError, struct.error, IndexError, OSError) as e:
        raise FormatError(f'{os.path.basename(path)}: {e}') from None
