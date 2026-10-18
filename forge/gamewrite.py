"""Write a figure into a game's own character model (.ghg), for LEGO Batman (2008), LEGO Indiana Jones 1 and
LEGO Star Wars: The Complete Saga.

The figure arrives as the GLB Figure Forge exports, built on the TARGET character's skeleton (its bones carry
the game's own names). The target's model file is the template: its skeleton, header and everything the game
expects stay; the figure replaces what is drawn. Measured on the games' files and checked in the running
games (2026-10-15):
- a material's texture is the i32 at +0xB8 (the i16 at +0x74 holds the same number but is not what is drawn);
- a model's meshes are a run of the mesh table from `first_mesh`; each is drawn by a pair of draw-list
  commands (0x83: an identity matrix, 0x82: the mesh record) found by the model's ids; the materials come
  from the model's list (indices into the material table);
- a layer holds per-bone rigid objects and up to two skinned objects; an object (12 bytes) points to a
  placed record (0xd0: matrix, bounds, model at +0xb0).
So the writer: bakes each kind of surface (skinned or rigid, opaque or see-through) into one texture atlas;
gives each a material copied from a template material of the same kind, in that material's own slot;
rewrites existing mesh records in place with new vertex and index buffers in the template's vertex format;
points existing models at them; and leaves the figure as the only thing in the layers the game shows.
"""
import io
import os
import re
import struct
import tempfile

import numpy as np

from .formats import classic, dxt
from .formats.nu20edit import ModelFile
from .glb import Glb


class WriteError(ValueError):
    pass


# -- small helpers --------------------------------------------------------------------------------------------

def stripify(triangles):
    """One triangle strip for a list (game winding), joined with degenerate triangles."""
    tris = [tuple(t) for t in triangles if len(set(t)) == 3]
    if not tris:
        return []
    edge = {}
    for i, (a, b, c) in enumerate(tris):
        for e in ((a, b), (b, c), (c, a)):
            edge.setdefault(e, []).append(i)
    used = [False] * len(tris)

    def take(x, y):
        for i in edge.get((x, y), ()):
            if not used[i]:
                used[i] = True
                a, b, c = tris[i]
                return {(a, b): c, (b, c): a, (c, a): b}[(x, y)]
        return None

    strip = []
    for i, t in enumerate(tris):
        if used[i]:
            continue
        used[i] = True
        run = list(t)
        while True:
            k = len(run) - 2
            x = take(run[-2], run[-1]) if k % 2 == 0 else take(run[-1], run[-2])
            if x is None:
                break
            run.append(x)
        if strip:
            strip += [strip[-1], run[0]]
            if len(strip) % 2:
                strip.append(run[0])
        strip += run
    return strip


def dxt1_like_size(side):
    return max(4, side)


def pack_atlas(images, max_side=1024):
    """Shelf-pack RGBA images (numpy HxWx4 uint8) into a square power-of-two atlas.
    Returns (atlas, rects [(x, y, w, h) in pixels]); images are scaled down together when needed."""
    if not images:
        return np.zeros((8, 8, 4), np.uint8), []
    scale = 1.0
    while True:
        sizes = [(max(4, int(im.shape[1] * scale)), max(4, int(im.shape[0] * scale))) for im in images]
        area = sum(w * h for w, h in sizes)
        side = 64
        while side * side < area * 1.15 and side < max_side:
            side *= 2
        order = sorted(range(len(images)), key=lambda i: -sizes[i][1])
        x = y = row = 0
        rects = [None] * len(images)
        ok = True
        for i in order:
            w, h = sizes[i]
            if w > side:
                ok = False
                break
            if x + w > side:
                x, y, row = 0, y + row, 0
            if y + h > side:
                ok = False
                break
            rects[i] = (x, y, w, h)
            x += w
            row = max(row, h)
        if ok:
            break
        scale *= 0.85
    atlas = np.zeros((side, side, 4), np.uint8)
    from PIL import Image
    for i, (x, y, w, h) in enumerate(rects):
        im = Image.fromarray(images[i]).resize((w, h), Image.LANCZOS) if (w, h) != images[i].shape[1::-1] else Image.fromarray(images[i])
        atlas[y:y + h, x:x + w] = np.asarray(im.convert('RGBA'))
    return atlas, rects


