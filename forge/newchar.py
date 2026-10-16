"""A NEW character for a classic TT LEGO game, made from a base character of that game.

What the game needs (LEGO Batman 2008 measured; Indy and Star Wars have the same files):
- chars\\<new>\\<new>_pc.ghg (and _lr_pc.ghg): the model, built on the base's skeleton;
- chars\\<new>\\<new>.txt: the base's text (layers, abilities, icon=, name_id=, animations);
- its animations: the base's .an3/.bsa files under the new folder, and <new>.fpk, the pack of them (paths
  inside a pack name the folder), with <new>_fpk.inf listing them;
- chars\\chars.txt: a char_start / dir / file / char_end entry;
- chars\\collection.txt: a `collect "<new>"` line (no condition: available from the start);
- the packs the game reads text from: alltxt.pak (every text file) and chars\\charstxt.fpk (character texts).
"""
import os
import re

from .formats import classic, pack


def _listing(folder):
    return classic.listing(folder)


def _find(folder, name):
    hit = _listing(folder).get(name.lower())
    return os.path.join(folder, hit) if hit else None


def _read(path):
    with open(path, 'rb') as f:
        return f.read()


def build(game_root, base_model_path, new_name, model_bytes, upper=False, unlock='', start=False):
    """{path inside the archive: bytes} for a new character `new_name` made from the base character whose
    model is `base_model_path` (inside the extracted `game_root`). `upper`: the archive's paths are upper
    case (Star Wars). `unlock`: the rest of the collect line (empty = available from the start)."""
    new = new_name.lower()
    if not re.fullmatch(r'[a-z0-9_]{2,24}', new):
        raise ValueError('a character name is 2 to 24 letters, digits or _')
    base_dir = os.path.dirname(base_model_path)
    base = classic.model_stem(base_model_path).lower()
    chars = os.path.dirname(base_dir)
    rel = lambda *parts: '\\'.join(parts)                          # noqa: E731
    case = (lambda s: s.upper()) if upper else (lambda s: s)
    out = {}
    folder = case(rel('chars', new))
    out[rel(folder, case(f'{new}_pc.ghg'))] = model_bytes
    out[rel(folder, case(f'{new}_lr_pc.ghg'))] = model_bytes
    txt_path = classic.character_txt(base_model_path)
    txt = _read(txt_path) if txt_path else b''
    out[rel(folder, case(f'{new}.txt'))] = txt

    # animations: the base's own files, and its pack with the folder renamed inside
    fpk = _find(base_dir, f'{base}.fpk')
    renamed = []
    if fpk:
        entries, stamp, tail = pack.read(_read(fpk))
        prefix_old = f'CHARS\\{base.upper()}\\'
        prefix_new = f'CHARS\\{new.upper()}\\'
        for name, kind, blob, extra in entries:
            nm = prefix_new + name[len(prefix_old):] if name.upper().startswith(prefix_old) else name
            renamed.append((nm, kind, blob, extra))
            loose = case(rel('chars', new, nm[len(prefix_new):].lower())) if nm.upper().startswith(prefix_new) else None
            if loose:
                out[loose] = blob
        out[rel(folder, case(f'{new}.fpk'))] = pack.write(renamed, stamp, tail, align=4)
        inf = _find(base_dir, f'{base}_fpk.inf')
        if inf:
            text = _read(inf).decode('latin1')
            text = re.sub(rf'(?i)chars\\{re.escape(base)}\\', f'chars\\\\{new}\\\\', text)
            out[rel(folder, case(f'{new}_fpk.inf'))] = text.encode('latin1')
    else:
        for name in os.listdir(base_dir):
            low = name.lower()
            if low.endswith(('.an3', '.bsa')):
                out[rel(folder, case(low))] = _read(os.path.join(base_dir, name))

    # the lists
    chars_txt = _find(chars, 'chars.txt')
    coll_txt = _find(chars, 'collection.txt')
    entry = f'\r\nchar_start\r\n\tdir "{new}"\r\n\tfile "{new}"\r\nchar_end\r\n'.encode('latin1')
    collect = f'\r\ncollect "{new}"{(" " + unlock) if unlock else ""}\r\n'.encode('latin1')
    new_chars = _read(chars_txt).rstrip(b'\0') + entry
    coll = _read(coll_txt).rstrip(b'\0')
    if start:
        # the first collect lines with no condition are the party a new game starts with
        m = re.search(rb'(?im)^collect\s', coll)
        at = m.start() if m else 0
        new_coll = coll[:at] + collect.lstrip(b'\r\n') + coll[at:]
    else:
        new_coll = coll + collect
    out[case(rel('chars', 'chars.txt'))] = new_chars
    out[case(rel('chars', 'collection.txt'))] = new_coll

    # the packs that carry text files
    txt_name = f'CHARS\\{new.upper()}\\{new.upper()}.TXT'
    alltxt = _find(game_root, 'alltxt.pak')
    if alltxt:
        out[case('alltxt.pak') if upper else 'alltxt.pak'] = pack.replace(
            _read(alltxt), {'CHARS\\CHARS.TXT': new_chars, 'CHARS\\COLLECTION.TXT': new_coll}, add=[(txt_name, 1, txt)])
    charstxt = _find(chars, 'charstxt.fpk')
    if charstxt:
        entries, stamp, tail = pack.read(_read(charstxt))
        kind = entries[0][1] if entries else 4
        entries = [e for e in entries if e[0].upper() != txt_name] + [(txt_name, kind, txt, (0, 0, 0))]
        out[case(rel('chars', 'charstxt.fpk'))] = pack.write(entries, stamp, tail, align=4)
    return out
