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
        'HAT_COLOUR': 9,
        # LEGO Pirates names one material per pair of arms, hands and legs (measured against the .cd roles of
        # jacksparrow_captain and elizabeth_redgown)
        'ARMS': 5, 'HANDS': 6, 'LEGS': 8, 'SKIRT': 12,
        # LEGO Batman 3 writes the sides with an underscore
        'RIGHT_ARM': 5, 'LEFT_ARM': 24, 'RIGHT_HAND': 6, 'LEFT_HAND': 26, 'RIGHT_LEG': 8, 'LEFT_LEG': 25,
        'GLASSES_LEFT': 9, 'GLASSES_RIGHT': 10,
        'FACETEX_MAT': 1}           # LEGO Batman 3's texture-faced heads: the face print
IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
SKELETON_MODELS = {'super_minifig': [('minifigs', 'super_characters', 'super_minifig', 'super_minifig_nxg.ghg'),
                                     ('super_character', 'super_minifig', 'super_minifig_nxg.ghg')]}   # LEGO Batman 3


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
        # LEGO Pirates keeps its sets as '<set>.pac' of .an3 entries (used only where no .an4 pack exists);
        # LEGO Batman 3 as folders '<set>_as' of loose .an4 files
        for dirpath, dirs, files in os.walk(chars_dir):
            for f in files:
                if f.lower().endswith('.pac'):
                    out.setdefault(f.lower()[:-4], os.path.join(dirpath, f))
            for dname in dirs:
                if dname.lower().endswith('_as'):
                    out.setdefault(dname.lower()[:-3], os.path.join(dirpath, dname))
        _PAKS[chars_dir] = out
    return _PAKS[chars_dir]


_MODELS = {}
_LOADED = {}


class Model:
    """One model file, parsed once and shared (every minifig of a game uses the same base model).
    Material names are read on first use."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.d = f.read()
        self.mesh = nxgmesh.MeshSection(self.d)
        self.disp = nxgdisp.read(self.d, len(self.mesh.parts))
        self._skels = None
        self._names = None

    @property
    def skeletons(self):
        if self._skels is None:
            self._skels = nxgskel.read_all(self.d)
        return self._skels

    @property
    def material_names(self):
        if self._names is None:
            self._table = material_table(self.d)
            self._names = [n for n, _ in self._table]
        return self._names

    @property
    def bundle(self):
        """(file, [(start, end)]) of the model's own textures: its .nxg_textures companion (LEGO Marvel), else
        the DDS images inside the model (LEGO Batman 2)."""
        if getattr(self, '_bundle', None) is None:
            comp = self.path.rsplit('.', 1)[0] + '.nxg_textures'
            src = comp if os.path.exists(comp) else self.path
            with open(src, 'rb') as f:
                data = f.read()
            starts, at = [], data.find(b'DDS |')
            while at >= 0:
                starts.append(at)
                at = data.find(b'DDS |', at + 4)
            spans = [(x, starts[i + 1] if i + 1 < len(starts) else len(data)) for i, x in enumerate(starts)]
            # the texture list names each texture; an entry without a name has no image (LEGO Marvel ends its
            # list with two such), so texture numbers are list places, not image places
            named = texture_list(self.d)
            if named is not None and sum(named) == len(spans):
                it = iter(spans)
                spans = [next(it) if has else None for has in named]
            self._bundle = (src, spans)
        return self._bundle

    def colour_texture(self, material_index):
        """(file, start, end) of a material's own colour map, or None."""
        names = self.material_names
        if getattr(self, '_slots', None) is None:
            self._slots = texture_slots(self.d, self._table, len(self.bundle[1]))
        if not 0 <= material_index < len(names) or self._slots[material_index] is None:
            return None
        row = self._slots[material_index]
        t = row[0]
        if t < 0 or t >= len(self.bundle[1]) or self.bundle[1][t] is None:
            return None
        layered = self._layered(row)
        if layered:
            return layered
        return (self.bundle[0],) + self.bundle[1][t]

    def _span(self, t):
        sp = self.bundle[1]
        return sp[t] if 0 <= t < len(sp) and sp[t] else None

    def _flat_colour(self, t):
        """The colour of a tiny one-colour texture (an 8 x 8 DXT1), or None."""
        span = self._span(t)
        if span is None or span[1] - span[0] > 300:
            return None
        import io
        from PIL import Image
        with open(self.bundle[0], 'rb') as f:
            f.seek(span[0])
            im = Image.open(io.BytesIO(f.read(span[1] - span[0]))).convert('RGB')
        lo, hi = zip(*im.getextrema())
        return lo if lo == hi else None

    def _layered(self, row):
        """LEGO Marvel's layered metal materials (material_remap_destroyer, _archangel: 165 of them): slot 0 holds
        a stock cyan 8 x 8 texture (0, 172, 238), slot 1 the metal's flat colour and slot 4 a mask whose blue
        channel draws the panel lines. Shown as the metal's colour with the lines darkened:
        ('layered', file, colour, mask span), or None for any other material."""
        if len(row) < 5 or self._flat_colour(row[0]) != (0, 172, 238):
            return None
        mask = self._span(row[4])
        colour = self._flat_colour(row[1]) if row[1] >= 0 else None
        if mask is None or mask[1] - mask[0] < 4000 or colour is None:
            return None
        return ('layered', self.bundle[0], colour, mask)

    def material_by_name(self, name):
        """The material of that name: its full name first (LEGO Batman 3 asks for 'MAT_ARM_LEFT:VARIANT_MANUAL'),
        else the name before any ':' suffix."""
        full = [n.lower() for n in self.material_names]
        if name.lower() in full:
            return full.index(name.lower())
        names = [n.split(':')[0] for n in full]
        want = name.split(':')[0].lower()
        return names.index(want) if want in names else -1


