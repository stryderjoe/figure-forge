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

import numpy as np

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
        files.update(portrait_files(game, glb, txt, classic.model_stem(base_char.path), None, report))
        return files
    if mode == 'new':
        name = (name or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9_]{2,24}', name):
            raise ExportError('give the new character a name of 2 to 24 letters, digits or _')
        if name in {e['name'].lower() for e in game.characters()}:
            raise ExportError(f'{name} is already a character of this game')
        files = newchar.build(game.root, base_char.path, name, model, upper=game.id == 'tcs')
        files.update(portrait_files(game, glb, txt, classic.model_stem(base_char.path), name, report))
        return files
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
        files.update(portrait_files(game, glb, base_char.path[:-3] + '.txt', os.path.basename(base_char.path)[:-3],
                                    None, report))
        report(f'{base_char.name} now wears the figure (model {model_name}).')
        return files
    files.update(nxg_new_character(game, base_char, new, cd))
    files.update(project_files(game, base_char, new))
    if lb3:
        files.update(lb3_resources(game, files, [f'{folder}{BS}{model_name}',
                                                 next(k for k in files if k.lower().endswith(f'{BS}{new}.cd'))]))
    files.update(anim_projects(game, files, archive_path(game.root, base_char.path)))
    files.update(portrait_files(game, glb, base_char.path[:-3] + '.txt', os.path.basename(base_char.path)[:-3],
                                new, report, files.get(f'chars{BS}collection.txt')))
    report(f'{new} joins the character list (from {base_char.name}, model {model_name}).')
    return files


def portrait_files(game, glb, txt_path, stem, new, report=print, collection=None):
    """The game's portrait files with the figure's portrait: in place of the body's (new None) or as the new
    character's own. A portrait that cannot be made is reported, never a reason to stop the export."""
    from . import gameportrait, portrait
    try:
        txt = open(txt_path, 'rb').read() if txt_path and os.path.exists(txt_path) else b''
        out = gameportrait.files(game, txt, stem, portrait.render(glb, 256), new, report, collection)
    except (ValueError, OSError) as e:
        report(f'The portrait was not changed ({e}).')
        return {}
    if out:
        report('The portrait shows the figure.')
    return out


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
            if like in at:
                # the base's entry spelled as the pack spells it (LEGO Pirates: MiniFigs\WillTurner\WILLTURNER.cd)
                name = cased_like(entries[at[like]][0], name)
            # the pack keeps its .cd files first: a new one goes after the last of them
            last = max(i for i, (n, *_r) in enumerate(entries) if n.lower().endswith('.cd'))
            entries.insert(last + 1, (name, 4, blob, extra))
            at = {n.lower(): i for i, (n, *_r) in enumerate(entries)}
    return {f'chars{BS}animprojects.pac': pack.write(entries, stamp, tail, align=4, gap=2)}


def cased_like(model, path):
    r"""path (a new character's file: ...\<new>\<new>.<ext>) spelled like model (its base's entry in a pack):
    every folder above the character's own as model has it."""
    m, p = model.split(BS), path.split(BS)
    if len(m) != len(p):
        return path
    return BS.join(m[:-2] + p[-2:])


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
    from .gameportrait import NO_NEW_PORTRAIT, renamed_icon
    if game.id not in NO_NEW_PORTRAIT:
        txt = renamed_icon(txt, new)
    out[f'{folder}{BS}{new}.txt'] = txt
    with open(os.path.join(chars, 'collection.txt'), 'rb') as f:
        coll = f.read().rstrip(b'\0')
    coll = coll.rstrip(b'\r\n') + f'\r\ncollect "{new}"\r\n'.encode('latin1')
    out[f'chars{BS}collection.txt'] = coll
    changes = {f'chars{BS}collection.txt': coll}
    # chars\hgopathnames.txt names every character's model (a character missing from it is left out of a level
    # that names it: found in the running LEGO Batman 2, 2026-10-17): the base's line under the new name
    hgo = os.path.join(chars, 'hgopathnames.txt')
    if os.path.exists(hgo):
        with open(hgo, 'rb') as f:
            names = f.read().rstrip(b'\0')
        base = os.path.basename(base_char.path)[:-3].lower().encode('latin1')
        lines = names.replace(b'\r', b'').split(b'\n')
        like = next((ln for ln in lines if ln.split(b' ', 1)[0].lower() == base), None) or next(
            ln for ln in lines if b'super_minifig.hgo' in ln.lower())
        names = names.rstrip(b'\r\n') + b'\r\n' + new.encode('latin1') + b' ' + like.split(b' ', 1)[1] + b'\r\n'
        out[f'chars{BS}hgopathnames.txt'] = names
        changes[f'chars{BS}hgopathnames.txt'] = names
    fpk = os.path.join(chars, 'charstxt.fpk')
    if os.path.exists(fpk):
        with open(fpk, 'rb') as f:
            data = f.read()
        name = f'{folder}{BS}{new}.txt'
        base_txt = archive_path(game.root, txt_path).lower()
        like = next((n for n, *_r in pack.read(data)[0] if n.lower() == base_txt), None)
        if like:
            name = cased_like(like, name)
        out[f'chars{BS}charstxt.fpk'] = pack.replace(data, changes, add=[(name, 1, txt)])
    return out


