"""TT's self-describing serialised files (.cd character definitions, .as animation sets, .cpd ...), as used by
the games from LEGO Batman 2 (2012) on.

Layout (worked out from LEGO Batman 2's files). A str is u32 length (counting the closing zero) and the bytes.
A node is u32 size (of the whole node), str tag, payload.
    node "StreamInfo": u32 stream version (25 in LEGO Batman 2)
    node "TypeList": u32 count, count * node "Type": str name, i32 byte size (-1 = variable)
    node "ClassList": u32 count, count * node "Class":
        str class name, node "Version": u32, node "Types": u32 count, count * field
            field: u32 type (index into TypeList), str name, u32 a, u32 b, u32 c, i32 d (16 bytes after the name)
            c & 0x80000000: the type is a CLASS index and the value is that class's fields, inline, no header
                (base classes: c = 0xC0000000 / 0xD0000000; embedded members: 0xA0000000)
        [node "Components": u32 count, count * node "Component": str name, u32 class index]
    u32 root count, then that many "OLST" nodes.
    node "OLST": u16 class index, u32 count, count * node "MOBJ"
    node "MOBJ": 3 bytes (not understood), then the class's fields in order, then one OLST per component (in component order)
Field values: Char 1, Short 2, Int 4, Int64 8, Float 4, String str, Colour3 12, Vec3 12, Vec4 16, Mtx 64, Half 2,
HalfVec3 6; ClassObject: u8 present, then (if 1) str class name and a MOBJ node of that class.
"""
import struct


MARK = bytes([11, 0, 0, 0]) + b'StreamInfo' + bytes([0])


LAYOUT = {25: (3, 4), 24: (5, 4), 22: (5, 2)}       # stream version -> (object header bytes, list count bytes), measured


class StreamError(ValueError):
    pass


FIXED = {'Char': 1, 'Short': 2, 'Int': 4, 'Int64': 8, 'Float': 4, 'Colour3': 12, 'Vec3': 12, 'Vec4': 16,
         'Mtx': 64, 'Half': 2, 'HalfVec3': 6, 'Ptr': 4}


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.p)[0]
        self.p += 2
        return v

    def u32(self):
        v = struct.unpack_from('<I', self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.p)[0]
        self.p += 4
        return v

    def raw(self, n):
        v = self.d[self.p:self.p + n]
        if len(v) != n:
            raise StreamError(f'file ends at {self.p:#x}')
        self.p += n
        return v

    def str(self):
        at = self.p
        n = self.u32()
        if not 0 < n < 65536 or self.p + n > len(self.d) or self.d[self.p + n - 1] != 0:
            raise StreamError(f'bad string at {at:#x} (length {n})')
        s = self.d[self.p:self.p + n - 1].decode('latin1')
        self.p += n
        return s

    def node(self, tag=None):
        """(size, tag, payload start, end) of a node; the reader moves into the payload."""
        start = self.p
        size = self.u32()
        name = self.str()
        if tag is not None and name != tag:
            raise StreamError(f'expected node {tag!r} at {start:#x}, found {name!r}')
        if start + size > len(self.d) or size < 8:
            raise StreamError(f'node {name!r} at {start:#x} has a bad size {size}')
        return size, name, self.p, start + size


