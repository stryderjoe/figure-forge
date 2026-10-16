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

from . import gamewrite, newchar
from .formats import classic, datpatch

NXG_EXPORT = {'lb2', 'lmsh', 'pirates'}                    # super-hero era games the figure can be written into
INSTALL_GUESSES = {
    'lb2': ('LEGO Batman 2',),
    'lmsh': ('LEGO Marvel Super Heroes',),
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
    if game.id == 'lb3':
        raise ExportError(f'putting figures into {game.title} is not built yet')
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
    """LEGO Batman 2 and LEGO Marvel Super Heroes: the figure as a model of its own beside the shared one, and the
    body's definition pointed at it (see nxgwrite), replacing the body or as a new character."""
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
        # LEGO Marvel: the name has the shared model's length (its resource header names the companions by it),
        # with a copy of the shared model's shaders
        size = len(writer.stem)
        stem = ('ff' + base)[:size].ljust(size, '_')
        out = writer.write(glb, stem, report=report, new_stem=stem)
        with open(writer.path[:-len('.ghg')] + '.pc_shaders', 'rb') as f:
            shaders = f.read()
        files = {f'{folder}{BS}{stem}_nxg.ghg': out['ghg'],
                 f'{folder}{BS}{stem}_nxg.nxg_textures': out['nxg_textures'],
                 f'{folder}{BS}{stem}_nxg.pc_shaders': shaders}
    ref = BS.join(rel + [stem.upper()])
    cd = nxgwrite.definition(base_char, ref)
    if new is None:
        files[archive_path(game.root, base_char.path)] = cd
        report(f'{base_char.name} now wears the figure (model {stem}_nxg.ghg).')
        return files
    files.update(nxg_new_character(game, base_char, new, cd))
    report(f'{new} joins the character list (from {base_char.name}, model {stem}_nxg.ghg).')
    return files


def nxg_new_character(game, base_char, new, cd):
    r"""LEGO Batman 2 / LEGO Marvel / LEGO Pirates: a character is found by the name of its text file, wherever its folder is
    (collection.txt says `collect "IronManClassic"`, the file is minifigs\ironman\ironmanclassic.txt), so a
    new one is chars\minifigs\<new>\<new>.cd and .txt (the base's text: its abilities, sizes and name), a
    `collect` line (no condition: there from the start), and both text files in chars\charstxt.fpk, which the
    game reads its character texts from."""
    from .formats import pack
    chars = os.path.join(game.root, 'chars')
    folder = BS.join(['chars', 'minifigs', new])
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


def patch(install, files):
    """Each file goes into the archive that already holds it; new files into GAME.DAT. Every archive changed is
    copied to '<archive>.figureforge-backup' first (once)."""
    dats = archives(install)
    if not dats:
        raise ExportError('no GAME.DAT in the game folder')
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
                'The game reads these loose files only when its GAME.DAT is out of the way:\n'
                '1. Back up the game folder.\n'
                '2. Unpack GAME.DAT into the game folder with a TT archive extractor, then rename GAME.DAT.\n'
                '3. Copy the contents of this folder into the game folder, replacing files.\n\n'
                'Or let Figure Forge patch the archive directly (Export > Into the game > Patch).\n'
                'This mod holds files made from your own copy of the game: do not share it publicly.\n')
    return {'folder': folder, 'files': len(files)}
