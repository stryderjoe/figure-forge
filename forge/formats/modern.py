"""Characters of the newer TT era (LEGO Batman 2, LEGO Marvel Super Heroes): a definition (.cd) names a skeleton
model (SUPER_MINIFIG -> chars/minifigs/super_characters/super_minifig/super_minifig_nxg.ghg, or its own model)
and the textures each body role wears. Same interface as classic.Character: bones, layers, geometry(), material(),
texture_bytes().

Material roles (measured on LEGO Batman 2's definitions against characters whose colours are known):
the shared minifig's materials are named for their role (HEAD_FRONT_GAME ...); a definition's 'Character Texture'
gives a texture per role number: 1 head front, 2 head back, 3 body front, 4 body back, 5 right arm, 24 left arm,
6 right hand, 26 left hand, 7 hips, 8 right leg, 25 left leg, 10 cape, 13 cape lining.
"""
import os
import struct

from . import nxgdisp, nxgmesh, nxgskel, ttstream
from .classic import FormatError, mat_mul

ROLE = {'HEAD_FRONT': 1, 'HEAD_BACK': 2, 'BODY_FRONT': 3, 'BODY_BACK': 4, 'RIGHTARM': 5, 'LEFTARM': 24,
        'RIGHTHAND': 6, 'LEFTHAND': 26, 'HIPS': 7, 'RIGHTLEG': 8, 'LEFTLEG': 25, 'CAPE': 10, 'CAPE_INSIDE': 13}
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
SKELETON_MODELS = {'super_minifig': ('minifigs', 'super_characters', 'super_minifig', 'super_minifig_nxg.ghg')}


def material_names(d):
    """Names of the model's materials, in table order."""
    a = d.find(b'LTMU')
    if a < 0:
        return []
    b = d.find(b'TDML', a)
    count = struct.unpack_from('>I', d, a + 8)[0]
    names, p = [], a + 12
    while p < b - 3 and len(names) < count:
        n = struct.unpack_from('>H', d, p)[0]
        if 3 <= n < 120 and d[p + 1 + n] == 0 and all(32 < c < 127 for c in d[p + 2:p + 1 + n]):
            names.append(d[p + 2:p + 1 + n].decode())
            p += 2 + n
        else:
            p += 1
    return names


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
                self.attachments.append({k: g.get(k) for k in ('Attachment Name', 'Layer', 'Resource File',
                                                                'Object Name')})
        self.textures = []          # [path of a .tex], indexed by material()['texture']
        self._tex_index = {}
        self.layers = self._layers()

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

    def geometry(self, mesh_index):
        try:
            pos, nrm, uv, col, tris, skin = self.mesh.geometry(mesh_index)
        except (ValueError, struct.error, IndexError):
            return None
        return pos, nrm or [], [tuple(u[:2]) for u in uv] if uv else [], col, [tuple(t) for t in tris], skin

    def material(self, mesh_index):
        mi = self._mesh_mat.get(mesh_index)
        name = self.material_names[mi] if mi is not None and mi < len(self.material_names) else ''
        role = name.split(':')[0].upper()
        role = role[:-5] if role.endswith('_GAME') else role
        path = self.role_textures.get(ROLE.get(role))
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
        return {'index': mi if mi is not None else -1, 'texture': tex, 'colour': (1.0, 1.0, 1.0, 1.0),
                'name': name}

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