def model(path):
    """The parsed model at path (the last 48 used are kept)."""
    m = _LOADED.pop(path, None)
    if m is None:
        m = Model(path)
    _LOADED[path] = m
    while len(_LOADED) > 48:
        _LOADED.pop(next(iter(_LOADED)))
    return m


def _model_index(chars_dir):
    """{file name (lower case): path} of every model under chars (cached)."""
    if chars_dir not in _MODELS:
        out = {}
        for dirpath, _dirs, files in os.walk(chars_dir):
            for f in files:
                low = f.lower()
                if low.endswith(('_nxg.ghg', '_nxg.gsc')):
                    out.setdefault(low, os.path.join(dirpath, f))
                elif low.endswith(('_dx11.ghg', '_dx11.gsc')):          # LEGO Batman 3, found by the same names
                    out.setdefault(low.replace('_dx11.', '_nxg.'), os.path.join(dirpath, f))
        _MODELS[chars_dir] = out
    return _MODELS[chars_dir]


_CDS = {}


def _cd_index(chars_dir):
    """{definition file name (lower case): path} under chars (cached)."""
    if chars_dir not in _CDS:
        out = {}
        for dirpath, _dirs, files in os.walk(chars_dir):
            for f in files:
                if f.lower().endswith('.cd'):
                    out.setdefault(f.lower(), os.path.join(dirpath, f))
        _CDS[chars_dir] = out
    return _CDS[chars_dir]


def load_clip(ref):
    """'<pack path>::<entry name>' -> the animation block for the main skeleton (the one with most nodes)."""
    from . import an3, pack, ttdeflate
    path, entry = ref.split('::', 1)
    if os.path.isdir(path):                         # LEGO Batman 3: a folder of loose files
        with open(os.path.join(path, entry), 'rb') as f:
            data = f.read()
    else:
        with open(path, 'rb') as f:
            items = pack.read(f.read())[0]
        data = next(x[2] for x in items if x[0] == entry)
    if ttdeflate.is_packed(data):
        data = ttdeflate.decompress(data)
    if data[4:8] == b'ANI9':                        # LEGO Pirates: one block after its size word
        return an3.An3(ref, data, 4)
    blocks = an3.An4Set(ref, data).blocks
    if not blocks:
        raise FormatError(f'{entry}: no animation blocks')
    return max(blocks, key=lambda b: b.nodes)


def texture_list(d):
    """Per entry of a model's texture list ('HGXT'), whether it names a texture (has an image), or None."""
    at = d.find(b'HGXT')
    if at < 0 or d[at + 8:at + 12] != b'ROTV':
        return None
    if struct.unpack_from('>I', d, at + 4)[0] >= 12:
        # LEGO Batman 3: one list; per entry a 16-byte hash, 4 bytes, a 1-byte name length, the name, a flag byte
        n = struct.unpack_from('>I', d, at + 12)[0]
        p, out = at + 16, []
        for _ in range(n):
            ln = d[p + 20]
            out.append(ln > 1)
            p += 22 + ln
        return out
    try:
        m = struct.unpack_from('>I', d, at + 12)[0]
        p = at + 16 + 4 * m
        if d[p:p + 4] != b'ROTV':
            return None
        n = struct.unpack_from('>I', d, p + 4)[0]
        p += 8
        out = []
        for _ in range(n):
            ln = struct.unpack_from('>I', d, p + 16)[0]
            out.append(ln > 1)
            p += 24 + ln
        return out
    except struct.error:
        return None


def material_table(d):
    """[(name, position of its length word)] of the model's materials, in table order. Every material record
    starts with the word 2 and holds its name (u16 length, text, zero) at one fixed distance from that start,
    which differs between files: the distance that gives a valid name for most records is the one."""
    import collections
    import re
    a = d.find(b'LTMU')
    if a < 0:
        return []
    count = struct.unpack_from('>I', d, a + 8)[0]
    b = d.find(b'TDML', a)
    b = len(d) if b < 0 else b
    starts = [m.start() + a for m in re.finditer(re.escape(bytes([0, 0, 0, 2])), d[a:b])]

    pirates = struct.unpack_from('>I', d, a + 4)[0] == 56

    def name(at):
        if at + 4 > len(d):
            return None
        if pirates:
            # LEGO Pirates: u32 length, the text without a closing zero (774 bytes into the record, measured)
            n = struct.unpack_from('>I', d, at)[0]
            text = d[at + 4:at + 4 + n] if 2 <= n < 120 else b''
            return text.decode() if text and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_:.\- ]+', text) else None
        n = struct.unpack_from('>H', d, at)[0]
        text = d[at + 2:at + 2 + n] if 3 <= n < 120 else b''
        if text.endswith(bytes([0])) and re.fullmatch(rb'[A-Za-z_][A-Za-z0-9_:.\- ]+', text[:-1]):
            return text[:-1].decode()
        return None

    if pirates:
        k = 774                 # LEGO Pirates: fixed (a one-material hat gives too few records to vote)
    else:
        hits = collections.Counter({k: sum(1 for c in starts if name(c + k)) for k in range(0x200, 0x700)})
        if not hits:
            return []
        k = hits.most_common(1)[0][0]
    out, last = [], -1
    for c in starts:
        nm = name(c + k)
        if nm and c > last:
            out.append((nm, c + k))
            last = c + k
    return out[:count]


