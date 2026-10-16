"""Characters of the newer TT era (LEGO Batman 2, LEGO Marvel Super Heroes): a definition (.cd) names a skeleton
model (SUPER_MINIFIG -> chars/minifigs/super_characters/super_minifig/super_minifig_nxg.ghg, or its own model)
and the textures each body role wears. Same interface as classic.Character: bones, layers, geometry(), material(),
texture_bytes().

Material roles (measured on LEGO Batman 2's definitions against characters whose colours are known):
the shared minifig's materials are named for their role (HEAD_FRONT_GAME ...); a definition's 'Character Texture'
gives a texture per role number: 1 head front, 2 head back, 3 body front, 4 body back, 5 right arm, 24 left arm,
6 right hand, 26 left hand, 7 hips, 8 right leg, 25 left leg, 10 cape, 13 cape lining, 9 hair / hat colour
(attachments' Hat_Colour material).
"""
import os
import struct

from . import nxgdisp, nxgmesh, nxgskel, ttstream
from .classic import FormatError, mat_mul

ROLE = {'HEAD_FRONT': 1, 'HEAD_BACK': 2, 'BODY_FRONT': 3, 'BODY_BACK': 4, 'RIGHTARM': 5, 'LEFTARM': 24,
        'RIGHTHAND': 6, 'LEFTHAND': 26, 'HIPS': 7, 'RIGHTLEG': 8, 'LEFTLEG': 25, 'CAPE': 10, 'CAPE_INSIDE': 13,
        'HAT_COLOUR': 9}
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
SKELETON_MODELS = {'super_minifig': ('minifigs', 'super_characters', 'super_minifig', 'super_minifig_nxg.ghg')}


_PAKS = {}


def _pak_index(chars_dir):
    """{set name (lower case): path of its '<set>_an4_nxg.pak'} for a game (cached)."""
    if chars_dir not in _PAKS:
        out = {}
        for dirpath, _dirs, files in os.walk(chars_dir):
            for f in files:
                low = f.lower()
                if low.endswith('_an4_nxg.pak'):
                    out.setdefault(low[:-len('_an4_nxg.pak')], os.path.join(dirpath, f))
        _PAKS[chars_dir] = out
    return _PAKS[chars_dir]


_MODELS = {}


def _model_index(chars_dir):
    """{file name (lower case): path} of every model under chars (cached)."""
    if chars_dir not in _MODELS:
        out = {}
        for dirpath, _dirs, files in os.walk(chars_dir):
            for f in files:
                low = f.lower()
                if low.endswith(('_nxg.ghg', '_nxg.gsc')):
                    out.setdefault(low, os.path.join(dirpath, f))
        _MODELS[chars_dir] = out
    return _MODELS[chars_dir]


def load_clip(ref):
    """'<pack path>::<entry name>' -> the animation block for the main skeleton (the one with most nodes)."""
    from . import an3, pack, ttdeflate
    path, entry = ref.split('::', 1)
    with open(path, 'rb') as f:
        items = pack.read(f.read())[0]
    data = next(x[2] for x in items if x[0] == entry)
    if ttdeflate.is_packed(data):
        data = ttdeflate.decompress(data)
    blocks = an3.An4Set(ref, data).blocks
    if not blocks:
        raise FormatError(f'{entry}: no animation blocks')
    return max(blocks, key=lambda b: b.nodes)


def material_names(d):
    """Names of the model's materials, in table order. Every material record starts with the word 2 and holds its
    name (u16 length, text, zero) at one fixed distance from that start, which differs between files: the
    distance that gives a valid name for most records is the one."""
    import collections
    import re
    a = d.find(b'LTMU')
    if a < 0:
        return []
    count = struct.unpack_from('>I', d, a + 8)[0]
    b = d.find(b'TDML', a)
    b = len(d) if b < 0 else b
    starts = [m.start() + a for m in re.finditer(re.escape(bytes([0, 0, 0, 2])), d[a:b])]

    def name(at):
        if at + 2 > len(d):
            return None
        n = struct.unpack_from('>H', d, at)[0]
        text = d[at + 2:at + 2 + n]
        if 3 <= n < 120 and text.endswith(bytes([0])) and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_:.\- ]+', text[:-1]):
            return text[:-1].decode()
        return None

    hits = collections.Counter({k: sum(1 for c in starts if name(c + k)) for k in range(0x200, 0x700)})
    if not hits:
        return []
    k = hits.most_common(1)[0][0]
    names, last = [], -1
    for c in starts:
        nm = name(c + k)
        if nm and c > last:
            names.append(nm)
            last = c + k
    return names[:count]


