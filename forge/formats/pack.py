"""TT file packs: alltxt.pak (every text file), chars\\charstxt.fpk (character text files) and
chars\\<name>\\<name>.fpk (a character's animations), in LEGO Batman 2008 and LEGO Star Wars TCS.

Layout, measured (2026-10-15):
    u32 0x1234567A, u32 count, u32 file size, u32 a stamp (not a checksum of anything tried), u32 0,
    u32 0x00402F4C, then count * {u32 name offset, u32 data offset, u32 size, u32 kind (1 text, 4 ...), 3 x u32 0}
    (offsets from the start of the pack), the names (zero-terminated, upper case, backslashes), then the data.
"""
import struct

MAGIC = 0x1234567A


class PackError(ValueError):
    pass


def read(data):
    """[(name, kind, bytes)] in the pack's order, and the header's stamp and last word."""
    magic, count, total, stamp, zero, tail = struct.unpack_from('<6I', data, 0)
    if magic != MAGIC:
        raise PackError('not a TT pack')
    out = []
    for i in range(count):
        no, do, size, kind, a, b, c = struct.unpack_from('<7I', data, 0x18 + 28 * i)
        name = data[no:data.index(b'\0', no)].decode('latin1')
        out.append((name, kind, data[do:do + size], (a, b, c)))
    return out, stamp, tail


def layout_of(data):
    """How a pack lays out its data: the gap rule between entries (for byte-identical rewrites)."""
    count = struct.unpack_from('<I', data, 4)[0]
    ents = [struct.unpack_from('<7I', data, 0x18 + 28 * i) for i in range(count)]
    return ents


def write(entries, stamp=0, tail=0x00402F4C, align=1, gap=0, lead=0):
    """entries: [(name, kind, bytes, extra(3 u32))] -> pack bytes. Data follows the names in entry order;
    `lead` zero bytes before the first file, `gap` after each (LEGO Batman 2 and Marvel: 2), then `align`."""
    count = len(entries)
    names = bytearray()
    name_off = []
    base = 0x18 + 28 * count
    for name, *_ in entries:
        name_off.append(base + len(names))
        names += name.encode('latin1') + b'\0'
    pos = base + len(names)
    data = bytearray(lead)
    data_off = []
    for _name, _kind, blob, _extra in entries:
        pad = (-(pos + len(data))) % align
        data += b'\0' * pad
        data_off.append(pos + len(data))
        data += blob + bytes(gap)
    total = pos + len(data)
    out = bytearray(struct.pack('<6I', MAGIC, count, total, stamp, 0, tail))
    for (name, kind, blob, extra), no, do in zip(entries, name_off, data_off):
        out += struct.pack('<7I', no, do, len(blob), kind, *extra)
    out += names + data
    return bytes(out)


def replace(data, changes, add=()):
    """A pack with some files' contents changed ({name: bytes}, names compared ignoring case) and new files
    added ([(name, kind, bytes)]), keeping everything else as it was."""
    entries, stamp, tail = read(data)
    layout = _layout(data, entries, stamp, tail)
    want = {k.upper(): v for k, v in changes.items()}
    out = [(n, k, want.get(n.upper(), b), x) for n, k, b, x in entries]
    have = {n.upper() for n, *_ in out}
    # new names follow the pack's own case (upper in LEGO Batman 2008, lower in LEGO Batman 2 and Marvel)
    upper = not entries or entries[0][0] == entries[0][0].upper()
    for n, k, b in add:
        if n.upper() not in have:
            out.append((n.upper() if upper else n.lower(), k, b, (0, 0, 0)))
    return write(out, stamp, tail, **layout)


def _layout(data, entries, stamp, tail):
    """The spacing of an existing pack ({align, gap, lead}) under which it rewrites unchanged, else 4-byte
    alignment (LEGO Batman 2008's packs)."""
    for kw in ({'align': 1, 'gap': 2, 'lead': 2}, {'align': 1, 'gap': 2}, {'align': 4}, {'align': 1},
               {'align': 2}, {'align': 8}, {'align': 16}):
        if write(entries, stamp, tail, **kw) == data:
            return kw
    return {'align': 4}