def material_names(d):
    return [n for n, _ in material_table(d)]


# material version -> where its texture numbers start, measured back from the name: LEGO Marvel 176 / 177 (helmets,
# material remaps), LEGO Pirates 56 (hair, body and head maps), LEGO Batman 2 132 (body, head and arm templates),
# LEGO Batman 3 202 (plain parts: lego_white, then alpha; the normal maps sit 24 bytes earlier)
GAPS = {176: [564], 177: [564], 56: [497], 132: [514], 202: [648]}


def texture_slots(d, table, texture_count):
    """Per material the 18 texture numbers of its record (-1 = none; slot 0 is the colour map). They end a
    fixed distance before the name: 564 bytes in LEGO Marvel's material versions 176 and 177 (measured on
    material-remap and helmet models); for other versions the distance where every number is valid and most
    colour maps are set."""
    a = d.find(b'LTMU')
    version = struct.unpack_from('>I', d, a + 4)[0] if a >= 0 else 0
    gaps = GAPS.get(version) or list(range(520, 640, 4))
    slots = 16 if version == 56 else 18        # LEGO Pirates has 16 (two other numbers follow)
    best = None
    for gap in gaps:
        rows = []
        for _n, at in table:
            if at - gap < 0:
                break
            rows.append(struct.unpack_from(f'>{slots}i', d, at - gap) + (-1,) * (18 - slots))
        if len(rows) != len(table) or not all(-1 <= x < max(texture_count, 1) for r in rows for x in r):
            continue
        score = sum(1 for r in rows if r[0] >= 0)
        if best is None or score > best[0]:
            best = (score, rows, gap)
    return best[1] if best else [None] * len(table)


def texture_slot_gap(d, table, texture_count):
    """The distance from a material's name back to its texture numbers (as texture_slots finds it), or None."""
    rows = texture_slots(d, table, texture_count)
    if not rows or rows[0] is None:
        return None
    a = d.find(b'LTMU')
    version = struct.unpack_from('>I', d, a + 4)[0]
    slots = 16 if version == 56 else 18
    gaps = GAPS.get(version) or list(range(520, 640, 4))
    for gap in gaps:
        if all(struct.unpack_from(f'>{slots}i', d, at - gap) + (-1,) * (18 - slots) == tuple(r)
               for (_n, at), r in zip(table, rows)):
            return gap
    return None


def _find(root, rel):
    """A path below root matched without regard to case (the archives' case varies). A name ending in '_nxg.*'
    also finds LEGO Batman 3's '_dx11.*'."""
    hit = _find_exact(root, rel)
    if hit is None and rel and '_nxg.' in rel[-1].lower():
        hit = _find_exact(root, list(rel[:-1]) + [rel[-1].lower().replace('_nxg.', '_dx11.')])
    return hit


