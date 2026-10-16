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

INSTALL_GUESSES = {
    'lb1': ('Lego Batman',),
    'indy': ('LEGO Indiana Jones The Original Adventures',),
    'tcs': ('Lego Star Wars Saga',),
}
STEAM_ROOTS = (r'C:\Program Files (x86)\Steam\steamapps\common', r'C:\Program Files\Steam\steamapps\common')
BACKUP = '.figureforge-backup'


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
    """{archive path: bytes} for the figure. game: catalog.Game; base_char: classic.Character of the body."""
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


def patch(install, files):
    dat = os.path.join(install, 'GAME.DAT')
    backup = dat + BACKUP
    if not os.path.exists(backup):
        shutil.copy2(dat, backup)
    replaced, added = datpatch.write_files(dat, files)
    return {'archive': dat, 'backup': backup, 'replaced': len(replaced), 'added': len(added)}


def restore(install):
    dat = os.path.join(install, 'GAME.DAT')
    backup = dat + BACKUP
    if not os.path.exists(backup):
        raise ExportError('there is no Figure Forge backup of this game\'s GAME.DAT')
    shutil.copy2(backup, dat)
    return {'archive': dat, 'restored_from': backup}


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
