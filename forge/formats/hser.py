"""Resource headers ('HSER', the engine's NuResourceHeader) of LEGO Marvel Super Heroes and LEGO Batman 3.

Every resource of those games (.cd, .ghg, .gsc, .as, .cpd, .res, texture and shader bundles, levels) starts with
one. It is big-endian:

    u32 size (of everything after this word), '.CC4', 'HSER', 'HSER', u32 version (6), u32 has a tree (1 / 0),
    with a tree: u32 1, u32 file count, u32 node count, u32 string table size, string table (at 0x28),
        nodes: {i16 a, i16 b, u32 name, u16 parent} * node count, u32 unknown (kept as read),
    'ROTV', u32 count, records * count:
        {u32 type, 0, u32 node, u32 n, n * 16 bytes, u32 platforms[, u32 usage, u32 parameter]}
        (definitions and most LEGO Batman 3 files have the last two words; LEGO Marvel's models and scenes not),
    u32 resource type, then the build's bookkeeping (version-control stream and transaction, user, project,
    source file name, discipline: kept as read).

The nodes are the path tree of the files the resource depends on, depth first. Node 0 is the root; its name word
is a 32-bit value that is not a hash of the path or the content (kept as read). For a folder, a is the number of
its last child and b of its previous sibling (0: none), so children are linked backwards; for a file, a is minus
its file number (files are numbered 0, 1, 2 ... in node order) and b its previous sibling. Strings are written
in full for each node, each followed by one extra zero, and the table ends with two more zeros.

Record types (measured over every file of both games): 1 .gsc, 2 .ghg, 5 texture, 9 .cd, 0x0a .as, 0x0d
.nxg_textures, 0x0e shaders, 0x10 .cpd, 0x11 .krw, 0x12 .dno, 0x14 .an4. Platforms is a mask (LEGO Batman 3:
0x4001EDBF on _dx11 files, 0x8001EDBF on _nxg, 0x2001EDBF / 0x20000100 on the PS Vita copies; LEGO Marvel
0x40000DBF / 0xA0000DBF; 0xFFFFFFFF for most others). Records keep the build's order (not sorted).

A definition lists every model it names, one per platform (LEGO Marvel 55 of 56, LEGO Batman 3 262 of 262), even
a platform's model that is not shipped (LEGO Marvel's _dx11 models). Minifigures list the shared .cpd instead.

LEGO Batman 3 reads the header with its generic serialiser (LEGOBatman3_DX11.exe 0x140214ce0: the tag compared
as 'HSER', version 6, fields m_resources, m_enResourceType, m_strAccurevStream, m_accurevTransaction,
m_strProducedByUserName, project, m_strSourceFileName, discipline). Whether the game refuses a file its list leaves
out has not been tested in the game.
"""
import struct

MAGIC = b'.CC4HSERHSER'
TYPES = {'.gsc': 1, '.ghg': 2, '.tex': 5, '.cd': 9, '.as': 0x0a, '.nxg_textures': 0x0d, '.pc_shaders': 0x0e,
         '.cpd': 0x10, '.krw': 0x11, '.dno': 0x12, '.an4': 0x14}


class HserError(ValueError):
    pass


def has_header(data):
    return data[4:16] == MAGIC


def read(data):
    """The header as a dict, and its length in bytes (the resource's own data follows)."""
    if not has_header(data):
        raise HserError('no HSER header')
    size, _magic, version, has_tree = struct.unpack_from('>I12sII', data, 0)
    p = 0x18
    h = {'version': version, 'tree': bool(has_tree), 'nodes': [], 'files': 0, 'unknown': 0, 'one': 1}
    if has_tree:
        h['one'], h['files'], count, strsize = struct.unpack_from('>IIII', data, p)
        strings = data[0x28:0x28 + strsize]
        p = 0x28 + strsize
        for i in range(count):
            a, b, name, parent = struct.unpack_from('>hhIH', data, p)
            p += 10
            if i == 0:
                h['nodes'].append({'a': a, 'b': b, 'root': name, 'parent': parent, 'name': ''})
            else:
                e = strings.find(b'\0', name)
                if e < 0:
                    raise HserError(f'node {i}: name outside the string table')
                h['nodes'].append({'a': a, 'b': b, 'name': strings[name:e].decode('latin1'), 'parent': parent})
        h['unknown'] = struct.unpack_from('>I', data, p)[0]
        p += 4
    if data[p:p + 4] != b'ROTV':
        raise HserError('no record list')
    n = struct.unpack_from('>I', data, p + 4)[0]
    p += 8
    end = size + 4
    # LEGO Batman 3's records end with two more words (usage, parameter) than LEGO Marvel's: the layout is the one
    # whose list ends on the resource type and the version-control stream's name
    for wide in (True, False):
        got = _records(data, p, n, wide, end)
        if got is not None:
            break
    else:
        raise HserError('unexpected record layout')
    h['records'], p = got
    h['wide'] = wide
    if p > end:
        raise HserError('records run past the header')
    h['rest'] = data[p:end]
    return h, end


