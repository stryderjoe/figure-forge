"""Part geometry fitted to a body base, for the viewer (viewer space: glTF's, game z negated).

A rigid part is given in its bone's frame, turned so it keeps its own orientation in model space when the
base's bone is oriented differently (two skeleton schemes, see skeleton.py), and anchored at the bone.
A skinned part is re-bound: each vertex moves with the base's bones as it moved with the donor's,
    v' = sum_i w_i * v * inverse(donor world_i) * base world_i     (row vectors, game space)
bones matched by common name, else the nearest ancestor the base has.
"""
import numpy as np

from . import catalog, skeleton


def _world(char):
    return [np.asarray(b['world'], dtype=np.float64).reshape(4, 4) for b in char.bones]


def _rotation(m):
    r = m[:3, :3].copy()
    for k in range(3):
        n = np.linalg.norm(r[k])
        if n > 1e-9:
            r[k] /= n
    return r


def bone_map(donor, base):
    """(anchor, base bone) per donor bone: the anchor is the bone itself when the base has its common name,
    else its nearest ancestor that the base has (whose base bone it then follows)."""
    by_common = {}
    for i, c in enumerate(base.common):
        by_common.setdefault(c, i)
    anchors, targets = [], []
    for i, b in enumerate(donor.bones):
        j = by_common.get(donor.common[i])
        if j is not None:
            anchors.append(i)
            targets.append(j)
        else:
            p = b['parent']
            anchors.append(anchors[p] if 0 <= p < len(anchors) else i)
            targets.append(targets[p] if 0 <= p < len(targets) else 0)
    return anchors, targets


def skeleton_json(char):
    bones = []
    for i, b in enumerate(char.bones):
        bones.append({'name': b['name'], 'common': char.common[i], 'parent': b['parent'],
                      'matrix': skeleton.mirror(b['bind_local']).reshape(16).tolist()})
    return {'id': char.id, 'name': char.name, 'scheme': catalog.scheme(char), 'bones': bones}


def material_json(game_id, char, mesh_index):
    m = char.material(mesh_index)
    if m is None:
        return {'colour': [0.8, 0.8, 0.8, 1.0], 'texture': None, 'alpha': False}
    tex = m['texture']
    has = 0 <= tex < len(char.nu.textures) and char.nu.textures[tex].get('size')
    # see-through where the texture has alpha (DXT3 / DXT5 / 32-bit); the material word at +0x40 is not a
    # plain on/off flag in every game (Star Wars stores floats there), so it is not used
    fmt = char.nu.textures[tex].get('format', '') if has else ''
    return {'colour': [round(float(c), 4) for c in m['colour'][:4]],
            'texture': f'/api/texture?char={char.id}&index={tex}' if has else None,
            'alpha': fmt.startswith(('DXT3', 'DXT5', 'A8R8G8B8')),
            'key': f'{char.id}#{m["index"]}'}


def _pack(mesh):
    return {k: (v.round(5).ravel().tolist() if isinstance(v, np.ndarray) and v.dtype.kind == 'f'
                else v.ravel().tolist() if isinstance(v, np.ndarray) else v) for k, v in mesh.items()}


