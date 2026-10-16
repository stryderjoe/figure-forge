"""Write a figure into a newer-era game (LEGO Batman 2, LEGO Marvel Super Heroes): a model of its own and the
body's definition pointed at it.

Every minifigure of these games shares one model (super_minifig_nxg.ghg); a character is a definition (.cd) that
picks that model's layers and names the textures each material wears. So a figure becomes:
- a copy of the shared model with the figure's pieces added: new part records (vertex and index buffers in the
  format of the model's own skinned body parts), a texture (one atlas of the figure's images, added to the
  model's texture list and its images), the Body material wearing it, and draw commands the Body layer's clip
  uses instead of its own pieces;
- a copy of the body's definition with that model as 'Override Model File', the Body layer as the only one shown,
  and no texture, material, attachment or layer choices of its own (so nothing is drawn over the figure).
The model reads front to back (no offsets to later sections, measured), so sections may grow; the word before
'02UN' (8 bytes before the tag) is the length of the model's main block and follows.
LEGO Marvel keeps a model's images in a companion '<model>.nxg_textures' (a resource header, then a 'TSXT' block
listing the textures as the model's own list does, then their DDS images back to back) and its shaders in
'<model>.pc_shaders'; the model starts with a resource header naming those companions after the model. The new
model's name has the shared model's length, so those names are rewritten in place.
LEGO Batman 3 (MESH 175, DISP 32, texture list 12; super_minifig_dx11.ghg) is the same with these differences
(measured on its shared model): vertex and index data little-endian, colours stored BGRA, the texture
coordinate twice (u, v, u, v) and a second float2 set after it (attribute 7), normals and tangents ending 0xFF;
each part record starts with one more word (1) and ends with a 40-byte tail; the index buffer keeps the
template's flags; a texture entry is {16-byte id, u32 0x00010000, u8 length, the path, u8 flag}, in the model
and in its '.nxg_textures' alike; a clip is u16 count, then (command, material) pairs.
"""
import os
import struct

import numpy as np

from .formats import modern, nxgdisp, nxgmesh, ttstream
from .gamewrite import WriteError, dds_dxt5_mipped, image_rgba, pack_atlas
from .glb import Glb

MAX_BONES = 26                                  # a part's palette holds 27 bones (measured: always 27 bytes)
BODY_LAYER = 2                                  # 'TT2_Body' in every minifigure skeleton of the era


def _srgb(lin):
    lin = np.clip(lin, 0.0, 1.0)
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


class Splices:
    """Byte changes against the original file, applied from the end so earlier positions stay valid."""

    def __init__(self, data):
        self.d, self.items = data, []

    def put(self, at, raw, length=None):
        self.items.append((at, at + (len(raw) if length is None else length), bytes(raw)))

    def growth_before(self, end):
        return sum(len(raw) - (e - s) for s, e, raw in self.items if s < end)

    def apply(self):
        out = bytearray(self.d)
        for s, e, raw in sorted(self.items, key=lambda x: (-x[0], -x[1])):
            out[s:e] = raw
        return bytes(out)