class Stream:
    """A parsed stream: .types, .classes, .roots (lists of objects). An object is a dict:
    {'class': name, 'id': u32, 'fields': {name: value}, 'components': {name: [objects]}, 'span': (start, end)}.
    String values remember where they are (self.strings: [(offset of the length word, node chain)]) so they can be
    rewritten (see replace_strings)."""

    def __init__(self, data):
        self.data = data
        start = 0
        if data[4:8] == b'.CC4':
            # LEGO Marvel Super Heroes wraps the stream in a resource header (the files it depends on)
            start = data.find(MARK) - 4
            if start < 0:
                raise StreamError('no stream after the resource header')
        r = Reader(data, start)
        _s, _t, _p, end = r.node('StreamInfo')
        self.version = r.u32()
        r.p = end
        _s, _t, _p, end = r.node('TypeList')
        count = r.u32()
        self.types = []
        for _ in range(count):
            _s, _t, _p, e = r.node('Type')
            name = r.str()
            self.types.append((name, r.i32()))
            r.p = e
        r.p = end
        _s, _t, _p, end = r.node('ClassList')
        count = r.u32()
        self.classes = []
        for _ in range(count):
            _s, _t, _p, ce = r.node('Class')
            cls = {'name': r.str(), 'fields': [], 'components': []}
            _s, _t, _p, e = r.node('Version')
            cls['version'] = r.raw(4)
            r.p = e
            _s, _t, _p, e = r.node('Types')
            for _ in range(r.u32()):
                t = r.u32()
                fname = r.str()
                a, b, c, d = struct.unpack('<IIIi', r.raw(16))
                # a field whose first flag word is 0x8000000N with N = 2 holds an object of class t, inline
                inline = bool(c & 0x80000000)
                cls['fields'].append({'type': ('inline', t) if inline else
                                      (self.types[t][0] if t < len(self.types) else t),
                                      'name': fname, 'flags': (a, b, c, d)})
            r.p = e
            if r.p < ce:
                _s, tag, _p, e = r.node()
                if tag == 'Components':
                    for _ in range(r.u32()):
                        _s, _t, _p, ee = r.node('Component')
                        cls['components'].append((r.str(), r.u32()))
                        r.p = ee
                r.p = e
            r.p = ce
            self.classes.append(cls)
        r.p = end
        self.data_at = r.p
        self.strings = []
        self.partial = 0
        # older streams: a list's count is 2 bytes and an object starts with more bytes (LEGO Pirates: version 22)
        self.id_size, self.count_size = LAYOUT.get(self.version, (3, 4))
        roots = r.u32()
        self.roots = []
        for _ in range(roots):
            self.roots.append(self._olst(r, ()))
        self.end = r.p

    def _olst(self, r, chain):
        size, _t, _p, end = r.node('OLST')
        here = chain + (end - size,)
        ci = r.u16()
        count = r.u32() if self.count_size == 4 else r.u16()
        objs = []
        for _ in range(count):
            objs.append(self._mobj(r, ci, here))
        if r.p != end:
            raise StreamError(f'list at {end - size:#x} of {self.classes[ci]["name"]!r} read {r.p - (end - size)} of {size}')
        return objs

    def _mobj(self, r, ci, chain):
        size, _t, _p, end = r.node('MOBJ')
        start = end - size
        here = chain + (start,)
        cls = self.classes[ci]
        obj = {'class': cls['name'], 'id': r.raw(self.id_size), 'fields': {}, 'components': {}, 'span': (start, end)}
        mark = len(self.strings)
        try:
            for f in cls['fields']:
                obj['fields'][f['name']] = self._value(r, f, here)
                if r.p > end:
                    raise StreamError('past the end')
            for name, cj in cls['components']:
                if r.p < end:
                    obj['components'][name] = self._olst(r, here)
            if r.p != end:
                raise StreamError(f'read {r.p - start} of {size} bytes')
        except (StreamError, struct.error, StopIteration, IndexError) as e:
            # every object carries its size: what is not understood is kept as it is (strings found in it after
            # the error are dropped, so they are never rewritten)
            del self.strings[mark + len([1 for v in obj['fields'].values() if isinstance(v, str)]):]
            obj['raw_tail'] = (r.p if r.p <= end else None, end)
            obj['error'] = str(e)
            self.partial += 1
            r.p = end
        return obj

    def _value(self, r, f, chain):
        t = f['type']
        if isinstance(t, tuple):
            cls = self.classes[t[1]]
            out = {'class': cls['name'], 'fields': {}, 'components': {}, 'inline': True}
            for g in cls['fields']:
                out['fields'][g['name']] = self._value(r, g, chain)
            return out
        if t == 'String':
            self.strings.append((r.p, chain))
            return r.str()
        if t in FIXED:
            raw = r.raw(FIXED[t])
            if t == 'Float':
                return struct.unpack('<f', raw)[0]
            if t in ('Int', 'Ptr'):
                return struct.unpack('<i', raw)[0]
            if t in ('Colour3', 'Vec3'):
                return struct.unpack('<3f', raw)
            if t == 'Vec4':
                return struct.unpack('<4f', raw)
            if t == 'Char':
                return raw[0]
            if t == 'Short':
                return struct.unpack('<h', raw)[0]
            if t == 'Int64':
                return struct.unpack('<q', raw)[0]
            return raw
        if t == 'ClassObject':
            if not r.u8():
                return None
            name = r.str()
            ci = next(i for i, c in enumerate(self.classes) if c['name'] == name)
            return self._mobj(r, ci, chain)
        raise StreamError(f'field {f["name"]!r} of type {t!r} at {r.p:#x}: not read yet')


def walk(objs):
    """Every object in a tree of lists, depth first."""
    for o in objs:
        yield o
        for v in o['fields'].values():
            if isinstance(v, dict) and 'class' in v:
                yield from walk([v])
        for lst in o['components'].values():
            yield from walk(lst)