def project_add(data, base, new):
    """A character project (chars\\*.apj in LEGO Batman 2 and LEGO Pirates, *.cpj in LEGO Marvel and LEGO Batman 3)
    with the new character's definition listed right after its base's (a copy of the base's entry: its folder and name
    changed), or None when the project does not list the base. The games load only the definitions their projects list (found in the running
    LEGO Batman 2, 2026-10-17: a new character in every other list was still left out of the level)."""
    from .formats import ttstream
    st = ttstream.Stream(data)
    for root in (o for lst in st.roots for o in lst):
        defs = root['components'].get('Char Def Files', [])
        names = [_editor_file(o) for o in defs]
        if any(n and n[1].lower() == new.lower() for n in names):
            return data
        for o, n in zip(defs, names):
            if n and n[1].lower() == base.lower():
                folder = n[0].rstrip('\\').rsplit('\\', 1)
                path = (folder[0] + '\\' if len(folder) > 1 else '') + new + '\\'
                raw = ttstream.copy_object(st, o, {n[0]: path, n[1]: new})
                return ttstream.edit(st, adds=[(o, raw)])
    return None


def _editor_file(obj):
    """(FilePath, FileName) of a project entry (held inline through its base classes), or None."""
    f = obj['fields']
    while 'FilePath' not in f:
        inner = next((v for v in f.values() if isinstance(v, dict) and 'fields' in v), None)
        if inner is None:
            return None
        f = inner['fields']
    if not isinstance(f.get('FilePath'), str) or not isinstance(f.get('FileName'), str):
        return None
    return f['FilePath'], f['FileName']


def project_files(game, base_char, new):
    """{archive path: bytes} of the character projects that list the base, each listing the new character too."""
    chars = os.path.join(game.root, 'chars')
    out = {}
    base = os.path.basename(base_char.path)[:-3]
    for name in sorted(os.listdir(chars)):
        if name.lower().endswith(('.apj', '.cpj')):
            with open(os.path.join(chars, name), 'rb') as f:
                got = project_add(f.read(), base, new)
            if got is not None:
                out[f'chars{BS}{name}'] = got
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
    shared = [k for k in files if k.lower().endswith(('.pac', '.fpk', '.pak', 'collection.txt', 'hgopathnames.txt',
                                                      '.tsh', '.gsc', '.apj', '.cpj'))
              or 'icontpage' in k.lower()]
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
        if k.lower().endswith(('.apj', '.cpj')):
            out[k] = project_merge(cur, orig, mine)
        elif k.lower().endswith('.tsh'):
            from .gameportrait import tsh_merge
            out[k] = tsh_merge(cur, orig, mine)
        elif k.lower().endswith('.gsc'):
            from .gameportrait import gsc_merge
            out[k] = gsc_merge(cur, orig, mine)
        elif len(mine) == len(orig) == len(cur) and not k.lower().endswith(('.pac', '.fpk', '.pak', '.txt')):
            # a sheet of portraits changed in place: this export's changed bytes onto the archive's copy
            a, o, c = (np.frombuffer(x, np.uint8) for x in (mine, orig, cur))
            out[k] = np.where(a != o, a, c).astype(np.uint8).tobytes()
        elif mine[:4] == struct.pack('<I', pack.MAGIC) and cur[:4] == mine[:4]:
            out[k] = _merge_pack(cur, orig, mine)
        elif k.lower().endswith('.txt'):
            had = set(orig.replace(b'\r', b'').split(b'\n'))
            have = set(cur.replace(b'\r', b'').split(b'\n'))
            extra = [ln for ln in mine.replace(b'\r', b'').split(b'\n') if ln.strip() and ln not in had
                     and ln not in have]
            out[k] = cur.rstrip(b'\0').rstrip(b'\r\n') + b''.join(b'\r\n' + ln for ln in extra) + b'\r\n'
    return _fit_portrait_pack(_merge_portrait_page(install, files, out, game_root, now), game_root)


