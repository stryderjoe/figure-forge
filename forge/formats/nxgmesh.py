"""The MESH section of TT's newer models (*_nxg.ghg / .gsc, big-endian): LEGO Batman 2 (MESH 47 / 48),
LEGO Marvel Super Heroes (161 / 169). Exploration reader: walks the part records and their buffers.

MESH 48 (LEGO Batman 2), measured on super_minifig_nxg.ghg:
    'HSEM' u32 version, 'ROTV' u32 part count, then per part:
        u32 stream count, per stream: a vertex buffer reference, u32 byte offset
        u32 0, an index buffer reference, u32 first index, u32 index count, u32 first vertex, u16 0,
        u32 vertex count, ...
    A buffer reference is u32 1 (a new buffer follows) or 0xC000nnnn (a buffer already read: nnnn counts every
    buffer-like record read so far). A new vertex buffer: u32 flags, u32 vertex count, u32 descriptor count,
    descriptors {u8 attribute, u8 storage, u8 offset}, then the vertices. A new index buffer: u32 flags,
    u32 index count, u32 index size, then the indices.
Attributes: 0 position, 1 normal, 2 colour, 5 uv, 9 skin indices, 10 skin weights.
Storage: 2 float2, 3 float3, 4 float4, 5 half2, 6 half4, 7 byte4, 8 byte4 (signed/normalised), 9 colour byte4.
"""
import struct

SIZES = {2: 8, 3: 12, 4: 16, 5: 4, 6: 8, 7: 4, 8: 4, 9: 4}


class MeshError(ValueError):
    pass


class C:
    def __init__(self, d, p):
        self.d, self.p = d, p

    def u32(self):
        v = struct.unpack_from('>I', self.d, self.p)[0]
        self.p += 4
        return v

    def u16(self):
        v = struct.unpack_from('>H', self.d, self.p)[0]
        self.p += 2
        return v

    def take(self, n):
        at = self.p
        self.p += n
        if self.p > len(self.d):
            raise MeshError('past the end')
        return at