def part_json(game, part_id, base, model_space=False):
    """Meshes of one part fitted to the base character. model_space=True gives a rigid piece in the model's
    space at the bind pose instead of its bone's (for pictures of the part on its own)."""
    char_id, layer_i, key = part_id.split('|', 2)
    donor = game.character(char_id)
    layer = donor.layers[int(layer_i)]
    info = next(p for p in catalog.parts(donor) if p['id'] == part_id)
    rec = layer['parts'][info['part']]
    wd, wb = _world(donor), _world(base)
    anchors, bmap = bone_map(donor, base)
    placed = np.asarray(rec['matrix'], dtype=np.float64).reshape(4, 4)
    meshes = []
    for mi in rec['meshes']:
        geo = donor.geometry(mi)
        if geo is None or not geo[4]:
            continue
        pos, nrm, uv, _col, tris, weights = geo
        p = np.c_[np.asarray(pos, dtype=np.float64), np.ones(len(pos))] @ placed
        n = np.asarray(nrm, dtype=np.float64) @ placed[:3, :3] if len(nrm) == len(pos) else None
        t = np.asarray(tris, dtype=np.int64)
        out = {}
        if info['kind'] == 'rigid':
            d = rec['bone']
            a, b = anchors[d], bmap[d]
            # into the anchor's frame (the same bone, or the ancestor the base has), then turned so the piece
            # keeps the donor's orientation in model space on the base's bone
            to_anchor = wd[d] @ np.linalg.inv(wd[a])
            turn = _rotation(wd[a]) @ _rotation(wb[b]).T
            p3 = (p @ to_anchor)[:, :3] @ turn
            if n is not None:
                n = n @ to_anchor[:3, :3] @ turn
            if model_space:
                p3 = (np.c_[p3, np.ones(len(p3))] @ wb[b])[:, :3]
                if n is not None:
                    n = n @ wb[b][:3, :3]
            out['bone'] = base.common[b]
            out['boneName'] = base.bones[b]['name']
        else:
            if info['region']:
                keep = np.array([r == info['region'] for r in catalog.triangle_regions(donor, geo)], dtype=bool)
                t = t[keep]
                if not len(t):
                    continue
            used = np.unique(t)
            remap = np.full(len(pos), -1, dtype=np.int64)
            remap[used] = np.arange(len(used))
            t = remap[t]
            p = p[used]
            n = n[used] if n is not None else None
            uv = [uv[i] for i in used] if len(uv) == len(pos) else []
            w = [weights[i] for i in used] if weights else [[] for _ in used]
            moved = np.zeros((len(used), 3))
            moved_n = np.zeros((len(used), 3)) if n is not None else None
            idx = np.zeros((len(used), 4), dtype=np.int64)
            wts = np.zeros((len(used), 4))
            cache = {}
            for vi, slots in enumerate(w):
                if not slots:
                    slots = [(0, 1.0)]
                total = sum(x for _, x in slots) or 1.0
                for k, (d, x) in enumerate(slots[:4]):
                    b = bmap[d]
                    m = cache.get(d)
                    if m is None:
                        m = cache[d] = np.linalg.inv(wd[anchors[d]]) @ wb[b]
                    moved[vi] += (x / total) * (p[vi] @ m)[:3]
                    if moved_n is not None:
                        moved_n[vi] += (x / total) * (n[vi] @ m[:3, :3])
                    idx[vi, k] = b
                    wts[vi, k] = x / total
            p3 = moved
            n = moved_n
            out['skinIndex'] = idx
            out['skinWeight'] = wts
        if p3.ndim == 2 and p3.shape[1] == 4:
            p3 = p3[:, :3]
        out['position'] = skeleton.to_viewer_points(p3)
        if n is not None:
            nn = skeleton.to_viewer_points(n)
            lens = np.linalg.norm(nn, axis=1, keepdims=True)
            out['normal'] = nn / np.where(lens > 1e-9, lens, 1.0)
        if len(uv) == len(out['position']):
            # v is measured from the image's top. Star Wars stores it one tile lower (-1 to 0, measured on its
            # models): textures repeat, as in the games, so the same formula serves all three
            uvs = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
            uvs[:, 1] = 1.0 - uvs[:, 1]
            out['uv'] = uvs
        out['index'] = t[:, [0, 2, 1]].astype(np.int64)      # the mirror reverses the winding
        out['material'] = material_json(game.id, donor, mi)
        out['mesh'] = mi
        meshes.append(_pack(out))
    return {'id': part_id, 'slot': info['slot'], 'label': info['label'], 'kind': info['kind'], 'meshes': meshes}
