"""The three games' characters as a catalogue of parts.

A game is found from its extracted folder (the one holding chars/ or CHARS/). Every full-detail character
model (`*_pc.ghg`, not `*_lr_pc.ghg`) is listed; a humanoid (one that has the common head, hands and legs)
can be a body base, and every character gives parts:
- a rigid piece is one slot by its bone and layer name: head, hair, headgear, face, held (hands and weapon
  bones), back (spine and chest), or accessory;
- a skinned layer named for a face, neck, cape or hair is that slot whole; any other skinned layer (the body)
  is cut into torso, arms, hands, hips, legs, head and cape by each triangle's strongest bone.
Break-apart, death and lower-detail layers are left out. A character's own look is the layers its .txt
shows (layers_high), else every layer left.
"""
import os
import re
from collections import OrderedDict

from .formats import classic
from . import skeleton

GAMES = OrderedDict((
    ('lb1', 'LEGO Batman: The Videogame'),
    ('indy', 'LEGO Indiana Jones: The Original Adventures'),
    ('tcs', 'LEGO Star Wars: The Complete Saga'),
    ('lb2', 'LEGO Batman 2: DC Super Heroes'),
    ('lmsh', 'LEGO Marvel Super Heroes'),
))
# games that share file formats form one era (a mode of the program)
ERAS = OrderedDict((
    ('classic', {'title': 'Classic (2007 to 2008)', 'games': ['lb1', 'indy', 'tcs']}),
    ('nxg', {'title': 'Super-hero era (2012 to 2013)', 'games': ['lb2', 'lmsh']}),
))
MODERN = {'lb2', 'lmsh'}


def era_of(game_id):
    return next((e for e, v in ERAS.items() if game_id in v['games']), 'classic')
SLOTS = ['head', 'face', 'hair', 'headgear', 'neck', 'torso', 'arms', 'hands', 'hips', 'legs', 'cape',
         'back', 'held', 'accessory']
SKIP_LAYER = re.compile(r'breakup|blowup|_dead|dead_|^tt\d+_dead|(^|_)(low|lo|med|medium|vlo|verylo)(_?res)?(_|$)|'
                        r'_(medium|low)_', re.I)
# layers named only for their detail level or the whole body hold the plain figure (Star Wars: TT2_HiRes_Nextgen)
GENERIC_LAYER = re.compile(r'alwaysdrawn|hi_?res|high|special|^tt\d+_body|default|^tt\d+_nextgen', re.I)
HEADGEAR = ('hat', 'helmet', 'helm', 'mask', 'cowl', 'hood', 'cap', 'crown', 'ears', 'visor', 'goggle', 'bandana',
            'turban', 'fedora', 'tiara', 'bow')


def find_chars_dir(root):
    hit = classic.listing(root).get('chars')
    return os.path.join(root, hit) if hit else None


def guess_game(root):
    """'lb1', 'indy' or 'tcs' from a folder's contents (or None)."""
    chars = find_chars_dir(root)
    if not chars:
        return None
    names = classic.listing(chars)
    if 'batman' in names or 'joker' in names:
        return 'lb1'
    if 'indianajones' in names or 'indy' in names:
        return 'indy'
    if 'lukeskywalker' in names or 'darthvader' in names or 'chewbacca' in names:
        return 'tcs'
    figs = classic.listing(os.path.join(chars, names['minifigs'])) if 'minifigs' in names else {}
    if 'ironman' in figs or 'spiderman' in figs or 'hulk' in figs:
        return 'lmsh'
    if 'batman' in figs:
        return 'lb2'
    return None