class MeshSection:
    """Parts of the MESH section: each {'streams': [(buffer key, byte offset)], 'index': buffer key,
    'first_index', 'index_count', 'first_vertex', 'vertex_count', 'palette': [bone numbers]}."""

    def __init__(self, data):
        self.d = data
        at = data.find(b'HSEM')
        while at >= 0 and struct.unpack_from('>I', data, at + 4)[0] not in (46, 47, 48, 161, 169):
            at = data.find(b'HSEM', at + 4)
        if at < 0:
            raise MeshError('no MESH section of a known version')
        c = C(data, at + 4)
        self.version = c.u32()
        self.old = self.version in (46, 47, 48)
        # LEGO Pirates' MESH 46 / 47 has the count without the marker, and 40-byte part tails (72 in LEGO Batman 2)
        self.tail = 72
        if data[c.p:c.p + 4] == b'ROTV':
            c.p += 4
        elif self.version in (46, 47):
            self.tail = 40
        else:
            raise MeshError('MESH without ROTV')
        count = c.u32()
        self.buffers, self.parts, self.serial, refs = {}, [], 0, []
        for _ in range(count):
            part = {'streams': []}
            for _s in range(c.u32() & 0xFF):          # the low byte is the count (LEGO Pirates: 0x0601 = 1)
                key = self._buffer(c, 'vertex', refs)
                part['streams'].append((key, c.u32()))
            short = c.u32()
            if short and self.tail != 40:
                raise MeshError(f'unexpected field at {c.p - 4:#x}')
            part['index'] = self._buffer(c, 'index', refs)
            part['first_index'], part['index_count'], part['first_vertex'] = c.u32(), c.u32(), c.u32()
            if short:
                # LEGO Pirates' short record: the vertex count in place of the zero, nothing after the indices
                # (they draw from position-only buffers: depth / shadow copies, not shown)
                part['vertex_count'], part['palette'], part['morphs'], part['short'] = short, [], [], True
                self.serial += 1
                self.parts.append(part)
                continue
            c.u16()
            part['vertex_count'] = c.u32()
            c.take(4)
            n = c.u32()
            at_pal = c.take(n)
            part['palette'] = [b for b in data[at_pal:at_pal + n] if b != 0xFF]
            self.serial += bool(n)
            morphs = []
            while True:
                flag = c.u32()
                if flag == 0:
                    break
                if flag != 1:
                    raise MeshError(f'morph table at {c.p - 4:#x}')
                morphs.append(c.u32())
            # morph targets (facial expressions): per target u32 dense count; dense = that many vec3 and 21
            # bytes; sparse = 13 bytes, u32 size, runs {u32 count, count vec3} (a last count without data: the
            # remaining vertices do not move), u32 n, n * u32. Kept as byte ranges; offsets read in morphs().
            part['morphs'] = []
            for target in morphs:
                rec = c.p
                dense = c.u32()
                if dense:
                    c.take(dense * 12 + 21)
                    part['morphs'].append((target, 'dense', rec + 4, dense))
                else:
                    c.take(13)
                    size = c.u32()
                    at_runs = c.take(size)
                    n = c.u32()
                    c.take(4 * n)
                    part['morphs'].append((target, 'runs', at_runs, size))
                    self.serial += bool(n)       # a target with an index list counts one record more
                self.serial += 2
            if self.tail == 40 and len(self.parts) + 1 < count:
                # LEGO Pirates: the tail is 40 bytes or longer (not understood); the next part is the first
                # place after it that reads as a part start
                q = c.p + 40
                while q < c.p + 400 and not self._part_start(q):
                    q += 4
                if not self._part_start(q):
                    raise MeshError(f'next part not found after {c.p:#x}')
                c.p = q
            else:
                c.take(self.tail)
            # a full record counts one record in LEGO Batman 2, none in LEGO Pirates (measured on shared
            # buffer references); a short one counts one
            self.serial += 0 if self.tail == 40 else 1
            self.parts.append(part)
        self.end = c.p
        # buffer references count every buffer-like record read; find the base that makes them all fit
        if refs:
            bases = None
            for absolute, kind, _slot in refs:
                cands = {absolute - k for k, b in self.buffers.items() if b['kind'] == kind and absolute >= k}
                bases = cands if bases is None else bases & cands
            if not bases or len(bases) != 1:
                raise MeshError('buffer references do not resolve')
            base = bases.pop()
            for absolute, kind, slot in refs:
                slot[0] = absolute - base

    def _part_start(self, q):
        """Whether a part record (LEGO Pirates) starts at q: stream count 1 to 4, then a new vertex buffer with a
        valid descriptor list, or a reference (0xC000nnnn, 1)."""
        d = self.d
        if q + 32 > len(d):
            return False
        ns, tok = struct.unpack_from('>II', d, q)
        if not (1 <= ns & 0xFF <= 4 and ns < 0x10000):
            return False
        if tok & 0xFFFF0000 == 0xC0000000:
            return struct.unpack_from('>I', d, q + 8)[0] == 1
        if tok != 1:
            return False
        flags, count, nd = struct.unpack_from('>III', d, q + 8)
        if not (0 < count < 1 << 20 and 1 <= nd <= 12):
            return False
        desc = [tuple(d[q + 20 + 3 * i:q + 23 + 3 * i]) for i in range(nd)]
        return all(a < 16 and t in SIZES and o < 64 for a, t, o in desc) and desc[0][2] == 0

    def _buffer(self, c, kind, refs):
        token = c.u32()
        if token & 0xFFFF0000 == 0xC0000000:
            if c.u32() != 1:
                raise MeshError(f'buffer reference at {c.p - 8:#x}')
            slot = [None]
            refs.append((token & 0xFFFF, kind, slot))
            return slot
        if token != 1:
            raise MeshError(f'buffer token {token:#x} at {c.p - 4:#x}')
        flags, count = c.u32(), c.u32()
        b = {'kind': kind, 'count': count, 'flags': flags}
        if kind == 'vertex':
            if not self.old:
                if self.d[c.p:c.p + 4] != b'DXTV':
                    raise MeshError('vertex buffer without DXTV')
                c.p += 8
            n = c.u32()
            b['desc'] = [tuple(self.d[c.p + 3 * i:c.p + 3 * i + 3]) for i in range(n)]
            c.p += 3 * n
            if not self.old:
                c.take(6)
            b['stride'] = max(o + SIZES[t] for _a, t, o in b['desc'])
            b['start'] = c.take(count * b['stride'])
        else:
            b['stride'] = c.u32()
            b['start'] = c.take(count * b['stride'])
        key = self.serial
        self.buffers[key] = b
        self.serial += 1
        return [key]

    def geometry(self, i):
        """(positions, normals, uvs, colours, triangles, skin) of part i; skin: per vertex [(bone, weight)]."""
        d, part = self.d, self.parts[i]
        attrs = {}
        for key, off in part['streams']:
            b = self.buffers[key[0]]
            base = b['start'] + off + part['first_vertex'] * b['stride']
            for a, t, o in b['desc']:
                attrs[a] = (base + o, t, b['stride'])
        n = part['vertex_count']

        def col(a):
            if a not in attrs:
                return None
            at, t, stride = attrs[a]
            out = []
            for v in range(n):
                p = at + v * stride
                if t in (2, 3, 4):
                    out.append(struct.unpack_from(f'>{t}f', d, p))
                elif t == 5:
                    out.append(struct.unpack_from('>2e', d, p))
                elif t == 6:
                    out.append(struct.unpack_from('>4e', d, p))
                else:
                    out.append(tuple(d[p:p + 4]))
            return out

        pos = [v[:3] for v in col(0)]
        nrm = col(1)
        if nrm and attrs[1][1] in (7, 8, 9):
            nrm = [tuple((x - 127.5) / 127.5 for x in v[:3]) for v in nrm]
        uv = col(5)
        colour = col(2)
        skin = None
        if 9 in attrs and 10 in attrs:
            idx, wts = col(9), col(10)
            pal = part['palette']
            skin = []
            for ii, ww in zip(idx, wts):
                acc = {}
                for k in range(4):
                    if ww[k] and ii[k] < len(pal):
                        acc[pal[ii[k]]] = acc.get(pal[ii[k]], 0) + ww[k]
                tot = sum(acc.values()) or 1
                skin.append(sorted(((b, w / tot) for b, w in acc.items()), key=lambda x: -x[1]))
        ib = self.buffers[part['index'][0]]
        fmt = '>' + ('H' if ib['stride'] == 2 else 'I') * part['index_count']
        idx = struct.unpack_from(fmt, d, ib['start'] + part['first_index'] * ib['stride'])
        tris = [idx[k:k + 3] for k in range(0, len(idx), 3)]
        return pos, nrm, uv, colour, tris, skin


def morph_offsets(section, part_index):
    """{target id: [(dx, dy, dz)] per vertex} of a part's blend shapes (facial expressions).
    Run-length records are 16 bytes: u32 vertex count, 3f offset shared by those vertices."""
    d, part = section.d, section.parts[part_index]
    n = part['vertex_count']
    out = {}
    for target, kind, at, size in part.get('morphs', ()):
        offs = []
        if kind == 'dense':
            offs = [struct.unpack_from('>3f', d, at + 12 * i) for i in range(size)]
        else:
            for p in range(at, at + size, 16):
                count = struct.unpack_from('>I', d, p)[0]
                offs.extend([struct.unpack_from('>3f', d, p + 4)] * count)
        if len(offs) == n:
            out[target] = offs
    return out