def project_merge(cur, orig, mine):
    """A project an earlier export changed (cur) with the entries this export added against the game's (orig)."""
    from .formats import ttstream

    def entries(data):
        st = ttstream.Stream(data)
        return [_editor_file(o) for lst in st.roots for r in lst for o in r['components'].get('Char Def Files', [])]
    had = set(entries(orig))
    m = entries(mine)
    out = cur
    for i, e in enumerate(m):
        if e and e not in had:
            before = next((m[j] for j in range(i - 1, -1, -1) if m[j] in had), None)
            got = project_add(out, before[1], e[1]) if before else None
            if got is not None:
                out = got
    return out


def _merge_portrait_page(install, files, out, game_root, now):
    """LEGO Marvel: a new character's portrait goes in the page cell of its line in the collection as merged (an
    earlier export's characters keep theirs), in the archive's current page and its copy in icons_tex.pak."""
    from . import gameportrait
    from .formats import pack
    page_k = next((k for k in files if k.lower().replace('/', BS) == f'stuff{BS}icons{BS}icontpage_nxg.tex'), None)
    coll_k = next((k for k in out if k.lower().endswith(f'chars{BS}collection.txt')), None)
    if page_k is None or coll_k is None:
        return out
    page = out[page_k]
    with open(os.path.join(game_root, 'chars', 'collection.txt'), 'rb') as f:
        own = {n.lower() for n in re.findall(rb'(?im)^\s*collect\s+"([^"]+)"', f.read())}
    added = [n.decode('latin1') for n in re.findall(rb'(?im)^\s*collect\s+"([^"]+)"', out[coll_k]) if n.lower() not in own]
    # every character the game did not have: its portrait (this export's, or an earlier one's in the archive) in
    # the cell of its line
    mine = {k.lower().replace('/', BS): v for k, v in files.items()}
    want = [f'stuff{BS}icons{BS}{n.lower()}_nxg.tex' for n in added]
    have = _read_installed(install, [w for w in want if w not in mine])
    for n, w in zip(added, want):
        icon = mine.get(w) or have.get(w)
        if icon:
            page = gameportrait.place_on_page(page, out[coll_k], n, icon)
    out[page_k] = page
    pak_k = next((k for k in out if k.lower() == 'icons_tex.pak'), None)
    if pak_k:
        out[pak_k] = pack.replace(out[pak_k], {f'stuff{BS}icons{BS}icontpage_nxg.tex': page})
    return out


def _fit_portrait_pack(out, game_root):
    """After merging: LEGO Pirates' icons_tex.pak must not grow (gameportrait.fit_pack)."""
    from . import gameportrait
    for k in out:
        if k.lower() == 'icons_tex.pak':
            src = os.path.join(game_root, k)
            if os.path.exists(src):
                out[k] = gameportrait.fit_pack(out[k], os.path.getsize(src))
    return out


def _merge_pack(cur, orig, mine):
    """The pack cur with the entries mine changed or added against orig: changed ones replaced in place, new
    ones placed after the entry they follow in mine (or at the end)."""
    from .formats import pack
    c_ents, stamp, tail = pack.read(cur)
    o = {n.lower(): b for n, _k, b, _x in pack.read(orig)[0]}
    m_ents = pack.read(mine)[0]
    at = {n.lower(): i for i, (n, *_r) in enumerate(c_ents)}
    in_order = [n for n, *_r in c_ents] == sorted(n for n, *_r in c_ents)
    prev = None
    for n, k, b, x in m_ents:
        low = n.lower()
        if low in o and o[low] == b:
            prev = low
            continue
        if low in at:
            cn, ck, _cb, cx = c_ents[at[low]]
            c_ents[at[low]] = (cn, ck, b, cx)
        elif in_order:
            pack.insert_sorted(c_ents, (n, k, b, x))
            at = {nn.lower(): i for i, (nn, *_r) in enumerate(c_ents)}
        else:
            c_ents.insert(at[prev] + 1 if prev in at else len(c_ents), (n, k, b, x))
            at = {nn.lower(): i for i, (nn, *_r) in enumerate(c_ents)}
        prev = low
    layout = pack._layout(cur, pack.read(cur)[0], stamp, tail)
    return pack.write(c_ents, stamp, tail, shared=pack.is_shared(cur) or pack.is_shared(mine), **layout)


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