class Game:
    def __init__(self, game_id, root):
        self.id = game_id
        self.root = root
        self.title = GAMES.get(game_id, game_id)
        self.chars_dir = find_chars_dir(root)
        if not self.chars_dir:
            raise classic.FormatError(f'{root}: no chars folder')
        self._list = None
        self._cache = OrderedDict()

    def characters(self):
        """[{id, name, folder, model}] for every full-detail model, sorted by name."""
        if self._list is None and self.id in MODERN:
            # newer era: a character is a definition (.cd) in chars/minifigs/<name>/ (or another kind's folder)
            out = []
            for kind in sorted(os.listdir(self.chars_dir), key=str.lower):
                kdir = os.path.join(self.chars_dir, kind)
                if not os.path.isdir(kdir) or kind.lower() not in ('minifigs', 'bigfigs', 'creatures'):
                    continue
                for folder in sorted(os.listdir(kdir), key=str.lower):
                    path = os.path.join(kdir, folder)
                    if not os.path.isdir(path) or folder.lower() == 'super_characters':
                        continue
                    for f in sorted(os.listdir(path), key=str.lower):
                        if f.lower().endswith('.cd'):
                            name = f[:-3]
                            out.append({'id': f'{self.id}:{folder}/{name}'.lower(), 'name': name, 'folder': folder,
                                        'model': os.path.join(path, f)})
            self._list = out
        if self._list is None:
            out = []
            for folder in sorted(os.listdir(self.chars_dir), key=str.lower):
                path = os.path.join(self.chars_dir, folder)
                if not os.path.isdir(path):
                    continue
                for f in sorted(os.listdir(path), key=str.lower):
                    low = f.lower()
                    if low.endswith('_pc.ghg') and not low.endswith('_lr_pc.ghg'):
                        name = f[:-7]
                        out.append({'id': f'{self.id}:{folder}/{name}'.lower(), 'name': name, 'folder': folder,
                                    'model': os.path.join(path, f)})
            self._list = out
        return self._list

    def entry(self, char_id):
        for e in self.characters():
            if e['id'] == char_id:
                return e
        raise KeyError(char_id)

    def character(self, char_id):
        """Character (cached; the last 24 used)."""
        if char_id in self._cache:
            self._cache.move_to_end(char_id)
            return self._cache[char_id]
        entry = self.entry(char_id)
        if self.id in MODERN:
            from .formats import modern
            char = modern.Character(entry['model'], self.chars_dir)
        else:
            char = classic.Character(entry['model'])
        char.id = char_id
        char.common = skeleton.common_names(char.bones)
        self._cache[char_id] = char
        while len(self._cache) > 24:
            self._cache.popitem(last=False)
        return char


def is_humanoid(char):
    have = set(char.common)
    return {'head', 'l_hand', 'r_hand', 'l_thigh', 'r_thigh', 'pelvis'} <= have


def scheme(char):
    """'A' (Hips, LeftUpLeg ...) or 'B' (upperTorso, leftLeg ...) or '' when neither."""
    names = {b['name'].lower() for b in char.bones}
    if 'uppertorso' in names:
        return 'B'
    if 'hips' in names:
        return 'A'
    return ''


def rigid_slot(layer_name, common_bone):
    name = layer_name.lower()
    region = skeleton.region_of(common_bone)
    if region == 'head':
        if 'hair' in name:
            return 'hair'
        if any(k in name for k in HEADGEAR):
            return 'headgear'
        if 'face' in name:
            return 'face'
        if 'head' in name or GENERIC_LAYER.search(name):
            return 'head'
        return 'headgear'
    if region == 'hands':
        return 'held'
    if region == 'hips':
        return 'hips'
    if common_bone in ('spine', 'chest'):
        return 'torso' if any(k in name for k in ('body', 'torso', 'chest')) or GENERIC_LAYER.search(name) else 'back'
    return 'accessory'


def skinned_slot(layer_name):
    """The slot of a whole skinned layer, or None when the layer is a body to be cut by region."""
    name = layer_name.lower()
    for key, slot in (('face', 'face'), ('neck', 'neck'), ('cape', 'cape'), ('cloak', 'cape'), ('hair', 'hair')):
        if key in name:
            return slot
    return None


MODERN_SLOTS = (('armour', 'torso'), ('hand', 'hands'), ('arm', 'arms'), ('head', 'head'), ('neck', 'neck'), ('hips', 'hips'),
                ('leg', 'legs'), ('cape', 'cape'), ('hair', 'hair'), ('hat', 'headgear'), ('glasses', 'headgear'),
                ('face', 'face'), ('backpack', 'back'), ('body', 'torso'), ('suit', 'torso'), ('armour', 'torso'))