def _find(root, rel):
    """A path below root matched without regard to case (the archives' case varies)."""
    cur = root
    for part in rel:
        try:
            hit = next((e for e in os.listdir(cur) if e.lower() == part.lower()), None)
        except OSError:
            return None
        if hit is None:
            return None
        cur = os.path.join(cur, hit)
    return cur


class Character:
    def __init__(self, cd_path, chars_dir):
        self.path = cd_path
        self.chars_dir = chars_dir
        self.name = os.path.splitext(os.path.basename(cd_path))[0]
        try:
            self.cd = ttstream.Stream(open(cd_path, 'rb').read())
        except (ttstream.StreamError, struct.error, ValueError) as e:
            raise FormatError(f'{self.name}: definition: {e}') from None
        root = self.cd.roots[0][0] if self.cd.roots and self.cd.roots[0] else None
        if root is None:
            raise FormatError(f'{self.name}: empty definition')
        f = root['fields']
        self.model_path = self._model(f.get('Override Model File') or '', f.get('Skeleton Name') or '')
        if not self.model_path:
            raise FormatError(f'{self.name}: model not found')
        self.d = d = open(self.model_path, 'rb').read()
        try:
            self.mesh = nxgmesh.MeshSection(d)
            self.disp = nxgdisp.read(d, len(self.mesh.parts))
        except (ValueError, struct.error, IndexError) as e:
            raise FormatError(f'{self.name}: {e}') from None
        skels = [s for s in nxgskel.read_all(d)
                 if all(m['special'] < len(self.disp['specials']) for m in s['meta'])]
        if not skels:
            raise FormatError(f'{self.name}: no skeleton')
        self.skel = skels[0]
        self.bones = []
        for j in self.skel['joints']:
            b = {'name': j['name'], 'parent': -1 if j['parent'] is None else j['parent'],
                 'bind_local': list(j['bind'])}
            b['world'] = b['bind_local'] if b['parent'] < 0 else mat_mul(b['bind_local'], self.bones[b['parent']]['world'])
            self.bones.append(b)
        self.material_names = material_names(d)
        self.role_textures = {}
        self.layer_choice = {}
        self.attachments = []
        for o in (x for r in self.cd.roots for x in ttstream.walk(r)):
            g = o['fields']
            if o['class'] == 'Character Texture' and isinstance(g.get('Texture File'), str):
                self.role_textures[g.get('Material')] = g['Texture File']
            elif o['class'] == 'Character Layer Special':
                self.layer_choice[g.get('Layer Id')] = g.get('Layer Special')
            elif o['class'] == 'Character Attachment':
                self.attachments.append({k: g.get(k) for k in ('Attachment Name', 'Layer', 'Resource File', 'Locator', 'Tint Colour',
                                                                'Object Name')})
        mask = f.get('Default Layers')
        # 'Default Layers' is a bitmask of the layers a character shows
        self.default_layers = ({i for i in range(32) if mask >> i & 1} if isinstance(mask, int) and mask > 0 else None)
        self.textures = []          # [path of a .tex], indexed by material()['texture']
        self._tex_index = {}
        self.attached = []          # [(model bytes, MeshSection, material names)] of attachments
        self.tints = []             # each attachment's tint (hair colour)
        self.layers = self._layers()
        self._attachments()

    def _attachments(self):
        """A definition's attachments (Batman's cowl is his 'Face') become rigid layers on their locator's bone.
        Mesh numbers of attachment k are 100000 * (k + 1) + its own mesh number."""
        locators, remap = self.skel['locators'], self.skel['remap']
        for att in self.attachments:
            res = att.get('Resource File') or ''
            if not res:
                continue
            rel = res.replace('\\', '/').split('/')
            if rel[0].lower() == 'chars':
                rel = rel[1:]
            stem = rel[-1]
            path = None
            for ext in ('_nxg.ghg', '_nxg.gsc'):          # hair and hats are scenes (.gsc)
                rel[-1] = stem + ext
                path = _find(self.chars_dir, rel) if len(rel) > 1 else None
                if path:
                    break
            if not path:                                   # LEGO Marvel names some by file name alone
                index = _model_index(self.chars_dir)
                path = index.get(stem.lower() + '_nxg.ghg') or index.get(stem.lower() + '_nxg.gsc')
            if not path:
                continue
            try:
                d = open(path, 'rb').read()
                mesh = nxgmesh.MeshSection(d)
                disp = nxgdisp.read(d, len(mesh.parts))
            except (ValueError, struct.error, IndexError, OSError):
                continue
            if not disp['specials']:
                continue
            loc = att.get('Locator')
            li = remap[loc] if isinstance(loc, int) and 0 <= loc < len(remap) else 255
            if li == 255 or li >= len(locators):
                continue
            k = len(self.attached)
            self.attached.append((d, mesh, material_names(d)))
            self.tints.append(tuple(att.get('Tint Colour') or (1.0, 1.0, 1.0)))
            base = 100000 * (k + 1)
            # the object the definition names, else the first (the most detailed)
            want = (att.get('Object Name') or '').lower()
            sp = next((x for x in disp['specials'] if want and x['name'].lower() == want), disp['specials'][0])
            names = self.attached[-1][2]
            # MAT_OCCLUDE pieces only mask other pieces in the game's renderer: they are not drawn
            mats = {base + m: mat for m, mat in sp['parts']
                    if not (mat < len(names) and 'OCCLUDE' in names[mat].upper())}
            self._mesh_mat.update(mats)
            label = att.get('Attachment Name') or stem            # LEGO Marvel leaves the name out: the file's
            self.layers.append({'index': len(self.layers), 'name': f'TT_{label}',
                                'chosen': True, 'parts': [{
                                    'special': sp['name'], 'kind': 'rigid', 'bone': locators[li]['joint'],
                                    'meshes': sorted(mats), 'mats': mats, 'matrix': list(locators[li]['matrix'])}]})

    def _model(self, override, skeleton):
        if override:
            rel = override.replace('\\', '/').split('/')
            if rel and rel[0].lower() == 'chars':
                rel = rel[1:]
            rel[-1] = rel[-1] + '_nxg.ghg' if not rel[-1].lower().endswith('.ghg') else rel[-1]
            hit = _find(self.chars_dir, rel)
            if hit:
                return hit
        known = SKELETON_MODELS.get(skeleton.lower())
        if known:
            hit = _find(self.chars_dir, known)
            if hit:
                return hit
        own = os.path.join(os.path.dirname(self.path), self.name + '_nxg.ghg')
        return own if os.path.exists(own) else None

    def _layers(self):
        specials = self.disp['specials']
        out = []
        for li, layer in enumerate(self.skel['layers']):
            want = self.layer_choice.get(li)
            if want is not None and want.lower() == 'all':        # every variant of the layer
                want = None
            entry = {'index': li, 'name': layer['name'], 'parts': [], 'chosen': want is not None}
            for item in layer['items']:
                sp = specials[item['special']]
                if want is not None and sp['name'] != want:
                    continue
                if want is None and not sp['name'].lower().endswith('_mesh'):
                    continue
                low = sp['name'].lower()
                if want is None and ('skeleton' in low or 'mech' in low or low.startswith('zhead')
                                     or 'boxing' in low):
                    continue
                part = {'special': sp['name'], 'meshes': [m for m, _mat in sp['parts']],
                        'mats': {m: mat for m, mat in sp['parts']},
                        # skinned layers carry an all-zero matrix: their vertices are already in model space
                        'matrix': list(sp['matrix']) if any(sp['matrix']) else IDENTITY}
                if item['kind'] == 0:
                    part.update(kind='rigid', bone=item['joint'])
                else:
                    part.update(kind='skinned', bone=-1)
                entry['parts'].append(part)
            out.append(entry)
        self._mesh_mat = {}
        for layer in out:
            for part in layer['parts']:
                self._mesh_mat.update(part['mats'])
        return out

    def _source(self, mesh_index):
        if mesh_index >= 100000:
            d, mesh, names = self.attached[mesh_index // 100000 - 1]
            return mesh, names, mesh_index % 100000
        return self.mesh, self.material_names, mesh_index

    def geometry(self, mesh_index):
        mesh, _names, i = self._source(mesh_index)
        try:
            pos, nrm, uv, col, tris, skin = mesh.geometry(i)
        except (ValueError, struct.error, IndexError):
            return None
        return pos, nrm or [], [tuple(u[:2]) for u in uv] if uv else [], col, [tuple(t) for t in tris], skin

    def material(self, mesh_index):
        mi = self._mesh_mat.get(mesh_index)
        _mesh, names, _i = self._source(mesh_index)
        name = names[mi] if mi is not None and mi < len(names) else ''
        role = name.split(':')[0].upper()
        role = role[:-5] if role.endswith('_GAME') else role
        path = self.role_textures.get(ROLE.get(role))
        if path is None and role == 'CAPE_INSIDE':
            path = self.role_textures.get(ROLE['CAPE'])          # no lining of its own: the cape's texture
        tex = -1
        if path:
            tex = self._tex_index.get(path)
            if tex is None:
                rel = path.replace('\\', '/').split('/')
                if rel[0].lower() == 'chars':
                    rel = rel[1:]
                rel[-1] = rel[-1] + '_nxg.tex'
                full = _find(self.chars_dir, rel)
                tex = -1
                if full:
                    tex = len(self.textures)
                    self.textures.append(full)
                self._tex_index[path] = tex
        colour, vc = (1.0, 1.0, 1.0, 1.0), False
        if tex < 0:
            # untextured pieces (faces, hair) are coloured by their vertex colours, 128 = full brightness,
            # times the attachment's tint (a hair colour)
            geo = self.geometry(mesh_index)
            vc = bool(geo and geo[3])
            if mesh_index >= 100000:
                t = self.tints[mesh_index // 100000 - 1]
                colour = (t[0], t[1], t[2], 1.0)
        return {'index': mi if mi is not None else -1, 'texture': tex, 'colour': colour, 'vertex_colours': vc,
                'name': name}

    def _vertex_colour(self, mesh_index):
        geo = self.geometry(mesh_index)
        col = geo[3] if geo else None
        if not col:
            return (1.0, 1.0, 1.0, 1.0)
        n = len(col)
        avg = [min(1.0, sum(c[k] for c in col) / n * 2 / 255) for k in range(3)]
        return (avg[0], avg[1], avg[2], 1.0)

    def clip_list(self):
        """[(clip name, '<pack>::<entry>', fps, action)] from the definition's animation sets."""
        from . import pack
        index = _pak_index(self.chars_dir)
        out, seen = [], set()
        for o in (x for r in self.cd.roots for x in ttstream.walk(r)):
            if o['class'] != 'Character Anim Set Reference':
                continue
            path = index.get((o['fields'].get('CharAnimSet Name') or '').lower())
            if not path:
                continue
            try:
                with open(path, 'rb') as f:
                    items = pack.read(f.read())[0]
            except (OSError, ValueError, struct.error):
                continue
            for item in items:
                entry = item[0]
                if not entry.lower().endswith('.an4'):
                    continue
                name = entry[:-4]
                for tail in ('_def_nxg', '_nxg'):
                    if name.lower().endswith(tail):
                        name = name[:-len(tail)]
                        break
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                out.append((name, f'{path}::{entry}', 30.0, None))
        return out

    def has_texture(self, index):
        return 0 <= index < len(self.textures)

    def texture_bytes(self, index):
        with open(self.textures[index], 'rb') as f:
            return f.read()

    def texture_format(self, index):
        if not self.has_texture(index):
            return ''
        h = self.texture_bytes(index)[:128]
        return h[84:88].decode('latin1') if h[:4] == b'DDS ' else ''
