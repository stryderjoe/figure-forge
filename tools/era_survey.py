"""Survey an extracted game: what model, definition, animation and cutscene formats it uses (for sorting games into
eras). python tools/era_survey.py <extracted game folder> [name]"""
import collections
import os
import sys


def head(path, n=64):
    with open(path, 'rb') as f:
        return f.read(n)


def chunk_tags(path, limit=4096):
    """Four-letter tags found in the first part of a file (rough inventory of chunk names)."""
    d = head(path, limit)
    tags = collections.Counter()
    for i in range(0, len(d) - 4):
        t = d[i:i + 4]
        if all(65 <= c <= 90 or 48 <= c <= 57 for c in t):
            tags[t.decode()] += 1
    return tags


def main(root, name=''):
    ext = collections.Counter()
    by_ext = collections.defaultdict(list)
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            e = os.path.splitext(f)[1].lower()
            ext[e] += 1
            if len(by_ext[e]) < 400:
                by_ext[e].append(os.path.join(dirpath, f))
    print(f'== {name or root}: {sum(ext.values())} files')
    print('   top extensions:', ext.most_common(18))
    for e in ('.ghg', '.gsc', '.cd', '.an3', '.an4', '.as', '.cu2', '.cu3', '.cbx', '.tex', '.pak', '.fpk'):
        files = by_ext.get(e, [])
        if not files:
            continue
        starts = collections.Counter()
        for p in files[:200]:
            try:
                h = head(p, 16)
            except OSError:
                continue
            word = h[:4]
            label = word.decode('latin1') if all(32 <= c < 127 for c in word) else word.hex()
            second = h[4:8]
            label2 = second.decode('latin1') if all(32 <= c < 127 for c in second) else ''
            starts[(label, label2)] += 1
        print(f'   {e:5s} {ext[e]:6d} files; first bytes:', starts.most_common(5))
    # chunk tags in character models
    models = [p for p in by_ext.get('.ghg', []) if '\\chars\\' in p.lower() or '/chars/' in p.lower()][:60]
    tags = collections.Counter()
    for p in models:
        tags.update(set(chunk_tags(p, 1 << 16)))
    if tags:
        print('   chunk tags in character .ghg:', [t for t, n in tags.most_common(40) if n > len(models) // 3])


if __name__ == '__main__':
    main(*sys.argv[1:])
