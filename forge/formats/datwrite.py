"""Write a classic TT .DAT archive's index, so files can be ADDED (LEGO Batman 2008 and Indiana Jones 1:
index -2; LEGO Star Wars TCS: index -3).

Index, measured on the three games' archives (2026-10-15):
    i32 version, i32 file count n,
    n * {u32 offset >> 8, u32 stored size, u32 size, u32 flags}    flags: low byte compression, next byte
                                                                    offset & 0xff (0 in the originals)
    i32 record count, records {i16 next, i16 prev, i32 name offset}: a tree in depth-first order. Record 0 is
        the root. A folder's `next` is the index of its last direct child; a file's `next` is minus its file
        number; `prev` is the previous sibling (0 for a first child). In a folder, files come before folders.
    i32 names size, names (zero-terminated)
    n * u32 FNV-style hash of the upper-case path (h = 0x811C9DC5; h = (h ^ c) * 0x199933), then 8 zero bytes.
    -2: files are numbered in tree order and the hashes follow that order.
    -3: the tree still numbers files in tree order, but the file table and the hashes are sorted by hash.
"""
import struct


def path_hash(path):
    h = 0x811C9DC5
    for ch in path.upper().encode('latin1'):
        h = ((h ^ ch) * 0x199933) & 0xFFFFFFFF
    return h


def read_tree(info):
    """The archive's own tree: ({folder path (lower): [child names in order]}, [file paths by file number])."""
    version, count = struct.unpack_from('<ii', info, 0)
    pos = 8 + count * 16
    (nn,) = struct.unpack_from('<i', info, pos)
    recs = [struct.unpack_from('<hhi', info, pos + 4 + 8 * i) for i in range(nn)]
    pos += 4 + nn * 8
    (ns,) = struct.unpack_from('<i', info, pos)
    blob = info[pos + 4:pos + 4 + ns]

    def name(o):
        return blob[o:blob.index(b'\0', o)].decode('latin1')

    order, full, parent_of = {}, [''] * nn, [0] * nn
    is_dir = [r[0] > 0 for r in recs]
    is_dir[0] = True
    last_dir = 0
    for i in range(1, nn):
        nx, pv, no = recs[i]
        parent_of[i] = parent_of[pv] if pv else last_dir
        par = full[parent_of[i]]
        full[i] = f'{par}\\{name(no)}' if par else name(no)
        order.setdefault(par.lower(), []).append(name(no))
        if nx > 0:
            last_dir = i
            is_dir[i] = True
    # an empty folder has next 0 too, like file 0: the first file record is the one whose path is a file
    return order, full, recs, is_dir


def build_index(version, files, order=None, names_pad=None):
    """files: {path: (off_hi, zsize, size, flags)} (paths with backslashes). Returns index bytes.
    order: {folder (lower): [names]} keeps an existing archive's order of children; new names go last."""
    tree = {}
    for path in files:
        parts = path.split('\\')
        node = tree
        for d in parts[:-1]:
            node = node.setdefault(('d', d.lower()), {'__name__': d})
        node[('f', parts[-1].lower())] = parts[-1]
    records, names, file_paths, file_recs = [], bytearray(), [], []

    def add_name(n):
        # every record has its own copy of its name (the originals do not share them)
        at = len(names)
        names.extend(n.encode('latin1') + b'\0')
        return at

    def children(node, prefix):
        fs = [k for k in node if k != '__name__' and k[0] == 'f']
        ds = [k for k in node if k != '__name__' and k[0] == 'd']
        want = order.get(prefix.lower()) if order else None
        if want is not None:
            rank = {n.lower(): i for i, n in enumerate(want)}
            fs.sort(key=lambda k: (rank.get(k[1], 1 << 30), k[1]))
            ds.sort(key=lambda k: (rank.get(k[1], 1 << 30), k[1]))
        else:
            fs.sort(key=lambda k: k[1])
            ds.sort(key=lambda k: k[1])
        return fs, ds

    def walk(node, prefix, my_index):
        fs, ds = children(node, prefix)
        prev, last = 0, 0
        for k in fs:
            name = node[k]
            idx = len(records)
            records.append([0, prev, add_name(name)])
            file_recs.append(idx)
            file_paths.append(f'{prefix}\\{name}' if prefix else name)
            prev = last = idx
        for k in ds:
            sub = node[k]
            idx = len(records)
            records.append([0, prev, add_name(sub['__name__'])])
            prev = last = idx
            walk(sub, f'{prefix}\\{sub["__name__"]}' if prefix else sub['__name__'], idx)
        records[my_index][0] = last

    records.append([0, 0, add_name('')])
    walk(tree, '', 0)
    if version == -3:
        listed = sorted(file_paths, key=path_hash)
    else:
        listed = list(file_paths)
    # the tree numbers files in tree order in both kinds; -3 lists the file table (and hashes) by hash
    for n, idx in enumerate(file_recs):
        records[idx][0] = -n
    out = bytearray(struct.pack('<ii', version, len(listed)))
    for p in listed:
        out += struct.pack('<IIII', *files[p])
    out += struct.pack('<i', len(records))
    for nx, pv, no in records:
        out += struct.pack('<hhi', nx, pv, no)
    # the originals pad the names (by 1 to 7 bytes, by no rule found); 4-byte alignment is used for new ones
    names += b'\0' * (names_pad if names_pad is not None else (-len(names)) % 4)
    out += struct.pack('<i', len(names)) + bytes(names)
    out += b''.join(struct.pack('<I', path_hash(p)) for p in listed) + b'\0' * 8
    return bytes(out)