def dds_dxt5_mipped(rgba):
    """A DXT5 DDS with every mip level down to 4x4 (as the games' own textures), and its level count."""
    from PIL import Image
    side = rgba.shape[0]
    levels, data, im = 0, b'', Image.fromarray(rgba)
    while True:
        s = max(4, side >> levels)
        level = np.asarray(im.resize((s, s), Image.LANCZOS) if s != side else im, dtype=np.uint8)
        data += dxt.encode_dxt5(level)
        levels += 1
        if s <= 4:
            break
    head = struct.pack('<4s31I', b'DDS ', 0x7C, 0x81007 | 0x20000, side, side, len(dxt.encode_dxt5(rgba)), 0, levels,
                       *([0] * 11), 0x20, 4, int.from_bytes(b'DXT5', 'little'), 0, 0, 0, 0, 0, 0x401008, 0, 0, 0, 0)
    return head + data, levels


def image_rgba(data, colour):
    """An image (PNG/JPEG bytes, or None for a plain colour) times a colour, as uint8 RGBA."""
    from PIL import Image
    c = np.array([*(colour[:3]), colour[3] if len(colour) > 3 else 1.0], dtype=np.float64)
    # glTF colours are linear: back to sRGB for the texture
    rgb = np.where(c[:3] <= 0.0031308, c[:3] * 12.92, 1.055 * np.power(np.clip(c[:3], 0, 1), 1 / 2.4) - 0.055)
    mult = np.r_[rgb, c[3]]
    if data is None:
        return np.clip(np.ones((8, 8, 4)) * mult * 255 + 0.5, 0, 255).astype(np.uint8)
    im = np.asarray(Image.open(io.BytesIO(data)).convert('RGBA'), dtype=np.float64)
    return np.clip(im * mult + 0.5, 0, 255).astype(np.uint8)


# -- the writer -----------------------------------------------------------------------------------------------

