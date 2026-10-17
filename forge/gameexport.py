"""Put a figure into a game: replace its body character, or add it as a new character.

Delivery:
- 'patch': the game's GAME.DAT is changed in place (new data and a new index appended). The first time,
  the archive is copied to GAME.DAT.figureforge-backup beside it; restore() puts that copy back.
- 'mod': the files go to a folder, laid out as the game's own folders, with a README. The game reads loose
  files only when its GAME.DAT has been unpacked and moved aside (measured in LEGO Batman).
"""
import os
import re
import shutil
import struct

from . import gamewrite, newchar
from .formats import classic, datpatch

NXG_EXPORT = {'lb2', 'lmsh', 'pirates', 'lb3'}             # super-hero era games the figure can be written into
INSTALL_GUESSES = {
    'lb2': ('LEGO Batman 2',),
    'lmsh': ('LEGO Marvel Super Heroes',),
    'lb3': ('LEGO Batman 3 Beyond Gotham',),
    'pirates': ('LEGO\u00ae Pirates of the Caribbean The Video Game', 'LEGO Pirates of the Caribbean The Video Game'),
    'lb1': ('Lego Batman',),
    'indy': ('LEGO Indiana Jones The Original Adventures',),
    'tcs': ('Lego Star Wars Saga',),
}
STEAM_ROOTS = (r'C:\Program Files (x86)\Steam\steamapps\common', r'C:\Program Files\Steam\steamapps\common')
BACKUP = '.figureforge-backup'
BS = '\\'


class ExportError(ValueError):
    pass


def find_install(game_id, cfg):
    """The game's install folder (the one holding GAME.DAT): from Settings, else the usual Steam folders."""
    given = (cfg.get('installs') or {}).get(game_id)
    candidates = [given] if given else []
    for root in STEAM_ROOTS:
        candidates += [os.path.join(root, n) for n in INSTALL_GUESSES.get(game_id, ())]
    for c in candidates:
        if c and os.path.isfile(os.path.join(c, 'GAME.DAT')):
            return c
    return None


def archive_path(game_root, path):
    """A file's path inside the archive: its path below the extracted game folder, with backslashes."""
    return os.path.relpath(os.path.abspath(path), os.path.abspath(game_root)).replace('/', '\\')