def _records(data, p, n, wide, end):
    records = []
    for _ in range(n):
        if p + 20 > end:
            return None
        t, z1, node, extra = struct.unpack_from('>4I', data, p)
        if z1 or extra > 16:
            return None
        blob = data[p + 16:p + 16 + 16 * extra]
        p += 16 + 16 * extra
        if wide:
            platforms, usage, param = struct.unpack_from('>3I', data, p)
            p += 12
        else:
            platforms, usage, param = struct.unpack_from('>I', data, p)[0], None, None
            p += 4
        records.append({'type': t, 'node': node, 'extra': blob, 'platforms': platforms, 'usage': usage,
                        'param': param})
    # then: u32 resource type, u16 length, the stream's name (printable)
    if p + 6 > end:
        return None
    rtype, ln = struct.unpack_from('>IH', data, p)
    name = data[p + 6:p + 6 + ln].rstrip(b'\0')
    if rtype > 0x100 or ln > 200 or not all(32 <= c < 127 for c in name):
        return None
    return records, p


def write(h):
    """The header's bytes (the inverse of read)."""
    body = bytearray()
    if h['tree']:
        table = bytearray()
        offsets = []
        for n in h['nodes'][1:]:
            offsets.append(len(table))
            table += n['name'].encode('latin1') + b'\0\0'
        table += b'\0\0'                    # the table ends with an empty name
        body += struct.pack('>IIII', h['one'], h['files'], len(h['nodes']), len(table)) + table
        for i, n in enumerate(h['nodes']):
            name = n['root'] if i == 0 else offsets[i - 1]
            body += struct.pack('>hhIH', n['a'], n['b'], name, n['parent'])
        body += struct.pack('>I', h['unknown'])
    body += b'ROTV' + struct.pack('>I', len(h['records']))
    for r in h['records']:
        extra = r.get('extra', b'')
        body += struct.pack('>4I', r['type'], 0, r['node'], len(extra) // 16) + extra
        if h.get('wide', True):
            body += struct.pack('>3I', r['platforms'], r['usage'], r['param'])
        else:
            body += struct.pack('>I', r['platforms'])
    body += h['rest']
    out = MAGIC + struct.pack('>II', h['version'], 1 if h['tree'] else 0) + bytes(body)
    return struct.pack('>I', len(out)) + out


def paths(h):
    """{node number: 'folder/.../file'} of the files listed."""
    nodes = h['nodes']
    out = {}
    for i, n in enumerate(nodes):
        if _is_file(h, i):
            parts, j = [], i
            while j:
                parts.append(nodes[j]['name'])
                j = nodes[j]['parent']
            out[i] = '/'.join(reversed(parts))
    return out


def children(h):
    """[[child node numbers in order]] per node (b links run backwards from a)."""
    nodes = h['nodes']
    out = [[] for _ in nodes]
    for i, n in enumerate(nodes):
        if i == 0 or not _is_file(h, i):
            c = n['a']
            seen = set()
            while c > 0 and c not in seen:
                seen.add(c)
                out[i].append(c)
                c = nodes[c]['b']
            out[i].reverse()
    return out


def _is_file(h, i):
    nodes = h['nodes']
    if i == 0:
        return False
    if nodes[i]['a'] < 0:
        return True
    # a = 0 is file number 0, or a folder with nothing in it; a folder's a points at a child that names it
    a = nodes[i]['a']
    return a == 0 or nodes[a]['parent'] != i


def rebuild(h, kids):
    """Renumber the tree depth first from per-node child lists (old node numbers), relink a and b, renumber the
    files and the records' node numbers (records keep their order)."""
    nodes = h['nodes']
    order = []

    def walk(i):
        order.append(i)
        for c in kids[i]:
            walk(c)
    walk(0)
    new = {old: k for k, old in enumerate(order)}
    out = []
    file_no = 0
    for old in order:
        n = dict(nodes[old])
        n['parent'] = 0xFFFF if old == 0 else new[n['parent']]
        out.append(n)
    for k, old in enumerate(order):
        n = out[k]
        sib = kids[nodes[old]['parent']] if old else []
        pos = sib.index(old) if old else 0
        n['b'] = new[sib[pos - 1]] if old and pos > 0 else 0
        if old and not kids[old] and n.get('file', _is_file(h, old)):
            n['a'] = -file_no
            file_no += 1
        else:
            n['a'] = new[kids[old][-1]] if kids[old] else 0
        n.pop('file', None)
    h['nodes'] = out
    h['files'] = file_no
    for r in h['records']:
        r['node'] = new[r['node']]
    return new


def add_file(h, path, type_=None, platforms=0xFFFFFFFF, usage=2, param=0):
    """List one more file ('folder/.../name', lower case as the games write them) with one record, the way the
    build tool does: missing folders and the file become the last children of their parents, and the tree is
    renumbered depth first. Returns the file's node number. A file listed already only gains the record."""
    if not h['tree']:
        raise HserError('the header lists no files')
    parts = [p for p in path.lower().replace('\\', '/').split('/') if p]
    kids = children(h)
    nodes = h['nodes']
    files = {i for i in range(1, len(nodes)) if _is_file(h, i)}
    cur = 0
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        hit = next((c for c in kids[cur] if nodes[c]['name'] == part and ((c in files) == last)), None)
        if hit is None:
            hit = len(nodes)
            nodes.append({'a': 0, 'b': 0, 'name': part, 'parent': cur, 'file': last})
            kids.append([])
            kids[cur].append(hit)
            if last:
                files.add(hit)
        cur = hit
    for i in files:
        nodes[i].setdefault('file', True)
    if type_ is None:
        type_ = next((t for e, t in TYPES.items() if parts[-1].endswith(e)), 0)
    rec = {'type': type_, 'node': cur, 'extra': b'', 'platforms': platforms, 'usage': usage, 'param': param}
    if not h.get('wide', True):
        rec.update(usage=None, param=None)
    h['records'].insert(0, rec)            # the games list the newest, highest numbered files first
    new = rebuild(h, kids)
    return new[cur]


def model_paths(stem_dir, stem, game):
    """The model files a definition lists for a model of its own, one per platform, with their records
    (type, platforms, usage, parameter), as LEGO Marvel and LEGO Batman 3 list them (abomb.cd,
    bane_bigfig.cd)."""
    base = stem_dir.rstrip('/') + '/' + stem.lower()
    if game == 'lmsh':
        return [(base + '_nxg.ghg', 2, 0xA0000DBF, 2, 0), (base + '_dx11.ghg', 2, 0x40000DBF, 2, 0)]
    return [(base + '_nxg.ghg', 2, 0x8001EDBF, 2, 0), (base + '_dx11.ghg', 2, 0x4001EDBF, 2, 0),
            (base + '_mob.ghg', 2, 0x2001EDBF, 2, 0)]


def entries(h):
    """The header's records as (type, 'folder/.../file', platforms, usage, parameter, extra bytes), in order."""
    ps = paths(h) if h['tree'] else {}
    return [(r['type'], ps.get(r['node'], '').lstrip('/'), r['platforms'], r['usage'], r['param'], r['extra'])
            for r in h['records']]


def pc_resources(own, lookup):
    r"""LEGO Batman 3's '<file>.res': what the PC game loads with a file, worked out from its own records the way
    the build does (reproduces every one of the game's 4563 .res files, records and bytes).

    Each record for the PC (platforms with bit 0x40000000 and bit 0x20) brings in, before itself, the PC records
    (extra bytes kept) of the file it names (lookup(path) -> that file's own records, or None) whose usage is 0 or shares a bit with
    the record's parameter, and so on down; everything brought in takes the usage of the top record. A record met
    again (same type, path and parameter) is kept where it was first met, its usage gaining the new bits."""
    out, index = [], {}

    def pc(platforms):
        return platforms & 0x40000000 and platforms & 0x20

    def add(e):
        k = (e[0], e[1], e[4])
        if k in index:
            o = out[index[k]]
            out[index[k]] = o[:3] + (o[3] | e[3],) + o[4:]
        else:
            index[k] = len(out)
            out.append(e)

    def visit(e, usage, depth):
        if depth > 16:
            raise HserError(f'dependencies of {e[1]} nest too deep')
        for s in lookup(e[1]) or ():
            if pc(s[2]) and (s[3] == 0 or s[3] & e[4]):
                visit(s, usage, depth + 1)
                add(s[:3] + (usage,) + s[4:])

    for e in own:
        if pc(e[2]):
            visit(e, e[3], 0)
            add(e)
    return out


def build(records, like):
    """A header listing records ((type, path, platforms, usage, parameter, extra bytes), in order) with the version, root word,
    unknown word and bookkeeping of the header like (a dict from read). The tree is laid out as the build lays a
    .res file's out: each folder's children in the reverse of the order they are first met in the records."""
    kids, nodes, at = [[]], [dict(like['nodes'][0]) if like['tree'] else
                             {'a': 0, 'b': 0, 'root': 0, 'parent': 0xFFFF, 'name': ''}], {}
    file_node = {}
    for _t, path, *_ in records:
        if path in file_node:
            continue
        parts = path.split('/')
        cur = 0
        for k, part in enumerate(parts):
            last = k == len(parts) - 1
            key = (cur, part, last)
            if key not in at:
                at[key] = len(nodes)
                nodes.append({'a': 0, 'b': 0, 'name': part, 'parent': cur, 'file': last})
                kids.append([])
                kids[cur].insert(0, at[key])
            cur = at[key]
        file_node[path] = cur
    h = {'version': like['version'], 'tree': True, 'one': like.get('one', 1), 'nodes': nodes, 'files': 0,
         'unknown': like.get('unknown', 0), 'wide': like.get('wide', True), 'rest': like['rest'],
         'records': [{'type': t, 'node': file_node[p], 'extra': x, 'platforms': pl, 'usage': u, 'param': pa}
                     for t, p, pl, u, pa, x in records]}
    rebuild(h, kids)
    return write(h)
