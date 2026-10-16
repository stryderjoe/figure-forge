"""LEGO Batman 3's .res files: each worked out again from the records of the file it belongs to (and the files
those name), and written again, must equal the game's own byte for byte.
Usage: python tests/check_res.py <extracted LEGO Batman 3 game folder> [limit]"""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from forge.formats import hser  # noqa: E402

root = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
cache = {}


def own(path):
    if path not in cache:
        cache[path] = None
        p = os.path.join(root, *path.split('/'))
        if os.path.exists(p):
            with open(p, 'rb') as f:
                d = f.read()
            if hser.has_header(d):
                cache[path] = hser.entries(hser.read(d)[0])
    return cache[path]


files = sorted(glob.glob(os.path.join(root, '**', '*.res'), recursive=True))[:limit]
same = records = 0
bad = []
for f in files:
    rel = os.path.relpath(f, root).replace(os.sep, '/')[:-4]
    with open(f, 'rb') as fh:
        want = fh.read()
    h, end = hser.read(want)
    got_records = hser.pc_resources(own(rel) or [], own)
    records += got_records == hser.entries(h)
    got = hser.build(got_records, h)
    if got == want[:end] and end == len(want):
        same += 1
    elif len(bad) < 5:
        bad.append(rel)
print(f'records {records} / {len(files)}, bytes {same} / {len(files)}', bad)
print('SUMMARY', 'all passed' if same == len(files) else 'FAILED')