class GameWriter:
    def __init__(self, template_bytes, game_id, txt_path=None):
        self.game = game_id
        self.txt = txt_path
        self.raw = template_bytes
        fd, self.tmp = tempfile.mkstemp(suffix='_pc.ghg')
        os.write(fd, template_bytes)
        os.close(fd)
        self.char = classic.Character(self.tmp)
        self.nu = self.char.nu
        self.m = ModelFile(template_bytes)
        self.notes = []
        bones = self.char.bones
        self.bone_index = {b['name'].lower(): i for i, b in enumerate(bones)}
        self.world = [np.asarray(b['world'], dtype=np.float64).reshape(4, 4) for b in bones]

    def _txt_value(self, *keys):
        """The number after one of these keys in the character's .txt (key=3 or key 3), or None."""
        if not self.txt or not os.path.exists(self.txt):
            return None
        text = classic.read_text(self.txt)
        for key in keys:
            m = re.search(rf'^\s*{key}\s*[= ]\s*(\d+)', text, re.M | re.I)
            if m:
                return int(m.group(1))
        return None

    def layer_plan(self):
        """Which layer each kind of part goes to. The games swap some layers by name in the character's .txt
        (hat_layer_index, hair_layer_index, cape_layer_index, backpack_layer): a figure's hat, hair, cape and
        back items go there, so the game's own hat, cape and backpack behaviour work on it; the rest goes to
        the first layer the game shows."""
        shown = classic.chosen_layers(self.txt) or [0]
        count = len(self.char.layers)
        plan = {'main': min(shown)}
        for slot, keys in (('headgear', ('hat_layer_index',)), ('hair', ('hair_layer_index', 'hair_layer')),
                           ('cape', ('cape_layer_index', 'cape_layer')), ('back', ('backpack_layer',))):
            v = self._txt_value(*keys)
            if v is not None and 0 <= v < count:
                plan[slot] = v
        return plan

    def _kept_layers(self, filled, pieces):
        """The special layers (hat, hair, cape, back) the figure does not fill, whose own piece the game goes on
        drawing. A figure with hair or headgear of its own does not keep the character's hat or hair: it would
        be drawn over the figure's head."""
        plan = self.layer_plan()
        own_head = any(p['slot'] in ('hair', 'headgear') for p in pieces)
        return {v for k, v in plan.items() if k != 'main' and not (own_head and k in ('headgear', 'hair'))} - filled

    def layer_for(self, slot):
        plan = self._plan if hasattr(self, '_plan') else self.layer_plan()
        self._plan = plan
        return plan.get(slot, plan['main'])

    def close(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    # which template materials, mesh records and models to reuse
    def _template_materials(self):
        """{(skinned, alpha): template material index} from the materials the template's own meshes use."""
        nu = self.nu
        out = {}
        for mesh in nu.meshes:
            mi = mesh['material']
            if mi < 0 or nu.mesh_problem(mesh):
                continue
            fmt = {a for a, _k, _o in nu.materials[mi]['vertex_format']}
            if 'position' not in fmt or 'uv1' not in fmt:
                continue
            skinned = 'blend_indices' in fmt
            tex = nu.materials[mi]['texture']
            fmt_name = nu.textures[tex].get('format', '') if 0 <= tex < len(nu.textures) else ''
            alpha = fmt_name.startswith(('DXT3', 'DXT5'))
            out.setdefault((skinned, alpha), mi)
        for skinned in (True, False):
            for alpha in (True, False):
                if (skinned, alpha) not in out and (skinned, not alpha) in out:
                    out[(skinned, alpha)] = out[(skinned, not alpha)]
        return out

    def _convert(self, glb):
        """GLB primitives -> game-space pieces: dict(kind, bone, pos, nrm, uv, tris, weights, material)."""
        mirror = np.array([1, 1, -1.0])
        names = {i: n.get('name', '') for i, n in enumerate(glb.nodes)}

        def game_bone(node):
            while node is not None:
                b = self.bone_index.get(names.get(node, '').lower())
                if b is not None:
                    return b
                node = glb.parent.get(node)
            return 0

        pieces = []
        for rec in glb.meshes():
            if rec.get('occluder'):
                continue                # a mouth mask, for the portrait only
            pos = rec['positions'] * mirror
            nrm = rec['normals'] * mirror if rec['normals'] is not None else None
            uv = rec['uv'] if rec['uv'] is not None else np.zeros((len(pos), 2))
            # the GLB's images are stored upright and its v is measured from the image's top, as the games'
            # are; Star Wars keeps its v one tile lower (-1 to 0)
            v = uv[:, 1] - (1.0 if self.game == 'tcs' else 0.0)
            uv = np.c_[uv[:, 0], v]
            tris = rec['indices'][:, [0, 2, 1]]                               # the mirror reverses the winding
            piece = {'pos': pos, 'nrm': nrm, 'uv': uv, 'tris': tris, 'material': rec['material'], 'name': rec['name'],
                     'slot': rec.get('slot', '')}
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
                    top = sorted(acc.items(), key=lambda kv: -kv[1])[:3] or [(0, 1.0)]
                    s = sum(x for _, x in top)
                    weights.append([(b, x / s) for b, x in top])
                piece.update(kind='skinned', weights=weights)
            else:
                b = game_bone(rec['bone'])
                inv = np.linalg.inv(self.world[b])
                piece['pos'] = (np.c_[pos, np.ones(len(pos))] @ inv)[:, :3]
                if nrm is not None:
                    piece['nrm'] = nrm @ inv[:3, :3]
                piece.update(kind='rigid', bone=b)
            pieces.append(piece)
        return pieces

    def _encode_vertices(self, template_mat, piece_pos, piece_nrm, piece_uv, slots=None):
        """Vertex bytes in the template material's format; unknown fields keep a template vertex's bytes."""
        nu = self.nu
        mat = nu.materials[template_mat]
        stride = mat['vertex_stride']
        fmt = {a: (k, o) for a, k, o in mat['vertex_format']}
        # a template vertex of this material, for the fields not set here
        sample = None
        for mesh in nu.meshes:
            if mesh['material'] == template_mat and not nu.mesh_problem(mesh):
                vb, _ = nu.vertex_buffers[mesh['vertex_buffer']]
                o = vb + mesh['vertex_offset'] * mesh['stride']
                sample = nu.d[o:o + stride]
                break
        n = len(piece_pos)
        out = np.frombuffer((sample or bytes(stride)) * n, dtype=np.uint8).reshape(n, stride).copy()

        def put(name, arr):
            kind, off = fmt[name]
            b = np.ascontiguousarray(arr).view(np.uint8).reshape(n, -1)
            out[:, off:off + b.shape[1]] = b

        put('position', piece_pos.astype('<f4'))
        if 'normal' in fmt and piece_nrm is not None:
            nn = piece_nrm / np.maximum(np.linalg.norm(piece_nrm, axis=1, keepdims=True), 1e-9)
            if fmt['normal'][0] == 'float3':
                put('normal', nn.astype('<f4'))
            else:
                kind, off = fmt['normal']
                out[:, off:off + 3] = np.clip((nn + 1) * 0.5 * 255 + 0.5, 0, 255).astype(np.uint8)
        if 'uv1' in fmt:
            put('uv1', piece_uv.astype('<f4') if fmt['uv1'][0] == 'float2' else piece_uv.astype('<f2'))
        if slots is not None and 'blend_weights' in fmt and 'blend_indices' in fmt:
            wo, io_ = fmt['blend_weights'][1], fmt['blend_indices'][1]
            for vi, ws in enumerate(slots):
                vals = [int(round(x * 255)) for _s, x in ws]
                vals[0] += 255 - sum(vals)
                for k in range(3):
                    out[vi, wo + k] = vals[k] if k < len(vals) else 0
                    out[vi, io_ + k] = ws[k][0] if k < len(ws) else 0
        return out.tobytes(), stride

    def write(self, glb_bytes, report=print):
        m, nu = self.m, self.nu
        glb = Glb(glb_bytes)
        pieces = self._convert(glb)
        if not pieces:
            raise WriteError('the figure has no meshes')
        self._pieces = pieces
        # a character whose hair layer is drawn only once its hat is off (Indiana Jones: hair 7, hat 8, layers
        # shown 0,2,6,8,9): a figure with hair and no hat wears its hair in the hat's layer, or it would not show
        # (in the main layer the running game crashed loading the level, 2026-10-18)
        plan = self.layer_plan()
        shown = classic.chosen_layers(self.txt) or [0]
        if (plan.get('hair') is not None and plan['hair'] not in shown and plan.get('headgear') in shown
                and not any(p['slot'] == 'headgear' for p in pieces)):
            plan['hair'] = plan['headgear']
        self._plan = plan
        templates = self._template_materials()
        if (True, False) not in templates or (False, False) not in templates:
            raise WriteError('the target model lacks a skinned or a rigid material to copy')

        # 1. textures: one atlas per kind of surface
        buckets = {}
        for p in pieces:
            colour, image, mode = glb.material(p['material'])
            alpha = mode != 'OPAQUE' or (len(colour) > 3 and colour[3] < 0.99)
            key = (p['kind'] == 'skinned', alpha)
            buckets.setdefault(key, []).append(p)
            p['image_key'] = (p['material'],)
        atlas_of = {}
        for key, plist in buckets.items():
            mats = sorted({p['material'] for p in plist}, key=lambda x: -1 if x is None else x)
            images = [image_rgba(glb.material(mi)[1], glb.material(mi)[0]) for mi in mats]
            atlas, rects = pack_atlas(images)
            side = atlas.shape[0]
            for p in plist:
                x, y, w, h = rects[mats.index(p['material'])]
                u = np.clip(p['uv'][:, 0], 0.0, 1.0)
                vv = p['uv'][:, 1] + (1.0 if self.game == 'tcs' else 0.0)       # back to 0..1 from the top
                vv = np.clip(vv, 0.0, 1.0)
                # half a pixel in from the rectangle's edges, so neighbours do not bleed in
                au = (x + 0.5 + u * (w - 1)) / side
                av = (y + 0.5 + vv * (h - 1)) / side
                p['uv'] = np.c_[au, av - (1.0 if self.game == 'tcs' else 0.0)]
            atlas_of[key] = atlas

        # 2. new texture records and a longer texture table
        recs = m.texture_records()
        g = m.gsnh_head
        new_tex_index = {}
        all_recs = list(recs)
        for key, atlas in atlas_of.items():
            side = atlas.shape[0]
            dds, levels = dds_dxt5_mipped(atlas)
            r = m.clone(recs[0], 128)
            if m.old_layout:
                # Star Wars: the record keeps width, height and a 16-byte hash at +0x28; the format, levels and
                # size are in the payload's own header (width, height, levels, 12 DXT1 / 15 DXT5, flags, size)
                m.set_u32(r + 0x28, side)
                m.set_u32(r + 0x2C, side)
                m.write(r + 0x30, os.urandom(16))
                m.textures.append([struct.pack('<6I', side, side, levels, 15, 4, len(dds)), dds])
            else:
                # Batman, Indy: width, height, a 16-byte hash, the format at +0x38 (1 DXT1, 6 DXT5), the
                # level count at +0x40, the size at +0x44
                m.set_u32(r + 0, side)
                m.set_u32(r + 4, side)
                m.write(r + 8, os.urandom(16))
                m.set_u32(r + 0x38, 6)
                m.set_u32(r + 0x40, levels)
                m.set_u32(r + 0x44, len(dds))
                m.textures.append([b'', dds])
            new_tex_index[key] = len(all_recs)
            all_recs.append(r)
        table = m.alloc(4 * len(all_recs))
        for i, r in enumerate(all_recs):
            m.set_ptr(table + 4 * i, r)
        m.set_ptr(g + 4, table)
        m.set_u32(g, len(all_recs))

        # 3. materials: a copy of the template material of each kind, put in a material slot of its own; the
        # slots of the pieces a kept layer goes on drawing are left alone (found in the running Indiana Jones,
        # 2026-10-18: Indy's kept hat took the colour of the figure material written into its slot)
        mt = m.ptr(g + 8)
        mat_count = m.u32(g + 0xC)
        kept = self._kept_layers({self.layer_for(p['slot']) for p in pieces}, pieces)
        kept_model_ids = {part['model'] for L in kept for part in self.char.layers[L]['parts']}
        used_slots = {mat for md in nu.models if md['index'] in kept_model_ids for mat in md['materials']}
        mat_slot = {}
        for key in atlas_of:
            tmpl = templates.get(key, templates[(key[0], False)])
            slot = tmpl if tmpl not in used_slots else next(i for i in range(mat_count) if i not in used_slots)
            used_slots.add(slot)
            old = m.ptr(mt + 4 * tmpl)
            new = m.clone(old, 708)
            m.write(new + 0x74, struct.pack('<h', new_tex_index[key]))
            m.write(new + 0xB8, struct.pack('<i', new_tex_index[key]))
            prev = m.ptr(mt + 4 * slot)
            m.set_ptr(mt + 4 * slot, new)
            for loc in list(m.ptr_set):                      # the slot's setup command in the draw list
                if loc < m.pntr_at and loc != mt + 4 * slot and m.ptr(loc) == prev:
                    m.set_ptr(loc, new)
            mat_slot[key] = (slot, tmpl)

        # 4. meshes: split into drawable pieces (rigid: one bone; skinned: at most 8 bones), new buffers
        groups = {}                                          # object key -> [(mesh record bytes info)]
        drawn = []
        for key, plist in buckets.items():
            slot, tmpl = mat_slot[key]
            # one geometry per object (skinned, or a rigid bone) and kind of surface: fewer meshes to draw
            merged = {}
            for p in plist:
                layer = self.layer_for(p['slot'])
                obj = ('skinned', layer) if p['kind'] == 'skinned' else ('rigid', layer, p['bone'])
                merged.setdefault(obj, []).append(p)
            for plist2 in merged.values():
                p = self._merge(plist2)
                for chunk in self._chunks(p):
                    vb_bytes, stride = self._encode_vertices(tmpl, chunk['pos'], chunk['nrm'], chunk['uv'], chunk.get('slots'))
                    strip = stripify(chunk['tris'])
                    if len(strip) < 3:
                        continue
                    m.vertex_buffers.append(vb_bytes)
                    m.index_buffers.append(struct.pack(f'<{len(strip)}H', *strip))
                    layer = self.layer_for(p['slot'])
                    obj_key = ('skinned', layer) if p['kind'] == 'skinned' else ('rigid', layer, p['bone'])
                    entry = {'vb': len(m.vertex_buffers) - 1, 'ib': len(m.index_buffers) - 1, 'stride': stride,
                             'verts': len(chunk['pos']), 'strip': len(strip), 'palette': chunk.get('palette'),
                             'material': slot}
                    groups.setdefault(obj_key, []).append(entry)
                    drawn.append(entry)

        # 5. reuse mesh records in table order, a run per object; point existing models at them
        mesh_table_order = list(range(len(nu.meshes)))
        draw_cmd = {}                                        # mesh index -> its 0x82 command index
        for k, (t, target) in enumerate(nu.commands):
            if t == 0x82:
                for mesh in nu.meshes:
                    if mesh['offset'] == target:
                        draw_cmd[mesh['index']] = k
        # a special layer the figure does not fill keeps the character's own piece: its models and meshes stay
        kept = self._kept_layers({key[1] for key in groups}, pieces)
        kept_models = {part['model'] for L in kept for part in self.char.layers[L]['parts']}
        kept_meshes = {mi for L in kept for part in self.char.layers[L]['parts'] for mi in part['meshes']}
        usable = [i for i in mesh_table_order if i in draw_cmd and i not in kept_meshes]
        if len(drawn) > len(usable):
            raise WriteError(f'the figure needs {len(drawn)} meshes and {self.char.name} has {len(usable)} '
                             f'to reuse: simplify the figure or choose a bigger character')
        models_in_layers = []
        for layer in self.char.layers:
            for part in layer['parts']:
                if part['model'] not in models_in_layers:
                    models_in_layers.append(part['model'])
        free_models = [i for i in models_in_layers + [md['index'] for md in nu.models if md['index'] not in models_in_layers]
                       if i not in kept_models]
        if len(groups) > len(free_models):
            raise WriteError('more separate pieces than the target has models')
        cursor = 0
        object_model = {}
        for gi, (obj_key, entries) in enumerate(groups.items()):
            model = nu.models[free_models[gi]]
            ids, mats = [], []
            first = usable[cursor]
            for e in entries:
                mi = usable[cursor]
                cursor += 1
                rec = nu.meshes[mi]['offset']
                self._rewrite_mesh(rec, e)
                ids.append(draw_cmd[mi])
                mats.append(e['material'])
            q = model['offset']
            m.set_u32(q, len(entries))
            mats_at = m.alloc(4 * len(entries))
            ids_at = m.alloc(4 * len(entries))
            for k in range(len(entries)):                    # the lists are stored last mesh first
                m.set_u32(mats_at + 4 * (len(entries) - 1 - k), mats[k])
                m.set_u32(ids_at + 4 * (len(entries) - 1 - k), ids[k])
            self._set_rel(q + 4, mats_at)
            self._set_rel(q + 8, ids_at)
            starts = nu.disp + 0x1C + nu.i32(nu.disp + 0x1C)
            m.write(starts + 2 * free_models[gi], struct.pack('<H', first))
            object_model[obj_key] = q

        # 6. layers: the figure alone in the layers the game shows
        self._place(object_model)
        out = m.tobytes()
        report(f'{len(drawn)} meshes, {len(atlas_of)} atlases, {len(groups)} objects')
        return out

    def _set_rel(self, at, target):
        """A model's list links are relative to the field's own position too (and listed in PNTR)."""
        self.m.set_ptr(at, target)

    def _rewrite_mesh(self, rec, e):
        m = self.m
        m.set_u32(rec + 0, 6)                                # triangle strip
        m.set_u32(rec + 4, e['strip'] - 2)
        m.write(rec + 8, struct.pack('<H', e['stride']))
        pal = (e['palette'] or []) + [-1] * 8
        m.write(rec + 10, struct.pack('<8b', *pal[:8]))
        m.set_u32(rec + 20, 0)                               # first vertex
        m.set_u32(rec + 24, e['verts'])
        m.set_u32(rec + 28, 0)                               # first index
        m.set_u32(rec + 32, e['ib'])
        m.set_u32(rec + 36, e['vb'])
        m.set_u32(rec + 40, 0)                               # no dynamic buffers

    @staticmethod
    def _merge(pieces):
        """Pieces of the same object and surface kind as one geometry."""
        if len(pieces) == 1:
            return pieces[0]
        out = dict(pieces[0])
        offs = np.cumsum([0] + [len(q['pos']) for q in pieces[:-1]])
        out['pos'] = np.concatenate([q['pos'] for q in pieces])
        out['nrm'] = (np.concatenate([q['nrm'] for q in pieces]) if all(q['nrm'] is not None for q in pieces) else None)
        out['uv'] = np.concatenate([q['uv'] for q in pieces])
        out['tris'] = np.concatenate([q['tris'] + o for q, o in zip(pieces, offs)])
        if pieces[0]['kind'] == 'skinned':
            out['weights'] = [w for q in pieces for w in q['weights']]
        return out

    def _chunks(self, p):
        """Drawable pieces of one primitive: under 65,536 vertices, and for a skinned one at most 8 bones."""
        tris = p['tris']
        if p['kind'] == 'rigid':
            groups = [list(range(len(tris)))]
        else:
            groups, cur, bones = [], [], set()
            for ti, t in enumerate(tris):
                need = {b for v in t for b, _x in p['weights'][v]}
                if len(bones | need) > 8:
                    groups.append(cur)
                    cur, bones = [], set()
                cur.append(ti)
                bones |= need
            if cur:
                groups.append(cur)
        out = []
        for g in groups:
            t = tris[g]
            used = np.unique(t)
            remap = {int(v): i for i, v in enumerate(used)}
            chunk = {'pos': p['pos'][used], 'nrm': p['nrm'][used] if p['nrm'] is not None else None,
                     'uv': p['uv'][used], 'tris': [tuple(remap[int(v)] for v in tri) for tri in t]}
            if p['kind'] == 'skinned':
                palette = sorted({b for v in used for b, _x in p['weights'][int(v)]})[:8]
                slot = {b: i for i, b in enumerate(palette)}
                chunk['palette'] = palette
                chunk['slots'] = [[(slot.get(b, 0), x) for b, x in p['weights'][int(v)]] for v in used]
            out.append(chunk)
        return out

    def _place(self, object_model):
        """Objects for the figure's models in their layers. A layer the figure fills shows only the figure; a
        special layer it does not fill keeps the character's own piece (the game may need it there); every
        other layer is emptied."""
        m, nu = self.m, self.nu
        g = nu.gsnh
        count = nu.i32(g + 0x18c)
        table = nu.ptr(g + 0x190)
        plan = self.layer_plan()
        sample_obj = sample_placed = None
        for L in range(count):
            for k in (8, 16):
                o = nu.ptr(table + 20 * L + k)
                if o and nu.ptr(o + 8):
                    sample_obj, sample_placed = o, nu.ptr(o + 8)
                    break
            if sample_obj:
                break
        if sample_obj is None:
            raise WriteError('no object in the target model to copy')
        filled = {key[1] for key in object_model}
        keep = self._kept_layers(filled, self._pieces)
        for L in range(count):
            if L in keep:
                continue
            for k in (4, 8, 12, 16):
                m.set_ptr(table + 20 * L + k, 0)
        identity = struct.pack('<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
        big = struct.pack('<12f', -1.0, -1.0, -1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.5)

        def make_object(model):
            obj = m.clone(sample_obj, 12)
            placed = m.clone(sample_placed, 0xd0)
            m.write(placed, identity)
            m.write(placed + 0x40, identity)
            m.write(placed + 0x80, big)                     # bounds: a generous box, never culled
            m.set_ptr(placed + 0xb0, model)
            m.set_ptr(obj + 8, placed)
            return obj

        n_bones = len(self.char.bones)
        for layer in sorted(filled):
            per_bone = None
            skinned_slots = [8, 16]
            for key, model in object_model.items():
                if key[1] != layer:
                    continue
                if key[0] == 'skinned':
                    if not skinned_slots:
                        raise WriteError('more than two skinned pieces in one layer')
                    m.set_ptr(table + 20 * layer + skinned_slots.pop(0), make_object(model))
                else:
                    if per_bone is None:
                        per_bone = m.alloc(4 * n_bones)
                        for b in range(n_bones):
                            m.set_ptr(per_bone + 4 * b, 0)
                    m.set_ptr(per_bone + 4 * key[2], make_object(model))
            if per_bone is not None:
                m.set_ptr(table + 20 * layer + 4, per_bone)
        # every detail list the game may use at a distance shows the figure's main layer
        main = plan['main']
        for detail in ('special', 'high', 'medium', 'low'):
            lst = classic.chosen_layers(self.txt, detail) or []
            if lst and main not in lst:
                other = min(lst)
                for k in (4, 8, 12, 16):
                    m.set_ptr(table + 20 * other + k, m.ptr(table + 20 * main + k))


def write_figure(template_bytes, glb_bytes, game_id, txt_path=None, report=print):
    w = GameWriter(template_bytes, game_id, txt_path)
    try:
        return w.write(glb_bytes, report)
    finally:
        w.close()
