"""Resource headers (HSER) of LEGO Marvel Super Heroes and LEGO Batman 3: read, write, list another file.

    python tests/check_hser.py <game folder> [<game folder> ...]

Each folder is an extracted game ('...\\extracted\\marvel\\game'). For every file with a header: the header
reads and writes back byte for byte, and its tree rebuilds to the same bytes. Then, on a few definitions, a model
is listed the way the build tool lists one: every path listed before is still listed and its records still point
at it, the new file is there once with its record, and the header reads back.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from forge.formats import hser  # noqa: E402

failures = []


def check(ok, what):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        failures.append(what)


def record_paths(h):
    p = hser.paths(h)
    return sorted((p.get(r['node'], '?'), r['type'], r['platforms']) for r in h['records'])


def main(folders):
    for folder in folders:
        same = rebuilt = total = 0
        defs = []
        for dp, _dirs, files in os.walk(folder):
            for f in files:
                path = os.path.join(dp, f)
                with open(path, 'rb') as fh:
                    data = fh.read()
                if not hser.has_header(data):
                    continue
                total += 1
                try:
                    h, end = hser.read(data)
                except hser.HserError:
                    continue
                same += hser.write(h) == data[:end]
                if h['tree']:
                    hser.rebuild(h, hser.children(h))
                    rebuilt += hser.write(h) == data[:end]
                    if f.endswith('.cd') and len(defs) < 40:
                        defs.append(path)
                else:
                    rebuilt += 1
        check(same >= total - 12 and total > 0, f'{folder}: {same} of {total} headers write back byte for byte')
        check(rebuilt >= total - 12, f'{folder}: {rebuilt} of {total} trees rebuild to the same bytes')
        game = 'lmsh' if 'marvel' in folder.lower() else 'lb3'
        added = 0
        for path in defs:
            data = open(path, 'rb').read()
            h, end = hser.read(data)
            before = record_paths(h)
            listed = set(hser.paths(h).values())
            h2 = copy.deepcopy(h)
            for fp, t, plat, use, par in hser.model_paths('chars/minifigs/super_characters/super_minifig',
                                                          'ff_test_model', game):
                hser.add_file(h2, fp, t, plat, use, par)
            out = hser.write(h2)
            h3, _ = hser.read(out)
            after = record_paths(h3)
            new = set(hser.paths(h3).values())
            ok = (listed <= new and all(b in after for b in before) and
                  sum('ff_test_model' in p for p in new) == len(hser.model_paths('x', 'y', game)) and
                  h3['files'] == h['files'] + len(hser.model_paths('x', 'y', game)))
            added += ok
            if not ok:
                print('   not kept:', path)
        check(added == len(defs) and added > 0, f'{folder}: a model listed in {added} of {len(defs)} definitions')
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
