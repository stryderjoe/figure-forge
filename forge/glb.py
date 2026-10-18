"""A small reader for the GLB files Figure Forge's viewer exports (three.js GLTFExporter): the scene's nodes,
the skin, mesh primitives with their attributes, and materials with their base colour and embedded image."""
import json
import struct

import numpy as np

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


class GlbError(ValueError):
    pass


def _matrix(node):
    """Local matrix of a node (column vectors)."""
    if 'matrix' in node:
        return np.array(node['matrix'], dtype=np.float64).reshape(4, 4).T
    t = node.get('translation', [0, 0, 0])
    x, y, z, w = node.get('rotation', [0, 0, 0, 1])
    s = node.get('scale', [1, 1, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * np.array(s)[None, :]
    m[:3, 3] = t
    return m


class Glb:
    def __init__(self, data):
        if data[:4] != b'glTF':
            raise GlbError('not a GLB file')
        pos, self.bin = 12, b''
        self.json = None
        while pos < len(data):
            length, kind = struct.unpack_from('<I4s', data, pos)
            chunk = data[pos + 8:pos + 8 + length]
            if kind == b'JSON':
                self.json = json.loads(chunk)
            elif kind == b'BIN\0':
                self.bin = chunk
            pos += 8 + length
        if self.json is None:
            raise GlbError('GLB without JSON')
        j = self.json
        self.nodes = j.get('nodes', [])
        self.parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get('children', []):
                self.parent[c] = i
        self.world = {}

    def accessor(self, index):
        a = self.json['accessors'][index]
        view = self.json['bufferViews'][a['bufferView']]
        dtype = COMPONENT[a['componentType']]
        width = WIDTH[a['type']]
        start = view.get('byteOffset', 0) + a.get('byteOffset', 0)
        item = np.dtype(dtype).itemsize * width
        stride = view.get('byteStride', item)
        raw = np.frombuffer(self.bin, dtype=np.uint8, count=view['byteLength'], offset=view.get('byteOffset', 0))
        rows = np.lib.stride_tricks.as_strided(raw[a.get('byteOffset', 0):], shape=(a['count'], item),
                                               strides=(stride, 1)).copy()
        out = rows.view(dtype).reshape(a['count'], width)
        if a.get('normalized') and dtype != np.float32:
            out = out.astype(np.float64) / np.iinfo(dtype).max
        del start
        return out

    def world_matrix(self, i):
        if i not in self.world:
            m = _matrix(self.nodes[i])
            if i in self.parent:
                m = self.world_matrix(self.parent[i]) @ m
            self.world[i] = m
        return self.world[i]

    def image_bytes(self, texture_index):
        tex = self.json['textures'][texture_index]
        img = self.json['images'][tex['source']]
        if 'bufferView' not in img:
            raise GlbError('only embedded images are read')
        view = self.json['bufferViews'][img['bufferView']]
        return self.bin[view.get('byteOffset', 0):view.get('byteOffset', 0) + view['byteLength']]

    def material(self, index):
        """(base colour [r,g,b,a] linear, image bytes or None, alpha mode)."""
        if index is None:
            return [1, 1, 1, 1], None, 'OPAQUE'
        m = self.json['materials'][index]
        pbr = m.get('pbrMetallicRoughness', {})
        colour = pbr.get('baseColorFactor', [1, 1, 1, 1])
        image = None
        if 'baseColorTexture' in pbr:
            image = self.image_bytes(pbr['baseColorTexture']['index'])
        return colour, image, m.get('alphaMode', 'OPAQUE')

    def meshes(self):
        """Every drawn primitive: dict(node, name, positions (world), normals (world), uvs, indices,
        joints (node indices), weights, material, bone (node index of a rigid mesh's bone, or None))."""
        skins = self.json.get('skins', [])
        out = []
        for i, n in enumerate(self.nodes):
            if 'mesh' not in n:
                continue
            world = self.world_matrix(i)
            skin = skins[n['skin']] if 'skin' in n else None
            for prim in self.json['meshes'][n['mesh']]['primitives']:
                attr = prim['attributes']
                if prim.get('mode', 4) != 4:
                    continue
                pos = self.accessor(attr['POSITION']).astype(np.float64)
                nrm = self.accessor(attr['NORMAL']).astype(np.float64) if 'NORMAL' in attr else None
                uv = self.accessor(attr['TEXCOORD_0']).astype(np.float64) if 'TEXCOORD_0' in attr else None
                idx = (self.accessor(prim['indices']).reshape(-1) if 'indices' in prim
                       else np.arange(len(pos))).astype(np.int64).reshape(-1, 3)
                extras = n.get('extras') or {}
                rec = {'node': i, 'name': n.get('name', ''), 'uv': uv, 'indices': idx, 'material': prim.get('material'),
                       'slot': extras.get('slot', ''), 'part': extras.get('part', ''),
                       'occluder': bool(extras.get('occluder'))}
                if 'COLOR_0' in attr:                  # linear vertex colours (faces of the newer games)
                    rec['colours'] = self.accessor(attr['COLOR_0']).astype(np.float64)
                if skin is not None and 'JOINTS_0' in attr:
                    # skinned: vertices are in the mesh node's space at bind; bring them to the scene's
                    rec['positions'] = (np.c_[pos, np.ones(len(pos))] @ world.T)[:, :3]
                    rec['normals'] = nrm @ world[:3, :3].T if nrm is not None else None
                    rec['joints'] = [skin['joints'][int(j)] for j in range(len(skin['joints']))]
                    rec['joint_index'] = self.accessor(attr['JOINTS_0']).astype(np.int64)
                    rec['weights'] = self.accessor(attr['WEIGHTS_0']).astype(np.float64)
                else:
                    rec['positions'] = (np.c_[pos, np.ones(len(pos))] @ world.T)[:, :3]
                    rec['normals'] = nrm @ world[:3, :3].T if nrm is not None else None
                    # the bone it rides on: the nearest ancestor that a skin uses as a joint
                    joints = {j for s in skins for j in s['joints']}
                    p = self.parent.get(i)
                    while p is not None and p not in joints:
                        p = self.parent.get(p)
                    rec['bone'] = p
                out.append(rec)
        return out