class NxgWriter:
    def __init__(self, char):
        """char: modern.Character of the body (LEGO Batman 2 or LEGO Marvel Super Heroes)."""
        self.char = char
        self.path = char.model_path
        with open(self.path, 'rb') as f:
            self.d = f.read()
        self.mesh = nxgmesh.MeshSection(self.d)
        self.lb3 = self.mesh.version == 175
        if not (self.mesh.version in (47, 48, 161, 169) and self.mesh.tail == 72 or self.mesh.pirates or self.lb3):
            raise WriteError('figures can be put into LEGO Batman 2, LEGO Marvel Super Heroes, LEGO Pirates and '
                             'LEGO Batman 3 only')
        name = os.path.basename(self.path)
        self.platform = '_dx11' if name.lower().endswith('_dx11.ghg') else '_nxg'
        self.stem = name[:-len(self.platform + '.ghg')]
        comp = self.path[:-len('.ghg')] + '.nxg_textures'
        self.companion = open(comp, 'rb').read() if os.path.exists(comp) else None
        self.disp = nxgdisp.read(self.d, len(self.mesh.parts))
        self.model = modern.model(self.path)
        self.bone_index = {b['name'].lower(): i for i, b in enumerate(char.bones)}

    # -- the figure -------------------------------------------------------------------------------------------

    def pieces(self, glb):
        """GLB primitives -> game-space pieces: pos, nrm, uv, colours (sRGB 0..1 or None), tris, weights."""
        mirror = np.array([1, 1, -1.0])
        names = {i: n.get('name', '') for i, n in enumerate(glb.nodes)}

        def game_bone(node):
            while node is not None:
                b = self.bone_index.get(names.get(node, '').lower())
                if b is not None:
                    return b
                node = glb.parent.get(node)
            return 0

        out = []
        for rec in glb.meshes():
            pos = rec['positions'] * mirror
            nrm = rec['normals'] * mirror if rec['normals'] is not None else np.tile([0, 1.0, 0], (len(pos), 1))
            uv = rec['uv'] if rec['uv'] is not None else np.zeros((len(pos), 2))
            col = None
            if rec.get('colours') is not None:
                # the material's colour tints vertex colours too (hair colours)
                tint = np.asarray(glb.material(rec['material'])[0][:3], dtype=np.float64)
                col = _srgb(rec['colours'][:, :3] * tint)
            tris = rec['indices'][:, [0, 2, 1]]                               # the mirror reverses the winding
            if 'joints' in rec:
                bones = [game_bone(n) for n in rec['joints']]
                idx, w = rec['joint_index'], rec['weights']
                weights = []
                for vi in range(len(pos)):
                    acc = {}
                    for k in range(4):
                        if w[vi, k] > 1e-4:
                            b = bones[idx[vi, k]]
                            acc[b] = acc.get(b, 0) + w[vi, k]
                    top = sorted(acc.items(), key=lambda kv: -kv[1])[:4] or [(0, 1.0)]
                    s = sum(x for _, x in top)
                    weights.append([(b, x / s) for b, x in top])
            else:
                # rigid pieces are already in the model's space at bind: one bone, full weight
                weights = [[(game_bone(rec['bone']), 1.0)]] * len(pos)
            out.append({'pos': pos, 'nrm': nrm, 'uv': uv, 'col': col, 'tris': tris, 'weights': weights,
                        'material': rec['material'], 'name': rec['name']})
        return out

    @staticmethod
    def chunks(piece):
        """Split a piece into runs of triangles using at most MAX_BONES bones and 65535 vertices each."""
        out, cur, bones = [], [], set()
        for t in piece['tris']:
            tb = {b for v in t for b, _w in piece['weights'][v]}
            if cur and (len(bones | tb) > MAX_BONES or 3 * (len(cur) + 1) > 65535):
                out.append((cur, bones))
                cur, bones = [], set()
            cur.append(t)
            bones |= tb
        if cur:
            out.append((cur, bones))
        return out

    # -- the model --------------------------------------------------------------------------------------------

    def _template_buffer(self, body_clip):
        """The vertex format of the Body layer's own first part (position, normal, colour, uv, skin, and in LEGO
        Marvel a tangent), so the Body material's shader gets what it expects."""
        cmd = self.disp['commands'][self.disp['clips'][body_clip][0][1]]
        part = self.mesh.parts[cmd[2]]
        b = self.mesh.buffers[part['streams'][0][0][0]]
        if not {0, 1, 5, 9, 10} <= {a for a, _t, _o in b['desc']}:
            raise WriteError('the Body part has no skinned, textured vertex format to copy')
        return b

    def _buffer_head(self, buf, count):
        """A new vertex buffer's header in the template's form: token, flags, count, (Marvel: 'DXTV' and its
        version), descriptors, (Marvel: 6 more bytes, copied)."""
        d, at = self.d, buf['at']
        head = struct.pack('>III', 1, buf['flags'], count)
        p = at + 12
        if not self.mesh.old:
            head += d[p:p + 8]
            p += 8
        n = struct.unpack_from('>I', d, p)[0]
        head += d[p:p + 4 + 3 * n]
        p += 4 + 3 * n
        if not self.mesh.old:
            head += d[p:p + 6]
        return head

    def _encode(self, desc, stride, pos, nrm, uv, col, skin_idx, skin_w):
        n = len(pos)
        out = np.zeros((n, stride), np.uint8)
        e = '<' if self.lb3 else '>'
        w = 255 if self.lb3 else 254
        for a, t, o in desc:
            if a == 0:
                v = np.c_[pos, np.ones(n)]
            elif a == 1:
                v = np.c_[np.clip(np.round(nrm * 127.5 + 127.5), 0, 255), np.full(n, w)]
            elif a == 3:
                # a tangent across the normal (any will do for flat prints: no normal map is drawn on them)
                tan = np.cross(nrm, [0.0, 1.0, 0.0])
                small = np.linalg.norm(tan, axis=1) < 1e-3
                tan[small] = [1.0, 0.0, 0.0]
                tan /= np.linalg.norm(tan, axis=1, keepdims=True)
                v = np.c_[np.clip(np.round(tan * 127.5 + 127.5), 0, 255), np.full(n, w)]
            elif a == 2:
                v = col[:, [2, 1, 0, 3]] if self.lb3 else col
            elif a == 5:
                v = np.c_[uv, uv] if self.lb3 else uv
            elif a == 7 and self.lb3:
                v = uv
            elif a == 9:
                v = skin_idx
            elif a == 10:
                v = skin_w
            else:
                continue
            if v.shape[1] < 4:
                v = np.c_[v, np.zeros((n, 4 - v.shape[1]))]
            if t == 6:
                raw = np.asarray(v[:, :4], e + 'f2').view(np.uint8).reshape(n, 8)
            elif t == 5:
                raw = np.asarray(v[:, :2], e + 'f2').view(np.uint8).reshape(n, 4)
            elif t in (2, 3, 4):
                raw = np.asarray(v[:, :t], e + 'f4').view(np.uint8).reshape(n, 4 * t)
            else:
                raw = np.asarray(v[:, :4], np.uint8)
            out[:, o:o + raw.shape[1]] = raw
        return out.tobytes()

    def _part_record(self, buf, tail, verts, tris, palette):
        """One part record (MESH 48 / 169 / 175): a new vertex buffer, a new index buffer, the counts,
        the bone palette (27 bytes), no morph targets, the tail of a body part."""
        rec = struct.pack('>II', 1, 1) if self.lb3 else struct.pack('>I', 1)
        rec += self._buffer_head(buf, len(verts) // buf['stride']) + verts
        rec += struct.pack('>II', 0, 0)
        idx = np.asarray(tris, '<u2' if self.lb3 else '>u2').reshape(-1)
        rec += struct.pack('>IIII', 1, self._index_flags if self.lb3 else 0, len(idx), 2) + idx.tobytes()
        rec += struct.pack('>IIIHI', 0, len(idx), 0, 0, len(verts) // buf['stride'])
        rec += bytes(4)
        rec += struct.pack('>I', 27) + bytes(palette) + b'\xff' * (27 - len(palette))
        rec += struct.pack('>I', 0)                                             # no morph targets
        return rec + tail

    def write(self, glb_bytes, texture_name='figureforge_atlas', report=print, new_stem=None):
        """{'ghg': the new model, 'nxg_textures': its images (LEGO Marvel) or None}. new_stem: the new model's
        name, as long as the shared model's (LEGO Marvel: the resource header names the companions by it)."""
        glb = Glb(glb_bytes)
        pieces = self.pieces(glb)
        if not pieces:
            raise WriteError('the figure has no meshes')
        d, sp = self.d, Splices(self.d)

        # 1. one atlas for the whole figure; uvs into its rectangles
        mats = sorted({p['material'] for p in pieces}, key=lambda x: -1 if x is None else x)
        images = [image_rgba(glb.material(mi)[1], glb.material(mi)[0]) for mi in mats]
        atlas, rects = pack_atlas(images)
        side = atlas.shape[0]
        for p in pieces:
            x, y, w, h = rects[mats.index(p['material'])]
            u = np.clip(p['uv'][:, 0], 0.0, 1.0)
            v = np.clip(p['uv'][:, 1], 0.0, 1.0)
            p['uv'] = np.c_[(x + 0.5 + u * (w - 1)) / side, (y + 0.5 + v * (h - 1)) / side]

        # 2. part records after the last one
        body_clip = self._body_clip()
        buf = self._template_buffer(body_clip)
        clip_items = self.disp['clips'][body_clip]
        tpart = self.mesh.parts[self.disp['commands'][clip_items[0][1]][2]]
        self._index_flags = self.mesh.buffers[tpart['index'][0]]['flags']
        # the printed front's material (LEGO Pirates lists the back first)
        mnames = [n.upper() for n in self.model.material_names]
        fronts = [it for it in clip_items if it[0] < len(mnames) and mnames[it[0]].startswith('BODY_FRONT')]
        if fronts:
            clip_items = fronts + [it for it in clip_items if it not in fronts]
        material = clip_items[0][0]
        # the tail of the Body's own part (72 bytes; LEGO Pirates 40)
        tail_part = self.mesh.parts[self.disp['commands'][clip_items[0][1]][2]]
        tail_at = self._record_end(tail_part)
        tail = d[tail_at - self.mesh.tail:tail_at]
        # pieces coloured by their vertices (faces, cowls: no image) wear the model's vertex-colour material,
        # the rest the Body material with the atlas
        names = [n.upper() for n in self.model.material_names]
        vc_material = next((i for i, n in enumerate(names) if n.startswith('VERTEXCOLOURS')), material)
        records, new_parts, part_mats = b'', [], []
        # LEGO Pirates numbers its full parts first and the depth copies after them: new parts come after the
        # full ones, and every draw of a depth copy moves up by their number (below)
        full = sum(1 for p in self.mesh.parts if not p.get('short'))
        first = full if self.mesh.pirates else len(self.mesh.parts)
        for p in pieces:
            coloured = p['col'] is not None and glb.material(p['material'])[1] is None
            for tris, bones in self.chunks(p):
                palette = sorted(bones)
                where = {b: i for i, b in enumerate(palette)}
                used = sorted({v for t in tris for v in t})
                remap = {v: i for i, v in enumerate(used)}
                pos = p['pos'][used]
                nrm = p['nrm'][used]
                uv = p['uv'][used]
                if p['col'] is not None:
                    # LEGO Batman 2 stores vertex colours at half strength (127 = full), LEGO Marvel at full
                    scale = 127.5 if self.mesh.old else 255.0
                    col = np.c_[np.round(p['col'][used] * scale), np.full(len(used), 127 if self.mesh.old else 255)]
                else:
                    col = np.full((len(used), 4), 127 if self.mesh.old else 255)
                si = np.zeros((len(used), 4))
                sw = np.zeros((len(used), 4))
                for k, vi in enumerate(used):
                    ws = p['weights'][vi]
                    q = [int(round(x * 255)) for _b, x in ws]
                    q[0] += 255 - sum(q)
                    for j, (b, _x) in enumerate(ws):
                        si[k, j], sw[k, j] = where[b], q[j]
                verts = self._encode(buf['desc'], buf['stride'], pos, nrm, uv, col, si, sw)
                records += self._part_record(buf, tail, verts, [[remap[v] for v in t] for t in tris], palette)
                new_parts.append(first + len(new_parts))
                part_mats.append(vc_material if coloured else material)
        sp.put(self.mesh.end, records, 0)
        count_at = self._part_count_at()
        sp.put(count_at, struct.pack('>I', len(self.mesh.parts) + len(new_parts)))

        # 3. the texture: an entry in the texture list, its image after the others (in the model, or in its
        # companion)
        entry = os.urandom(16)
        tex_index = self._add_texture(sp, atlas, texture_name, entry)
        companion = self._companion(atlas, texture_name, entry, new_stem) if self.companion else None

        # 4. the Body material wears it
        table = self.model._table if getattr(self.model, '_table', None) is not None else None
        if table is None:
            self.model.material_names
            table = self.model._table
        gap = modern.texture_slot_gap(d, table, tex_index)
        if gap is None:
            raise WriteError('the material table could not be read')
        sp.put(table[material][1] - gap, struct.pack('>i', tex_index))

        # 5. draw commands for the new parts, before the list's last command; the Body clip draws them
        cmds = self.disp['commands']
        nc_at = self.disp['command_count_at']
        last_at = nc_at + 4 + 6 * (len(cmds) - 1)
        pairs = len([c for c in cmds if c[0] == 0x83])
        new_cmds, items = b'', []
        for j, part in enumerate(new_parts):
            new_cmds += struct.pack('>BBI', 0x83, 0, pairs + j) + struct.pack('>BBI', 0xB3, 0, part)
            items.append(len(cmds) - 1 + 2 * j + 1)
        sp.put(last_at, new_cmds, 0)
        if self.mesh.pirates:
            for i, (op, fl, value) in enumerate(cmds):
                if op == 0xB3 and value >= full:
                    sp.put(nc_at + 4 + 6 * i + 2, struct.pack('>I', value + len(new_parts)))
        sp.put(nc_at, struct.pack('>I', len(cmds) + 2 * len(new_parts)))
        s0, s1 = self.disp['clip_spans'][body_clip]
        if self.disp['version'] >= 32:
            pairs32 = [x for it, m in zip(items, part_mats) for x in (it, m)]
            clip = struct.pack('>H', len(items)) + struct.pack(f'>{len(pairs32)}I', *pairs32)
        else:
            head = d[s0:s0 + 2]
            clip = head + struct.pack('>I', len(items)) + struct.pack(f'>{len(items)}I', *part_mats)
            clip += struct.pack('>I', len(items)) + struct.pack(f'>{len(items)}I', *items)
        sp.put(s0, clip, s1 - s0)
        # every piece of the Body layer draws that clip
        for sp_index in self._body_specials():
            rec = self.disp['specials'][sp_index]
            sp.put(rec.get('clip_at', rec['at'] + 180), struct.pack('>I', body_clip))

        # 6. the main block's length word; the new name in the resource header and the display list's path
        q = d.find(b'02UN')
        nu = struct.unpack_from('>I', d, q - 8)[0]
        sp.put(q - 8, struct.pack('>I', nu + sp.growth_before(q - 4 + nu)))
        if new_stem:
            self._rename(sp, d, new_stem, [(0, q - 8), self._disp_path()])
        report(f'{len(new_parts)} parts, a {side} x {side} texture')
        return {'ghg': sp.apply(), 'nxg_textures': companion}

    def _disp_path(self):
        at = self.d.find(b'PSID')
        n = struct.unpack_from('>I', self.d, at + 8)[0]
        return (at + 12, at + 12 + n)

    def _rename(self, sp, d, new_stem, regions):
        """The shared model's name -> new_stem (same length), in any case, inside the given byte ranges."""
        import re
        old = self.stem
        if len(new_stem) != len(old):
            raise WriteError(f'the new model name must have {len(old)} letters')
        for s0, s1 in regions:
            # file names only ('<model>_nxg.*', the source '<model>.hgo'): the folder keeps its name, as the new
            # model lives beside the shared one (a folder the game's chardata.txt validates)
            pattern = re.escape(old.encode('latin1')) + rb'(?=_nxg|_dx11|\.hgo)'
            for m in re.finditer(pattern, d[s0:s1], re.IGNORECASE):
                found = m.group(0).decode('latin1')
                new = new_stem.upper() if found.isupper() else new_stem.lower() if found.islower() else new_stem.title()
                sp.put(s0 + m.start(), new.encode('latin1'))

    def _companion(self, atlas, name, entry, new_stem):
        """LEGO Marvel's '.nxg_textures' with the atlas added: one more entry in 'TSXT' (its block length
        follows) and its DDS after the others."""
        t = self.companion
        sp = Splices(t)
        x = t.find(b'.CC4TSXT')
        a = t.find(b'ROTV', t.find(b'CONVDATE'))
        if x < 0 or a < 0:
            raise WriteError('the texture file is not as expected')
        n = struct.unpack_from('>I', t, a + 4)[0]
        p = a + 8
        path = f'figureforge/{name}.nut'.encode('latin1') + b'\0'
        if self.lb3:
            for _ in range(n):
                p += 22 + t[p + 20]
            rec = self._entry12(entry, path)
        else:
            for _ in range(n):
                ln = struct.unpack_from('>I', t, p + 16)[0]
                p += 20 + ln + 4
            rec = entry + struct.pack('>I', len(path)) + path + struct.pack('>I', 0)
        sp.put(a + 4, struct.pack('>I', n + 1))
        sp.put(p, rec, 0)
        size = struct.unpack_from('>I', t, x - 4)[0]
        sp.put(x - 4, struct.pack('>I', size + len(rec)))
        dds, _levels = dds_dxt5_mipped(atlas)
        sp.put(len(t), dds, 0)
        if new_stem:
            self._rename(sp, t, new_stem, [(0, struct.unpack_from('>I', t, 0)[0] + 4)])
        return sp.apply()

    def _record_end(self, part):
        starts = sorted(p['at'] for p in self.mesh.parts) + [self.mesh.end]
        return starts[starts.index(part['at']) + 1]

    def _part_count_at(self):
        d = self.d
        at = d.find(b'HSEM')
        while struct.unpack_from('>I', d, at + 4)[0] != self.mesh.version:
            at = d.find(b'HSEM', at + 4)
        if d[at + 8:at + 12] != b'ROTV':
            return at + 8                       # LEGO Pirates: the count follows the version
        return at + 12

    def _body_specials(self):
        layer = self.char.skel['layers'][BODY_LAYER]
        return sorted({item['special'] for item in layer['items']})

    def _body_clip(self):
        """The clip of the Body layer's plain piece ('Body_Mesh'; not the skeleton or mech variants), whose
        first material is the body's printed front."""
        found = [self.disp['specials'][si] for si in self._body_specials()]
        found = [s for s in found if s['clip'] != 0xFFFFFFFF]
        plain = [s for s in found if s['name'].lower() == 'body_mesh']
        other = [s for s in found if not any(w in s['name'].lower() for w in ('skeleton', 'mech', 'breakup'))]
        for s in plain + other + found:
            return s['clip']
        raise WriteError('the model has no Body layer to draw the figure with')

    def _add_texture(self, sp, atlas, name, entry):
        """Adds the atlas to the texture list ('HGXT': a count and a word per texture (LEGO Marvel: none), then a
        count and per texture a 16-byte hash, the source path, a flag) and, when the model holds its images
        (LEGO Batman 2), its DDS image after the model's own."""
        d = self.d
        at = d.find(b'HGXT')
        if at >= 0 and struct.unpack_from('>I', d, at + 4)[0] == 7:
            return self._add_texture7(sp, atlas, name, entry)
        if at >= 0 and struct.unpack_from('>I', d, at + 4)[0] == 12:
            return self._add_texture12(sp, name, entry)
        if at < 0 or d[at + 8:at + 12] != b'ROTV':
            raise WriteError('the model has no texture list')
        m = struct.unpack_from('>I', d, at + 12)[0]
        words_end = at + 16 + 4 * m
        n = struct.unpack_from('>I', d, words_end + 4)[0] if d[words_end:words_end + 4] == b'ROTV' else -1
        if m not in (0, n):
            raise WriteError('the texture list is not as expected')
        p = words_end + 8
        for _ in range(n):
            ln = struct.unpack_from('>I', d, p + 16)[0]
            p += 20 + ln + 4
        if m:
            sp.put(at + 12, struct.pack('>I', n + 1))
            sp.put(words_end, struct.pack('>I', 0), 0)
        sp.put(words_end + 4, struct.pack('>I', n + 1))
        path = f'figureforge/{name}.nut'.encode('latin1') + b'\0'
        sp.put(p, entry + struct.pack('>I', len(path)) + path + struct.pack('>I', 0), 0)
        if self.companion is None:
            dds, _levels = dds_dxt5_mipped(atlas)
            sp.put(len(d), dds, 0)
        return n

    @staticmethod
    def _entry12(entry, path):
        return entry + struct.pack('>I', 0x10000) + bytes([len(path)]) + path + b'\0'

    def _add_texture12(self, sp, name, entry):
        """LEGO Batman 3 (texture list version 12): 'ROTV' n, then per texture {16-byte id, u32 0x00010000, u8
        length, the path, u8 flag (1 on normal maps)}; the images are in the companion."""
        d = self.d
        at = d.find(b'HGXT')
        if d[at + 8:at + 12] != b'ROTV' or self.companion is None:
            raise WriteError('the texture list is not as expected')
        n = struct.unpack_from('>I', d, at + 12)[0]
        p = at + 16
        for _ in range(n):
            p += 22 + d[p + 20]
        path = f'figureforge/{name}.nut'.encode('latin1') + b'\0'
        sp.put(at + 12, struct.pack('>I', n + 1))
        sp.put(p, self._entry12(entry, path), 0)
        return n

    def _add_texture7(self, sp, atlas, name, entry):
        """LEGO Pirates (texture list version 7, no markers): u32 n, n zero words, u32 n, then per texture a
        16-byte hash, u32 width, u32 height, 0, 0x10000, 1, u32 mip levels, 6, 1, 0, a zero byte, u32 length,
        the path, 8 zero bytes and u16 its number + 1; its DXT1 image (the format every Pirates texture has)
        after the model's own."""
        from .formats import dxt
        from PIL import Image
        d = self.d
        at = d.find(b'HGXT')
        n = struct.unpack_from('>I', d, at + 8)[0]
        words_end = at + 12 + 4 * n
        if struct.unpack_from('>I', d, words_end)[0] != n:
            raise WriteError('the texture list is not as expected')
        p = words_end + 4
        for _ in range(n):
            ln = struct.unpack_from('>I', d, p + 53)[0]
            p += 57 + ln + 10
        side = atlas.shape[0]
        levels, data, im, s0 = 0, b'', Image.fromarray(atlas), side
        while True:
            s1 = max(4, side >> levels)
            data += dxt.encode_dxt1(np.asarray(im.resize((s1, s1), Image.LANCZOS) if s1 != side else im, np.uint8))
            levels += 1
            if s1 <= 4:
                break
        head = struct.pack('<4s31I', b'DDS ', 0x7C, 0x81007 | 0x20000, side, side, (side // 4) * (side // 4) * 8, 0,
                           levels, *([0] * 11), 0x20, 4, int.from_bytes(b'DXT1', 'little'), 0, 0, 0, 0, 0,
                           0x401008, 0, 0, 0, 0)
        path = f'figureforge/{name}.nut'.encode('latin1') + b'\0'
        rec = (entry + struct.pack('>9I', side, side, 0, 0x10000, 1, levels, 6, 1, 0) + b'\0' +
               struct.pack('>I', len(path)) + path + bytes(8) + struct.pack('>H', n + 1))
        sp.put(at + 8, struct.pack('>I', n + 1))
        sp.put(words_end, struct.pack('>I', 0), 0)
        sp.put(words_end, struct.pack('>I', n + 1), 4)
        sp.put(p, rec, 0)
        sp.put(len(d), head + data, 0)
        return n


def definition(char, model_ref):
    """The body's definition, pointed at the figure's model: (bytes)."""
    with open(char.path, 'rb') as f:
        s = ttstream.Stream(f.read())
    root = s.roots[0][0]
    objs = [x for r in s.roots for x in ttstream.walk(r)]
    drop = [o for o in objs if not o.get('inline') and o['class'] in
            ('Character Texture', 'Character Material', 'Character Attachment', 'Character Layer Special',
             'Character Material Mapping', 'Character Layer')]
    key = 'Override Model File' if 'Override Model File' in root['fields'] else 'Model File'
    values = {}
    for field in ('Default Layers', 'Cutscene Layers'):
        if isinstance(root['fields'].get(field), int):
            values[root['at'][field]] = struct.pack('<i', 1 << BODY_LAYER)
    for field in ('Hat Layers', 'Hair Layers', 'Cape Layers', 'Christmas Hat Layers'):
        if isinstance(root['fields'].get(field), int):
            values[root['at'][field]] = struct.pack('<i', 0)
    return ttstream.edit(s, removes=drop, strings={root['at'][key]: model_ref}, values=values)