def _find_exact(root, rel):
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
    def __init__(self, cd_path, chars_dir, depth=0):
        self._depth = depth
        self._baked = {}            # mesh number -> geometry fixed in a pose (attachments with a skeleton)
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
        self.model_path = self._model(f.get('Override Model File') or f.get('Model File') or '', f.get('Skeleton Name') or '')
        if not self.model_path:
            raise FormatError(f'{self.name}: model not found')
        try:
            self.base = model(self.model_path)
            self.d, self.mesh, self.disp = self.base.d, self.base.mesh, self.base.disp
        except (ValueError, struct.error, IndexError) as e:
            raise FormatError(f'{self.name}: {e}') from None
        skels = [s for s in self.base.skeletons
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
        self.role_textures = {}
        self.role_tints = {}        # LEGO Batman 3: a role's colour, multiplying its material's own map
        self.layer_choice = {}
        self.attachments = []
        for o in (x for r in self.cd.roots for x in ttstream.walk(r)):
            g = o['fields']
            if o['class'] == 'Character Texture' and isinstance(g.get('Texture File'), str):
                self.role_textures[g.get('Material')] = g['Texture File']
            elif o['class'] == 'Character Material Mapping' and g.get('Diffuse Texture File 1'):
                # LEGO Pirates' older definitions: a role's texture as its first diffuse map
                self.role_textures[g.get('Material')] = g['Diffuse Texture File 1']
            elif o['class'] == 'Character Material' and g.get('Source Material Resource File'):
                # LEGO Marvel: the role wears a named material of another model (a material remap)
                self.role_textures.setdefault(g.get('Material'), ('material', g['Source Material Resource File'],
                                                                  g.get('Source Material') or ''))
            elif o['class'] == 'Character Material' and isinstance(g.get('Layer 1 Tint Colour'), tuple):
                # LEGO Batman 3: plain parts are the material's white map times this colour
                self.role_tints[g.get('Material')] = g['Layer 1 Tint Colour']
            elif o['class'] in ('Character Layer Special', 'Character Layer'):     # 'Character Layer': LEGO Pirates
                self.layer_choice[g.get('Layer Id', g.get('Layer'))] = g.get('Layer Special')
            elif o['class'] == 'Character Attachment':
                self.attachments.append({k: g.get(k) for k in ('Attachment Name', 'Layer', 'Resource File', 'Locator', 'Tint Colour',
                                                                'Object Name', 'Object Offset')})
        mask = f.get('Default Layers')
        # 'Default Layers' is a bitmask of the layers a character shows
        self.default_layers = ({i for i in range(32) if mask >> i & 1} if isinstance(mask, int) and mask > 0 else None)
        self.textures = []          # [path of a .tex], indexed by material()['texture']
        self._tex_index = {}
        self.attached = []          # [Model] of attachments
        self.tints = []             # each attachment's tint (hair colour)
        self.attachment_roles = {}  # attachment number -> {role: texture} of its own definition (texture faces)
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
            roles = None
            if not path:
                scene = self._scene_attachment(stem)
                if scene:
                    path, obj, roles = scene
                    att = dict(att, **{'Object Name': obj})
            if not path:
                # LEGO Marvel: capes and skinned hair are small definitions of their own (cape_red.cd)
                self._sub_attachment(att, stem)
                continue
            try:
                am = model(path)
                disp = am.disp
            except (ValueError, struct.error, IndexError, OSError):
                continue
            if not disp['specials']:
                continue
            loc = att.get('Locator')
            li = remap[loc] if isinstance(loc, int) and 0 <= loc < len(remap) else 255
            if li == 255 or li >= len(locators):
                continue
            k = len(self.attached)
            self.attached.append(am)
            self.tints.append(self._attachment_tint(att, stem))
            roles = roles or self._own_roles(stem)
            if roles:
                self.attachment_roles[k] = roles
            base = 100000 * (k + 1)
            # the object the definition names, else the first (the most detailed)
            want = (att.get('Object Name') or '').lower()
            specials = disp['specials']
            named = [x for x in specials if want and x['name'].lower() == want]
            ingame = [x for x in specials if x['name'].lower().endswith('_ingame')]
            # the object the definition names, else the in-game pieces (LEGO Marvel's helmets: helmet and
            # visor), else the first detail level's pieces
            # (LEGO Batman 3's Killer Croc head: Head, Head_NonBlended and Jaw): the pieces up to the first repeated name
            first = []
            for x in specials:
                if any(x['name'] == y['name'] for y in first):
                    break
                first.append(x)
            # a '_NonBlended' piece is the still copy of a piece drawn with expressions: never both
            # (LEGO Batman 3's Adam West: a second, open mouth over the face)
            blended = {x['name'].lower() for x in first}
            first = [x for x in first if not (x['name'].lower().endswith('_nonblended') and
                                              x['name'].lower()[:-len('_nonblended')] in blended)]
            if not named and not ingame:
                first = self._own_layer_choice(stem, am, specials, first)
            chosen = named or ingame or first or specials[:1]
            posed_pair = False                  # (bind, idle) worlds of the attachment's own definition, when read
            # a rigid piece of the attachment sits on a joint of its own skeleton: its vertices are in that
            # joint's frame, so the joint's bind position comes first, then the locator
            joint_world, mentioned, skinned_sp = {}, set(), set()
            for sk in am.skeletons:                  # one copy per detail level, each naming its own pieces
                worlds = []
                for j in sk['joints']:
                    m = list(j['bind'])
                    worlds.append(m if j['parent'] is None else mat_mul(m, worlds[j['parent']]))
                for item in sk['meta']:
                    # a piece's first mention decides (LEGO Pirates' goatee face: skinned in the first copy,
                    # rigid on a turned joint in a later one)
                    if item['special'] in mentioned:
                        continue
                    mentioned.add(item['special'])
                    if item['kind'] == 0 and item['joint'] < len(worlds):
                        joint_world[item['special']] = worlds[item['joint']]
                    elif item['kind'] != 0:
                        skinned_sp.add(item['special'])
            label = att.get('Attachment Name') or stem            # LEGO Marvel leaves the name out: the file's
            parts = []
            for sp in chosen:
                # MAT_OCCLUDE pieces are drawn invisibly: they hide what lies behind them (the inside of the mouth)
                mats = {base + m: mat for m, mat in sp['parts']}
                self._mesh_mat.update(mats)
                si = specials.index(sp)
                place = list(locators[li]['matrix'])
                bone = locators[li]['joint']
                if si in joint_world:
                    place = mat_mul(joint_world[si], place)
                elif si in skinned_sp:
                    if posed_pair is False:
                        posed_pair = self._posed_attachment(stem, path)
                    matched = self._match_joint(am, base, mats, posed_pair[1] if posed_pair else None)
                    if matched is not None:
                        bone, place = matched
                        if posed_pair:
                            for mi in mats:
                                self._bake(mi, *posed_pair)
                offset = att.get('Object Offset')
                if isinstance(offset, (bytes, bytearray)) and len(offset) == 64:
                    # the definition's own offset comes first (LEGO Marvel's big figures scale their faces by 1.376)
                    m = list(struct.unpack('<16f', offset))
                    if any(abs(a - b) > 1e-6 for a, b in zip(m, IDENTITY)) and all(abs(v) < 1e3 for v in m):
                        place = mat_mul(m, place)
                parts.append({'special': sp['name'], 'kind': 'rigid', 'bone': bone,
                              'meshes': sorted(mats), 'mats': mats, 'matrix': place})
            self.layers.append({'index': len(self.layers), 'name': f'TT_{label}', 'chosen': True, 'parts': parts,
                                'from_layer': att.get('Layer')})

    def _sub_attachment(self, att, stem):
        """An attachment that is a definition (a cape, skinned hair): its visible pieces, skinned to its own
        skeleton, are fixed in the first frame of its idle (a hanging cape, settled hair) and placed on the
        locator like any rigid attachment. Its colour is the definition's tint times the attachment's."""
        from .. import anim
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd or getattr(self, '_depth', 0) > 1:
            return
        try:
            sub = Character(cd, self.chars_dir, depth=getattr(self, '_depth', 0) + 1)
        except (FormatError, ValueError, struct.error, IndexError, OSError):
            return
        locators, remap = self.skel['locators'], self.skel['remap']
        loc = att.get('Locator')
        li = remap[loc] if isinstance(loc, int) and 0 <= loc < len(remap) else 255
        if li == 255 or li >= len(locators):
            return
        sub.id = f'{getattr(self, "id", self.name)}#{stem}'
        sub.common = []
        world = self._idle_world(sub)
        import numpy as np
        skin_m = [np.linalg.inv(np.asarray(b['world'], dtype=np.float64).reshape(4, 4)) @
                  np.asarray(w, dtype=np.float64).reshape(4, 4) for b, w in zip(sub.bones, world)]
        k = len(self.attached)
        self.attached.append(sub.base)
        roles = self._own_roles(stem)
        if roles:
            self.attachment_roles[k] = roles
        t = tuple(att.get('Tint Colour') or (1.0, 1.0, 1.0))
        dt = sub.cd.roots[0][0]['fields'].get('Default Tint Colour') or (1.0, 1.0, 1.0)
        self.tints.append((t[0] * dt[0], t[1] * dt[1], t[2] * dt[2]))
        base = 100000 * (k + 1)
        meshes, mats = [], {}
        shown = sub.default_layers
        for L in sub.layers:
            if L['name'].startswith('TT_') or (shown is not None and L['index'] not in shown):
                continue
            for part in L['parts']:
                for mi in part['meshes']:
                    geo = sub.geometry(mi)
                    if geo is None:
                        continue
                    pos, nrm, uv, col, tris, skin = geo
                    out = []
                    for i, v in enumerate(pos):
                        acc = np.zeros(3)
                        weights = skin[i] if skin and skin[i] else [(part['bone'] if part['bone'] >= 0 else 0, 1.0)]
                        for bj, w in weights:
                            if bj < len(skin_m):
                                acc += w * (np.array([v[0], v[1], v[2], 1.0]) @ skin_m[bj])[:3]
                        out.append(tuple(acc))
                    self._baked[base + mi] = (out, [], uv, col, tris, None)
                    mats[base + mi] = part['mats'].get(mi, 0)
                    meshes.append(base + mi)
        if not meshes:
            self.attached.pop()
            self.tints.pop()
            self.attachment_roles.pop(k, None)
            return
        self._mesh_mat.update(mats)
        self.layers.append({'index': len(self.layers), 'name': f'TT_{att.get("Attachment Name") or stem}',
                            'chosen': True, 'parts': [{'special': stem, 'kind': 'rigid', 'bone': locators[li]['joint'],
                                                       'meshes': meshes, 'mats': mats,
                                                       'matrix': list(locators[li]['matrix'])}],
                            'from_layer': att.get('Layer')})

    @staticmethod
    def _idle_world(sub):
        """World matrices of a definition's bones in the first frame of its idle (the bind pose without one)."""
        from .. import anim
        world = [list(b['world']) for b in sub.bones]
        try:
            clips = anim.clip_list(sub)
            pick = (next((c for c in clips if c['name'].lower() == 'idle'), None)
                    or next((c for c in clips if 'idle' in c['name'].lower()), None) or (clips[0] if clips else None))
            if pick:
                from . import classic as _classic
                import numpy as np
                from .. import skeleton as _skel
                clip = _classic.load_clip(pick['file'])
                pose = clip.pose(0)
                nf = getattr(clip, 'node_flags', None) or []
                world = []
                for j, b in enumerate(sub.bones):
                    if j < len(pose):
                        m = anim.node_matrix(sub, j, pose[j], nf[j] if j < len(nf) else 0)
                        local = _skel.mirror(np.asarray(m).reshape(16)).reshape(16).tolist()
                    else:
                        local = list(b['bind_local'])
                    world.append(local if b['parent'] < 0 else mat_mul(local, world[b['parent']]))
        except (FormatError, ValueError, struct.error, IndexError, OSError, StopIteration):
            world = [list(b['world']) for b in sub.bones]
        return world

    def _posed_attachment(self, stem, path):
        """(bind worlds, idle worlds) of an attachment model that has a definition of the same name with an
        animation (LEGO Marvel's backpack_venom.cd: its vines hang in its idle), or None."""
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd or getattr(self, '_depth', 0) > 1:
            return None
        try:
            sub = Character(cd, self.chars_dir, depth=getattr(self, '_depth', 0) + 1)
        except (FormatError, ValueError, struct.error, IndexError, OSError):
            return None
        if os.path.normcase(os.path.abspath(sub.model_path)) != os.path.normcase(os.path.abspath(path)):
            return None
        sub.id = f'{getattr(self, "id", self.name)}#{stem}'
        sub.common = []
        posed = self._idle_world(sub)
        bind = [list(b['world']) for b in sub.bones]
        return (bind, posed) if posed != bind else None

    def _bake(self, mesh_index, bind, posed):
        """Fix a skinned piece in a pose: each vertex moved by its bones from bind to posed (model space)."""
        import numpy as np
        geo = self.geometry(mesh_index)
        if geo is None:
            return
        pos, nrm, uv, col, tris, skin = geo
        if not skin:
            return
        skin_m = [np.linalg.inv(np.asarray(b, dtype=np.float64).reshape(4, 4)) @
                  np.asarray(w, dtype=np.float64).reshape(4, 4) for b, w in zip(bind, posed)]
        out = []
        for i, v in enumerate(pos):
            acc = np.zeros(3)
            for bj, w in skin[i] or [(0, 1.0)]:
                if bj < len(skin_m):
                    acc += w * (np.array([v[0], v[1], v[2], 1.0]) @ skin_m[bj])[:3]
            out.append(tuple(float(x) for x in acc))
        self._baked[mesh_index] = (out, [], uv, col, tris, None)

    def _match_joint(self, am, base, mats, posed=None):
        """A skinned attachment piece whose skeleton copies the character's joints as '<joint>_match' (LEGO Batman
        3's Killer Croc head, LEGO Marvel's Venom backpack): its vertices are in the character's model space. It
        rides the character joint named by the nearest '_match' joint above its most weighted one: (bone, matrix),
        or None."""
        import numpy as np
        if not am.skeletons:
            return None
        joints = am.skeletons[0]['joints']
        weight = {}
        for mi in mats:
            geo = self.geometry(mi)
            for ws in (geo[5] or []) if geo else []:
                for j, w in ws or ():
                    weight[j] = weight.get(j, 0.0) + w
        if not weight:
            return None
        j = max(weight, key=weight.get)
        names = {b['name'].lower(): i for i, b in enumerate(self.bones)}
        worlds = []
        for jt in joints:
            m = list(jt['bind'])
            worlds.append(m if jt['parent'] is None else mat_mul(m, worlds[jt['parent']]))
        if posed is not None and len(posed) == len(worlds):
            worlds = posed                      # the piece is fixed in its idle: the '_match' joint's frame there
        while j is not None and 0 <= j < len(joints):
            name = joints[j]['name'].lower()
            if name.endswith('_match') and name[:-6] in names:
                # into the '_match' joint's own frame, which the character's joint of that name carries
                inv = np.linalg.inv(np.asarray(worlds[j], dtype=np.float64).reshape(4, 4))
                return names[name[:-6]], [float(v) for v in inv.ravel()]
            j = joints[j]['parent']
        return None

    def _scene_attachment(self, stem):
        """An attachment whose definition names a scene and an object in it instead of a model (LEGO Batman 3's
        texture-faced heads: facetex_goonlanternb.cd shows FaceTex_Head of FACETEX_HEAD with its own face print as
        material 1): (scene path, object name, {role: texture}), or None."""
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd:
            return None
        try:
            stream = ttstream.Stream(open(cd, 'rb').read())
        except (ttstream.StreamError, struct.error, ValueError, OSError):
            return None
        root = stream.roots[0][0] if stream.roots and stream.roots[0] else None
        scene = root and root['fields'].get('Scene File')
        if not isinstance(scene, str) or not scene:
            return None
        rel = scene.replace('\\', '/').split('/')
        if rel[0].lower() == 'chars':
            rel = rel[1:]
        path = _find(self.chars_dir, rel[:-1] + [rel[-1] + '_nxg.gsc']) or _find(self.chars_dir, rel[:-1] + [rel[-1] + '_nxg.ghg'])
        if not path:
            return None
        roles = {}
        for o in (x for r in stream.roots for x in ttstream.walk(r)):
            g = o['fields']
            if o['class'] == 'Character Texture' and isinstance(g.get('Texture File'), str):
                roles[g.get('Material')] = g['Texture File']
        return path, root['fields'].get('Scene Object') or '', roles

    def _own_layer_choice(self, stem, am, specials, first):
        """The pieces an attachment's own definition shows: its Default Layers pick layers of its model's skeleton
        (LEGO Batman 3: face_batman2014.cd shows layers 0 and 1, the face and the eyeholes; face_lexluthor.cd only
        layer 0, without the target). A model without a skeleton of its own (face_batman: FACE, ElectroFlash,
        Hat_SensorGoggles, Eyes) shows only its FACE under layer 0 alone. Without such a definition: first."""
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd:
            return first
        try:
            root = ttstream.Stream(open(cd, 'rb').read()).roots[0][0]
        except (ttstream.StreamError, struct.error, ValueError, IndexError, OSError):
            return first
        mask = root['fields'].get('Default Layers')
        if not isinstance(mask, int) or mask <= 0:
            return first
        layers = am.skeletons[0]['layers'] if am.skeletons else []
        if layers:
            keep = set()
            for li, layer in enumerate(layers):
                if mask >> li & 1:
                    keep.update(item['special'] for item in layer['items'])
            out = [x for x in first if specials.index(x) in keep]
            return out or first
        if mask == 1:
            out = [x for x in first if x['name'].lower() == 'face']
            return out or first
        return first

    def _own_roles(self, stem):
        """{material number: texture path or ('material', remap file, material)} from an attachment's own
        definition (LEGO Marvel's wingsarchangel.cd), or {}."""
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd:
            return {}
        try:
            stream = ttstream.Stream(open(cd, 'rb').read())
        except (ttstream.StreamError, struct.error, ValueError, OSError):
            return {}
        roles = {}
        for o in (x for r in stream.roots for x in ttstream.walk(r)):
            g = o['fields']
            if o['class'] == 'Character Texture' and isinstance(g.get('Texture File'), str):
                roles[g.get('Material')] = g['Texture File']
            elif o['class'] == 'Character Material' and g.get('Source Material Resource File'):
                roles.setdefault(g.get('Material'), ('material', g['Source Material Resource File'],
                                                     g.get('Source Material') or ''))
        return roles

    def _attachment_tint(self, att, stem):
        """The attachment's tint times that of a definition of the same name beside its model (LEGO Batman 3's
        skinnedhair_mediumwavy.cd: blonde; its _black and _red copies are other definitions of the same model)."""
        t = tuple(att.get('Tint Colour') or (1.0, 1.0, 1.0))
        cd = _cd_index(self.chars_dir).get(stem.lower() + '.cd')
        if not cd:
            return t
        try:
            root = ttstream.Stream(open(cd, 'rb').read()).roots[0][0]
        except (ttstream.StreamError, struct.error, ValueError, IndexError, OSError):
            return t
        dt = root['fields'].get('Default Tint Colour')
        if not (isinstance(dt, tuple) and len(dt) >= 3):
            return t
        return (t[0] * dt[0], t[1] * dt[1], t[2] * dt[2])

    def _model(self, override, skeleton):
        if override:
            rel = override.replace('\\', '/').split('/')
            if rel and rel[0].lower() == 'chars':
                rel = rel[1:]
            rel[-1] = rel[-1] + '_nxg.ghg' if not rel[-1].lower().endswith('.ghg') else rel[-1]
            hit = _find(self.chars_dir, rel)
            if hit:
                return hit
        for known in SKELETON_MODELS.get(skeleton.lower(), ()):
            hit = _find(self.chars_dir, known)
            if hit:
                return hit
        own = _find(os.path.dirname(self.path), [self.name + '_nxg.ghg'])
        if own:
            return own
        # a definition naming its skeleton after a model of its own (capes, skinned hair): cape_nxg.ghg
        return _model_index(self.chars_dir).get(skeleton.lower() + '_nxg.ghg') if skeleton else None

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
                low = sp['name'].lower()
                # without a choice: the plain variant (not the skeleton, mech, depth-only head, glove or
                # break-apart pieces); big figures' pieces are not called '..._Mesh'
                if want is None and ('skeleton' in low or 'mech' in low or 'robot' in low or low.startswith('zhead')
                                     or 'boxing' in low or 'breakup' in low):
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
            am = self.attached[mesh_index // 100000 - 1]
            return am.mesh, am.material_names, mesh_index % 100000
        return self.mesh, self.base.material_names, mesh_index

    def geometry(self, mesh_index):
        if mesh_index in self._baked:
            return self._baked[mesh_index]
        mesh, _names, i = self._source(mesh_index)
        if mesh.parts[i].get('short'):
            return None
        try:
            pos, nrm, uv, col, tris, skin = mesh.geometry(i)
        except (ValueError, TypeError, struct.error, IndexError):   # a part whose buffers did not resolve
            return None
        if any(not -100.0 < x < 100.0 for v in pos for x in v):
            return None             # read from the wrong buffer (a reference resolved by guess): not drawn
        return pos, nrm or [], [tuple(u[:2]) for u in uv] if uv else [], col, [tuple(t) for t in tris], skin

    def material(self, mesh_index):
        mi = self._mesh_mat.get(mesh_index)
        _mesh, names, _i = self._source(mesh_index)
        name = names[mi] if mi is not None and mi < len(names) else ''
        role = name.split(':')[0].upper()
        role = role[:-5] if role.endswith('_GAME') else role
        path = self.role_textures.get(ROLE.get(role))
        own_roles = self.attachment_roles.get(mesh_index // 100000 - 1) if mesh_index >= 100000 else None
        if own_roles:
            # an attachment's own definition numbers its model's materials from 1 (wingsarchangel.cd: material 1,
            # the feathers, wears MAT_ARCHANGELMETAL; facetex_*.cd: material 1, FaceTex_MAT, the face print)
            key = mi + 1 if mi is not None and mi + 1 in own_roles else ROLE.get(role)
            if key in own_roles:
                path = own_roles[key]
        if path is None and role == 'CAPE_INSIDE':
            path = self.role_textures.get(ROLE['CAPE'])          # no lining of its own: the cape's texture
        tex = -1
        if isinstance(path, tuple):
            tex = self._remap_texture(path)
            path = None
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
        if tex < 0 and mi is not None:
            # the model's own colour map (helmets, hats, big figures)
            owner = self.attached[mesh_index // 100000 - 1] if mesh_index >= 100000 else self.base
            own = owner.colour_texture(mi)
            if own:
                tex = self._texture_index(own)
        colour, vc = (1.0, 1.0, 1.0, 1.0), False
        if tex < 0:
            # untextured pieces (faces, hair) are coloured by their vertex colours, 128 = full brightness,
            # times the attachment's tint (a hair colour)
            geo = self.geometry(mesh_index)
            vc = bool(geo and geo[3])
        elif name.upper().startswith('VERTEXCOLOURS'):
            # a vertex-colour material over a plain white map (LEGO Marvel): the colours are what shows
            geo = self.geometry(mesh_index)
            vc = bool(geo and geo[3])
        elif self._source(mesh_index)[0].version in (46, 47):
            # LEGO Pirates shades textured pieces with their vertex colours too (127 = unchanged; Blackbeard's
            # beard is a grey texture made black this way)
            geo = self.geometry(mesh_index)
            vc = bool(geo and geo[3] and any(c[:3] != (127, 127, 127) for c in geo[3]))
        tint = self.role_tints.get(ROLE.get(role)) if mesh_index < 100000 else None
        if tint is not None and not vc:
            colour = (tint[0], tint[1], tint[2], 1.0)
        if mesh_index >= 100000:
            # an attachment's tint (hair colour, a cape's colour) multiplies its texture or vertex colours
            t = self.tints[mesh_index // 100000 - 1]
            colour = (t[0], t[1], t[2], 1.0)
        # vertex colours are sRGB; LEGO Batman 2 stores them at half strength (128 = full), LEGO Marvel at full
        owner_mesh = self.attached[mesh_index // 100000 - 1].mesh if mesh_index >= 100000 else self.mesh
        return {'index': mi if mi is not None else -1, 'texture': tex, 'colour': colour, 'vertex_colours': vc,
                'vertex_colour_scale': 2.0 if owner_mesh.old else 1.0,
                'occluder': 'OCCLUDE' in name.upper(),
                'double_sided': 'CAPE' in name.upper(),           # capes are seen from both sides
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
            path = index.get((o['fields'].get('CharAnimSet Name') or o['fields'].get('Name') or '').lower())
            if not path:
                continue
            try:
                if os.path.isdir(path):
                    items = [(f,) for f in sorted(os.listdir(path))]
                else:
                    with open(path, 'rb') as f:
                        items = pack.read(f.read())[0]
            except (OSError, ValueError, struct.error):
                continue
            for item in items:
                entry = item[0]
                if not entry.lower().endswith(('.an4', '.an3')):
                    continue
                name = entry[:-4]
                for tail in ('_def_nxg', '_nxg', '_def_ps3', '_ps3'):
                    if name.lower().endswith(tail):
                        name = name[:-len(tail)]
                        break
                if name.lower() in seen:
                    continue
                seen.add(name.lower())
                out.append((name, f'{path}::{entry}', 30.0, None))
        return out

    def _texture_index(self, ref):
        tex = self._tex_index.get(ref)
        if tex is None:
            tex = self._tex_index[ref] = len(self.textures)
            self.textures.append(ref)
        return tex

    def _remap_texture(self, ref):
        _kind, res, mat = ref
        rel = res.replace('\\', '/').split('/')
        if rel[0].lower() == 'chars':
            rel = rel[1:]
        index = _model_index(self.chars_dir)
        path = None
        for ext in ('_nxg.gsc', '_nxg.ghg'):
            path = ((_find(self.chars_dir, rel[:-1] + [rel[-1] + ext]) if len(rel) > 1 else None)
                    or index.get(rel[-1].lower() + ext))
            if path:
                break
        if not path:
            return -1
        try:
            m = model(path)
        except (ValueError, struct.error, IndexError, OSError):
            return -1
        own = m.colour_texture(m.material_by_name(mat))
        return self._texture_index(own) if own else -1

    def has_texture(self, index):
        return 0 <= index < len(self.textures)

    def texture_bytes(self, index):
        ref = self.textures[index]
        if isinstance(ref, tuple) and ref[0] == 'layered':
            return _layered_png(*ref[1:])
        if isinstance(ref, tuple):                     # (file, start, end) inside a model or texture bundle
            with open(ref[0], 'rb') as f:
                f.seek(ref[1])
                return f.read(ref[2] - ref[1])
        with open(ref, 'rb') as f:
            return f.read()

    def texture_format(self, index):
        if not self.has_texture(index):
            return ''
        h = self.texture_bytes(index)[:128]
        return h[84:88].decode('latin1') if h[:4] == b'DDS ' else ''


def _layered_png(file, colour, mask):
    """A layered metal material as one picture (see Model._layered): its colour, darkened along the mask's lines
    (blue channel), as PNG bytes."""
    import io
    import numpy as np
    from PIL import Image
    with open(file, 'rb') as f:
        f.seek(mask[0])
        m = np.asarray(Image.open(io.BytesIO(f.read(mask[1] - mask[0]))).convert('RGB'), dtype=np.float32)
    lines = m[:, :, 2:3] / 255.0
    rgb = np.asarray(colour, dtype=np.float32).reshape(1, 1, 3) * (1.0 - 0.55 * lines)
    out = io.BytesIO()
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), 'RGB').save(out, 'PNG')
    return out.getvalue()
