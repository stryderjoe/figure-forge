"""LEGO Batman 3 archives (index -6): the index rebuilds byte for byte, and files can be replaced and added.

    python tests/check_dat6.py <LEGO Batman 3 install folder> <work folder>

Every GAME*.DAT's index is read and rebuilt unchanged (its tree, sorted path hashes and MD5 tables). Then the
smallest archive is COPIED into the work folder (the install is only read): one file is replaced and one added,
and the copy is read back: both files, a sample of untouched files, and the index's MD5s of the new data.
The copy is deleted at the end. Nothing is tried in the game.
"""
import glob
import hashlib
import os
import random
import shutil
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from forge.formats import datpatch, datwrite  # noqa: E402
from forge.formats.dat import DatArchive  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def index_of(path):
    with open(path, 'rb') as f:
        a, size = struct.unpack('<II', f.read(8))
        at = (((a ^ 0xFFFFFFFF) << 8) + 0x100) if a & 0x80000000 else a
        f.seek(at)
        return f.read(size)


def main(install, work):
    dats = sorted(glob.glob(os.path.join(install, 'GAME*.DAT')))
    check(bool(dats), f'{len(dats)} archives found')
    for dat in dats:
        info = index_of(dat)
        ix = datwrite.read_index6(info)
        files = {p: ix['files'][i] for i, p in enumerate(ix['paths'])}
        md5 = {p: ix['md5'][i] for i, p in enumerate(ix['paths'])}
        check(datwrite.build_index6(ix, files, md5) == info,
              f'{os.path.basename(dat)}: index of {len(files)} files rebuilds byte for byte')
    small = min(dats, key=os.path.getsize)
    os.makedirs(work, exist_ok=True)
    copy = os.path.join(work, 'ff_check_' + os.path.basename(small))
    shutil.copyfile(small, copy)
    try:
        arc = DatArchive(copy)
        paths = [e.path for e in arc.entries]
        random.seed(3)
        sample = random.sample(range(len(paths)), 40)
        before = {paths[i]: hashlib.md5(arc.read(arc.entries[i])).digest() for i in sample}
        target = paths[sample[0]]
        arc.f.close()
        new = '\\'.join(['chars', 'ff_test', 'ff_test.cd'])
        replaced, added = datpatch.write_files(copy, {target: b'REPLACED' * 100, new: b'NEW' * 50})
        check(replaced == [target] and added == [new], 'one file replaced, one added')
        arc = DatArchive(copy)
        by = {e.path.lower(): e for e in arc.entries}
        check(arc.read(by[target.lower()]) == b'REPLACED' * 100, 'the replaced file reads back')
        check(arc.read(by[new.lower()]) == b'NEW' * 50, 'the added file reads back')
        same = sum(hashlib.md5(arc.read(by[p.lower()])).digest() == h for p, h in before.items() if p != target)
        check(same == len(before) - 1, f'untouched files read the same ({same} of {len(before) - 1})')
        arc.f.close()
        ix = datwrite.read_index6(index_of(copy))
        at = {p.lower(): i for i, p in enumerate(ix['paths'])}
        check(ix['md5'][at[new.lower()]] == (hashlib.md5(b'NEW' * 50).digest(),) * 2 and
              ix['md5'][at[target.lower()]][0] == hashlib.md5(b'REPLACED' * 100).digest(),
              'the index holds the MD5s of the new data')
    finally:
        os.remove(copy)
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2]))