def modern_slot(layer_name):
    """Newer era: every body layer is one part already (TT0_LeftHand, TT2_Body ...)."""
    name = re.sub(r'^tt\d+_', '', layer_name, flags=re.I).lower()
    for key, slot in MODERN_SLOTS:
        if key in name:
            return slot
    return 'accessory'


def layer_label(name):
    """'TT12_Body_Blue_NG' -> 'Body Blue'."""
    s = re.sub(r'^tt\d+_', '', name, flags=re.I)
    s = re.sub(r'_(ng|nextgen)$', '', s, flags=re.I)
    return s.replace('_', ' ').strip() or name


def triangle_regions(char, geo):
    """Region of each triangle of a skinned mesh: the region of the bone with the most weight on it."""
    weights = geo[5] or []
    out = []
    for tri in geo[4]:
        total = {}
        for v in tri:
            for b, w in (weights[v] if v < len(weights) else ()):
                total[b] = total.get(b, 0.0) + w
        if not total:
            out.append('other')
            continue
        best = max(total.items(), key=lambda kv: kv[1])[0]
        out.append(skeleton.region_of(char.common[best]) if best < len(char.common) else 'other')
    return out


def parts(char):
    """Parts of one character: [{id, slot, label, layer, kind, bone, region, default, meshes}]."""
    if hasattr(char, '_parts'):
        return char._parts
    if hasattr(char, 'layer_choice'):
        # newer era: the body layers 0 to 10 and the ones the definition picks; break-ups and suits are left out
        dl = char.default_layers
        shown = {L['index'] for L in char.layers
                 if (L['name'].startswith('TT_') or (L['index'] in dl if dl is not None else
                                                       (L['chosen'] or L['index'] <= 10)))
                 and not SKIP_LAYER.search(L['name'])}
    else:
        shown = classic.chosen_layers(classic.character_txt(char.path))
    out = []
    for layer in char.layers:
        if SKIP_LAYER.search(layer['name']) and (shown is None or layer['index'] not in shown):
            continue
        default = (layer['index'] in shown) if shown is not None else True
        label = layer_label(layer['name'])
        for p_i, part in enumerate(layer['parts']):
            if part['kind'] == 'rigid':
                common = char.common[part['bone']]
                slot = rigid_slot(layer['name'], common)
                same_bone = sum(1 for q in layer['parts'][:p_i] if q['kind'] == 'rigid' and q['bone'] == part['bone'])
                pid = f'{char.id}|{layer["index"]}|r{part["bone"]}' + (f'.{same_bone}' if same_bone else '')
                out.append({'id': pid, 'slot': slot, 'label': label,
                            'layer': layer['index'], 'kind': 'rigid', 'bone': common, 'part': p_i,
                            'default': default})
                continue
            whole = skinned_slot(layer['name']) or (modern_slot(layer['name']) if hasattr(char, 'layer_choice') else None)
            if whole:
                out.append({'id': f'{char.id}|{layer["index"]}|s{p_i}', 'slot': whole, 'label': label,
                            'layer': layer['index'], 'kind': 'skinned', 'region': None, 'part': p_i,
                            'default': default})
                continue
            regions = set()
            for mi in part['meshes']:
                geo = char.geometry(mi)
                if geo is not None:
                    regions.update(triangle_regions(char, geo))
            for region in sorted(regions):
                # a skinned piece on the head is the face print (the head itself is a rigid piece)
                slot = 'face' if region == 'head' else region if region in SLOTS else 'accessory'
                out.append({'id': f'{char.id}|{layer["index"]}|s{p_i}|{region}', 'slot': slot,
                            'label': f'{label} ({region})' if slot != region or region == 'head' else label,
                            'layer': layer['index'], 'kind': 'skinned', 'region': region, 'part': p_i,
                            'default': default})
    char._parts = out
    return out