def files_for(game, base_char, glb, mode, name='', report=print):
    """{archive path: bytes} for the figure. game: catalog.Game; base_char: classic.Character of the body
    (modern.Character in the super-hero era)."""
    if game.id in NXG_EXPORT:
        return nxg_files_for(game, base_char, glb, mode, name, report)
    txt = classic.character_txt(base_char.path)
    model = gamewrite.write_figure(open(base_char.path, 'rb').read(), glb, game.id, txt, report=report)
    if mode == 'replace':
        rel = archive_path(game.root, base_char.path)
        files = {rel: model}
        lr = re.sub(r'(?i)_pc\.ghg$', lambda m: '_LR_PC.GHG' if m.group(0).isupper() else '_lr_pc.ghg', rel)
        if os.path.exists(os.path.join(game.root, lr)):
            files[lr] = model
        return files
    if mode == 'new':
        name = (name or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{2,24}', name):
            raise ExportError('give the new character a name of 2 to 24 letters, digits or _')
        if name in {e['name'].lower() for e in game.characters()}:
            raise ExportError(f'{name} is already a character of this game')
        return newchar.build(game.root, base_char.path, name, model, upper=game.id == 'tcs')
    raise ExportError('mode must be replace or new')


def nxg_files_for(game, base_char, glb, mode, name='', report=print):
    """LEGO Batman 2, LEGO Marvel Super Heroes, LEGO Pirates and LEGO Batman 3: the figure as a model of its own beside
    the shared one, and the body's definition pointed at it (see nxgwrite), replacing the body or as a new
    character. LEGO Batman 3 also gets the '.res' lists the PC game loads with the model and the definition."""
    from . import nxgwrite
    if mode not in ('replace', 'new'):
        raise ExportError('mode must be replace or new')
    new = None
    if mode == 'new':
        new = (name or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{2,24}', new):
            raise ExportError('give the new character a name of 2 to 24 letters, digits or _')
        if new in {e['name'].lower() for e in game.characters()}:
            raise ExportError(f'{new} is already a character of this game')
    writer = nxgwrite.NxgWriter(base_char)
    base = new or re.sub(r'[^a-z0-9_]', '_', base_char.name.lower())
    # the new model goes beside the shared one: the games only load models from the folders chars\chardata.txt
    # validates (Super_Minifig is one; a character's own folder is not)
    folder = os.path.dirname(archive_path(game.root, writer.path))
    rel = folder.split(BS)[1:]                                      # below 'chars'
    if writer.companion is None:
        # LEGO Batman 2: any name
        stem = base[:24] + '_ff'
        out = writer.write(glb, stem, report=report)
        files = {f'{folder}{BS}{stem}_nxg.ghg': out['ghg']}
    else:
        # LEGO Marvel and LEGO Batman 3: the name has the shared model's length (its resource header names the
        # companions by it), with a copy of the shared model's shaders
        size = len(writer.stem)
        stem = ('ff' + base)[:size].ljust(size, '_')
        out = writer.write(glb, stem, report=report, new_stem=stem)
        with open(writer.path[:-len('.ghg')] + '.pc_shaders', 'rb') as f:
            shaders = f.read()
        pf = writer.platform
        files = {f'{folder}{BS}{stem}{pf}.ghg': out['ghg'],
                 f'{folder}{BS}{stem}{pf}.nxg_textures': out['nxg_textures'],
                 f'{folder}{BS}{stem}{pf}.pc_shaders': shaders}
    lb3 = game.id == 'lb3'
    ref = BS.join(rel + [stem.upper()])
    if lb3:
        ref = 'Chars' + BS + ref                    # LEGO Batman 3 names models from 'Chars' (arkillo.cd)
    cd = list_model(nxgwrite.definition(base_char, ref), folder, stem, 'lb3' if lb3 else 'lmsh')
    model_name = f'{stem}{writer.platform}.ghg'
    if new is None:
        cd_rel = archive_path(game.root, base_char.path)
        files[cd_rel] = cd
        if lb3:
            files.update(lb3_resources(game, files, [f'{folder}{BS}{model_name}', cd_rel]))
        files.update(anim_projects(game, files, cd_rel))
        report(f'{base_char.name} now wears the figure (model {model_name}).')
        return files
    files.update(nxg_new_character(game, base_char, new, cd))
    if lb3:
        files.update(lb3_resources(game, files, [f'{folder}{BS}{model_name}',
                                                 next(k for k in files if k.lower().endswith(f'{BS}{new}.cd'))]))
    files.update(anim_projects(game, files, archive_path(game.root, base_char.path)))
    report(f'{new} joins the character list (from {base_char.name}, model {model_name}).')
    return files


def anim_projects(game, files, base_rel):
    """LEGO Batman 2 and LEGO Pirates read every definition (.cd, .cpd, .as) from chars\animprojects.pac, a
    pack of copies of them, not from the files themselves (found in the running game: a patched batman.cd
    alone changed nothing). Each definition among files goes into the pack too: in place of its copy, or a
    new one at the end of the definitions with the base definition's two numbers."""
    from .formats import pack
    p = os.path.join(game.root, 'chars', 'animprojects.pac')
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as f:
        entries, stamp, tail = pack.read(f.read())
    at = {n.lower(): i for i, (n, *_r) in enumerate(entries)}
    like = base_rel.lower().split(BS, 1)[1]
    extra = entries[at[like]][3] if like in at else (0, 0, 0)
    for rel, blob in files.items():
        low = rel.lower()
        if not low.startswith('chars' + BS) or not low.endswith(('.cd', '.cpd', '.as')):
            continue
        name = rel.split(BS, 1)[1]
        if low.split(BS, 1)[1] in at:
            n, k, _old, x = entries[at[low.split(BS, 1)[1]]]
            entries[at[low.split(BS, 1)[1]]] = (n, k, blob, x)
        else:
            # the pack keeps its .cd files first: a new one goes after the last of them
            last = max(i for i, (n, *_r) in enumerate(entries) if n.lower().endswith('.cd'))
            entries.insert(last + 1, (name, 4, blob, extra))
            at = {n.lower(): i for i, (n, *_r) in enumerate(entries)}
    return {f'chars{BS}animprojects.pac': pack.write(entries, stamp, tail, align=4, gap=2)}


def lb3_resources(game, files, which):
    """LEGO Batman 3: '<file>.res' for each file named (archive paths among files), worked out from its own
    resource header and the headers of what it lists (the new files first, then the game's), as the build
    writes them (hser.pc_resources); the bookkeeping is taken from the base file's '.res'."""
    from .formats import hser
    mine = {k.replace(BS, '/').lower(): v for k, v in files.items()}
    cache = {}

    def own(path):
        if path not in cache:
            cache[path] = None
            data = mine.get(path.lower())
            if data is None:
                p = os.path.join(game.root, *path.split('/'))
                if os.path.exists(p):
                    with open(p, 'rb') as f:
                        data = f.read()
            if data is not None and hser.has_header(data):
                cache[path] = hser.entries(hser.read(data)[0])
        return cache[path]

    out = {}
    for rel in which:
        records = hser.pc_resources(own(rel.replace(BS, '/').lower()) or [], own)
        like = _like_res(game, rel)
        out[rel + '.res'] = hser.build(records, hser.read(like)[0])
    return out


def _like_res(game, rel):
    """The '.res' whose bookkeeping a new one copies: the file's own, else one of the same kind beside the shared
    minifigure model or definition."""
    p = os.path.join(game.root, rel + '.res')
    if os.path.exists(p):
        return open(p, 'rb').read()
    folder = os.path.join(game.root, 'chars', 'super_character', 'super_minifig')
    name = 'super_minifig_dx11.ghg.res' if rel.lower().endswith('.ghg') else 'super_minifig.cd.res'
    with open(os.path.join(folder, name), 'rb') as f:
        return f.read()


def list_model(cd, folder, stem, game_id):
    """A definition with a resource header (LEGO Marvel) lists every model it names, one per platform, as the
    games' build does (abomb.cd lists abomb_nxg.ghg and abomb_dx11.ghg): the figure's model is added to the
    list. A definition without a header (LEGO Batman 2, LEGO Pirates) is returned as it is."""
    from .formats import hser
    if not hser.has_header(cd):
        return cd
    h, end = hser.read(cd)
    if not h['tree']:
        return cd
    where = folder.replace(BS, '/').lower()
    for path, type_, platforms, usage, param in hser.model_paths(where, stem, game_id):
        hser.add_file(h, path, type_, platforms, usage, param)
    return hser.write(h) + cd[end:]


def nxg_new_character(game, base_char, new, cd):
    r"""LEGO Batman 2 / LEGO Marvel / LEGO Pirates: a character is found by the name of its text file, wherever its folder is
    (collection.txt says `collect "IronManClassic"`, the file is minifigs\ironman\ironmanclassic.txt), so a
    new one is chars\minifigs\<new>\<new>.cd and .txt (the base's text: its abilities, sizes and name), a
    `collect` line (no condition: there from the start), and both text files in chars\charstxt.fpk, which the
    game reads its character texts from."""
    from .formats import pack
    chars = os.path.join(game.root, 'chars')
    # LEGO Batman 3 keeps them in chars\minifig (the singular)
    kind = 'minifig' if game.id == 'lb3' else 'minifigs'
    folder = BS.join(['chars', kind, new])
    out = {f'{folder}{BS}{new}.cd': cd}
    txt_path = base_char.path[:-3] + '.txt'
    txt = open(txt_path, 'rb').read() if os.path.exists(txt_path) else b''
    out[f'{folder}{BS}{new}.txt'] = txt
    with open(os.path.join(chars, 'collection.txt'), 'rb') as f:
        coll = f.read().rstrip(b'\0')
    coll = coll.rstrip(b'\r\n') + f'\r\ncollect "{new}"\r\n'.encode('latin1')
    out[f'chars{BS}collection.txt'] = coll
    fpk = os.path.join(chars, 'charstxt.fpk')
    if os.path.exists(fpk):
        with open(fpk, 'rb') as f:
            data = f.read()
        out[f'chars{BS}charstxt.fpk'] = pack.replace(data, {f'chars{BS}collection.txt': coll},
                                                     add=[(f'{folder}{BS}{new}.txt', 1, txt)])
    return out


def archives(install):
    """The game's archives: GAME.DAT first, then GAME0.DAT, GAME1.DAT ... (the newer games have several)."""
    names = [n for n in os.listdir(install) if re.fullmatch(r'(?i)game\d*\.dat', n)]
    names.sort(key=lambda n: (n.upper() != 'GAME.DAT', len(n), n.upper()))
    return [os.path.join(install, n) for n in names]


def patch(install, files, game_root=None):
    """Each file goes into the archive that already holds it; new files into GAME.DAT. Every archive changed is
    copied to '<archive>.figureforge-backup' first (once). game_root (the extracted game the files were made
    from): shared lists an earlier export already changed in the archive (packs, collection.txt) are merged
    rather than replaced, so a second export keeps the first one's entries."""
    dats = archives(install)
    if not dats:
        raise ExportError('no GAME.DAT in the game folder')
    if game_root:
        files = merge_shared(install, files, game_root)
    groups = {}
    remaining = dict(files)
    for dat in dats:
        if not remaining:
            break
        held = datpatch.paths(dat) if len(dats) > 1 else {}
        for path in list(remaining):
            if len(dats) == 1 or path.lower().replace('/', BS) in held:
                groups.setdefault(dat, {})[path] = remaining.pop(path)
    if remaining:
        groups.setdefault(dats[0], {}).update(remaining)
    replaced = added = 0
    for dat, part in groups.items():
        backup = dat + BACKUP
        if not os.path.exists(backup):
            shutil.copy2(dat, backup)
        r, a = datpatch.write_files(dat, part)
        replaced += len(r)
        added += len(a)
    return {'archive': ', '.join(os.path.basename(d) for d in groups) if len(groups) > 1 else dats[0],
            'backup': dats[0] + BACKUP, 'replaced': replaced, 'added': added}


def _read_installed(install, rels):
    """{lower-case archive path: bytes} of the given paths as the installed archives now hold them (the first
    archive holding a path wins, as patch() writes there)."""
    from .formats.dat import DatArchive
    want, got = {r.lower().replace('/', BS) for r in rels}, {}
    for dat in archives(install):
        arc = DatArchive(dat)
        try:
            for e in arc.entries:
                k = e.path.lower()
                if k in want and k not in got:
                    got[k] = arc.read(e)
        finally:
            arc.f.close()
    return got


def merge_shared(install, files, game_root):
    """files with each shared list (a pack, or a '.txt' list) that the archive already holds in a changed form
    (an earlier export) merged: the entries this export changed or added against the game's own copy go into
    the archive's current copy. Other files are returned as they are."""
    from .formats import pack
    shared = [k for k in files if k.lower().endswith(('.pac', '.fpk', '.pak', 'collection.txt'))]
    if not shared:
        return files
    now = _read_installed(install, shared)
    out = dict(files)
    for k in shared:
        cur = now.get(k.lower().replace('/', BS))
        src = os.path.join(game_root, *k.replace('/', BS).split(BS))
        if cur is None or not os.path.exists(src):
            continue
        with open(src, 'rb') as f:
            orig = f.read()
        if cur == orig:
            continue                                    # untouched so far: this export's copy stands
        mine = files[k]
        if mine[:4] == struct.pack('<I', pack.MAGIC) and cur[:4] == mine[:4]:
            out[k] = _merge_pack(cur, orig, mine)
        elif k.lower().endswith('.txt'):
            had = set(orig.replace(b'\r', b'').split(b'\n'))
            have = set(cur.replace(b'\r', b'').split(b'\n'))
            extra = [ln for ln in mine.replace(b'\r', b'').split(b'\n') if ln.strip() and ln not in had
                     and ln not in have]
            out[k] = cur.rstrip(b'\0').rstrip(b'\r\n') + b''.join(b'\r\n' + ln for ln in extra) + b'\r\n'
    return out


def _merge_pack(cur, orig, mine):
    """The pack cur with the entries mine changed or added against orig: changed ones replaced in place, new
    ones placed after the entry they follow in mine (or at the end)."""
    from .formats import pack
    c_ents, stamp, tail = pack.read(cur)
    o = {n.lower(): b for n, _k, b, _x in pack.read(orig)[0]}
    m_ents = pack.read(mine)[0]
    at = {n.lower(): i for i, (n, *_r) in enumerate(c_ents)}
    prev = None
    for n, k, b, x in m_ents:
        low = n.lower()
        if low in o and o[low] == b:
            prev = low
            continue
        if low in at:
            cn, ck, _cb, cx = c_ents[at[low]]
            c_ents[at[low]] = (cn, ck, b, cx)
        else:
            c_ents.insert(at[prev] + 1 if prev in at else len(c_ents), (n, k, b, x))
            at = {nn.lower(): i for i, (nn, *_r) in enumerate(c_ents)}
        prev = low
    layout = pack._layout(cur, pack.read(cur)[0], stamp, tail)
    return pack.write(c_ents, stamp, tail, **layout)


def restore(install):
    done = []
    for dat in archives(install):
        if os.path.exists(dat + BACKUP):
            shutil.copy2(dat + BACKUP, dat)
            done.append(dat)
    if not done:
        raise ExportError("there is no Figure Forge backup of this game's archives")
    return {'archive': ', '.join(os.path.basename(d) for d in done) if len(done) > 1 else done[0],
            'restored_from': done[0] + BACKUP}


def mod_folder(folder, files, title, game_title):
    os.makedirs(folder, exist_ok=True)
    for rel, data in files.items():
        path = os.path.join(folder, *rel.split('\\'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
    with open(os.path.join(folder, 'README.txt'), 'w', encoding='utf-8') as f:
        f.write(f'{title}: a Figure Forge mod for {game_title}.\n\n'
                'The game reads these loose files only when its archives (GAME.DAT, and GAME0.DAT ... in the\n'
                'newer games) are out of the way:\n'
                '1. Back up the game folder.\n'
                '2. Unpack the archives into the game folder with a TT archive extractor, then rename them.\n'
                '3. Copy the contents of this folder into the game folder, replacing files.\n\n'
                'Or let Figure Forge patch the archive directly (Export > Into the game > Patch).\n'
                'This mod holds files made from your own copy of the game: do not share it publicly.\n')
    return {'folder': folder, 'files': len(files)}
