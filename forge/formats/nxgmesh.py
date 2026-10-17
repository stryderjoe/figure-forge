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
import math
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


def _streams(word):
    """The stream count of a part record: the low byte (LEGO Pirates: 0x0601 = 1), or its low two bits when the
    byte is larger (LEGO Pirates' faces: 0x25 = 1)."""
    low = word & 0xFF
    return low if low <= 4 else low & 3


class MeshSection:
    """Parts of the MESH section: each {'streams': [(buffer key, byte offset)], 'index': buffer key,
    'first_index', 'index_count', 'first_vertex', 'vertex_count', 'palette': [bone numbers]}."""

    def __init__(self, data):
        self.d = data
        at = data.find(b'HSEM')
        while at >= 0 and struct.unpack_from('>I', data, at + 4)[0] not in (46, 47, 48, 161, 169, 175):
            at = data.find(b'HSEM', at + 4)
        if at < 0:
            raise MeshError('no MESH section of a known version')
        c = C(data, at + 4)
        self.version = c.u32()
        self.old = self.version in (46, 47, 48)
        # LEGO Pirates' MESH 46 / 47 has the count without the marker, and 40-byte part tails (72 in LEGO Batman 2)
        self.tail, self.pirates = 72, False
        if data[c.p:c.p + 4] == b'ROTV':
            c.p += 4
        elif self.version in (46, 47):
            self.tail, self.pirates = 40, True
        elif self.version == 175:
            self.tail = 40                      # LEGO Batman 3: the count follows the version; 40-byte tails
        else:
            raise MeshError('MESH without ROTV')
        count = c.u32()
        self.buffers, self.parts, self.serial, refs = {}, [], 0, []
        # LEGO Pirates: the count is of full records; a full record's depth copy (a short record) follows it
        # uncounted (measured on super_minifig: 330 full and 318 short records up to 'LTMU')
        full_seen = 0
        while full_seen < count or (self.pirates and self.parts and not self.parts[-1].get('short')
                                    and self._short_at(c.p)):
            part = {'streams': [], 'at': c.p}
            if self.version == 175:
                part['lead'] = c.u32()          # LEGO Batman 3: one more word before the stream count (1 seen)
            for _s in range(_streams(c.u32())):
                key = self._buffer(c, 'vertex', refs)
                part['streams'].append((key, c.u32()))
            short = c.u32()
            if short and not self.pirates:
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
            full_seen += 1
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
            if self.version == 175 and morphs:
                # LEGO Batman 3: per target 'ROTV' 0, u32 2, u32 size (of what follows), run records ({u32 count,
                # vec3} as the older sparse targets) filling that size, then 'ROTV' n, n words (not needed).
                # Where that does not hold, the targets are skipped up to the next part start.
                read = self._morphs175(c.p, morphs)
                if read is not None:
                    part['morphs'], c.p = read
                    self.serial += 2 * len(morphs)
                    c.take(self.tail)
                    self.parts.append(part)
                    continue
                q = self._next_part175(c.p)
                c.p = q if q is not None else self._mesh_end175(c.p)
                self.serial += 2 * len(morphs)
                part['skipped_morphs'] = len(morphs)
                self.parts.append(part)
                continue
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
            if self.pirates and (full_seen < count or self._short_at(c.p + 40)):
                # LEGO Pirates: the tail is 40 bytes or longer (not understood); the next part is the first
                # place after it that reads as a part start
                q = c.p + 40
                while q < c.p + 400 and not self._part_start(q):
                    q += 4
                if not self._part_start(q):
                    raise MeshError(f'next part not found after {c.p:#x}')
                part['tail'] = q - c.p
                c.p = q
            else:
                c.take(self.tail)
            # a full record counts one record in LEGO Batman 2; in LEGO Pirates (and LB2's MESH 47) only one with
            # a 72-byte tail, which is every part with facial targets (measured: 12 files whose references needed
            # the fallback below now resolve with one base, among them the monkey, donkey and monkey cage); a
            # short one counts one
            # LEGO Batman 3 counts one record more per part (measured on shared buffer references)
            self.serial += (part.get('tail') == 72) if self.pirates else 2 if self.version == 175 else 1
            self.parts.append(part)
        self.end = c.p
        # buffer references count every buffer-like record read; find the base that makes them all fit
        if refs:
            bases = None
            for absolute, kind, _slot, _now in refs:
                cands = {absolute - k for k, b in self.buffers.items() if b['kind'] == kind and absolute >= k}
                bases = cands if bases is None else bases & cands
            if bases and len(bases) == 1:
                base = bases.pop()
                for absolute, kind, slot, _now in refs:
                    slot[0] = absolute - base
            else:
                self._resolve_by_kind(refs)
        # LEGO Pirates: the display list numbers the full records first, then the short depth copies (measured
        # on super_minifig: only so do the first skeleton's layers land on symmetric pairs of legs, arms and
        # mirrored hands, skinned bodies and rigid hips)
        self.parts = [p for p in self.parts if not p.get('short')] + [p for p in self.parts if p.get('short')]

    def _short_at(self, p):
        """LEGO Pirates: does a short record (a part's depth copy) start at p? A part start whose word after the
        streams (zero in a full record) is its vertex count."""
        if p + 40 > len(self.d) or not self._part_start(p):
            return False
        keep_buffers, keep_serial = dict(self.buffers), self.serial
        try:
            c = C(self.d, p)
            for _s in range(_streams(c.u32())):
                self._buffer(c, 'vertex', [])
                c.u32()
            return c.u32() != 0
        except (MeshError, struct.error):
            return False
        finally:
            self.buffers, self.serial = keep_buffers, keep_serial

    def _resolve_by_kind(self, refs):
        """When one base does not fit every reference (LEGO Pirates' and LEGO Batman 3's faces: records counted in
        ways not yet understood, or expressions skipped), references are resolved in groups: the offset that
        makes the most of them name an earlier buffer of their kind first (the most recent buffers on a tie),
        then the next offset for the rest, and so on."""
        for kind in ('vertex', 'index'):
            left = [r for r in refs if r[1] == kind]
            keys = sorted(k for k, b in self.buffers.items() if b['kind'] == kind)
            while left:
                votes = {}
                for absolute, _kind, _slot, now in left:
                    for k in keys:
                        if k < now and absolute >= k:
                            votes[absolute - k] = votes.get(absolute - k, 0) + 1
                if not votes:
                    raise MeshError('buffer references do not resolve')
                base = min(votes, key=lambda b: (-votes[b], b))
                done = []
                for r in left:
                    absolute, _kind, slot, now = r
                    k = absolute - base
                    if k in self.buffers and self.buffers[k]['kind'] == kind and k < now:
                        slot[0] = k
                        done.append(r)
                if not done:
                    raise MeshError('buffer references do not resolve')
                left = [r for r in left if r not in done]

    def _morphs175(self, p, targets):
        d = self.d
        out = []
        try:
            for target in targets:
                if d[p:p + 4] != b'ROTV':
                    return None
                size = struct.unpack_from('>I', d, p + 12)[0]       # counted from after itself
                if p + 16 + size > len(d) or size % 16 or size < 16:
                    return None
                # run records ({u32 count, vec3}) fill the size; the first run is not always a still one (its
                # offset can be set: checked against TTGames-Workshop's target reader)
                out.append((target, 'runs', p + 16, size))
                p = p + 16 + size
                if d[p:p + 4] != b'ROTV':
                    return None
                n = struct.unpack_from('>I', d, p + 4)[0]
                p += 8 + 4 * n
        except struct.error:
            return None
        if self._next_part175(p) not in (p + self.tail, None):
            return None
        return out, p

    def _next_part175(self, p):
        """The next LEGO Batman 3 part record after p: u32 1, a stream count of 1 to 4, then a new vertex buffer
        ('DXTV' 12 bytes on) or a reference (0xC000nnnn, 1)."""
        d = self.d
        limit = self._mesh_end175(p)
        q = d.find(b'\x00\x00\x00\x01', p)
        while 0 <= q < limit:
            if q % 1 == 0 and q + 32 <= len(d):
                ns, tok = struct.unpack_from('>II', d, q + 4)
                if 1 <= ns <= 4:
                    if tok == 1 and d[q + 20:q + 24] == b'DXTV':
                        return q
                    if tok & 0xFFFF0000 == 0xC0000000 and struct.unpack_from('>I', d, q + 12)[0] == 1:
                        return q
            q = d.find(b'\x00\x00\x00\x01', q + 1)
        return None

    def _mesh_end175(self, p):
        q = self.d.find(b'LTMU', p)
        return len(self.d) if q < 0 else q - 4

    def _part_start(self, q):
        """Whether a part record (LEGO Pirates) starts at q: stream count 1 to 4, then a new vertex buffer with a
        valid descriptor list, or a reference (0xC000nnnn, 1)."""
        d = self.d
        if q + 32 > len(d):
            return False
        ns, tok = struct.unpack_from('>II', d, q)
        if not (1 <= _streams(ns) <= 4 and ns < 0x10000):
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
            refs.append((token & 0xFFFF, kind, slot, self.serial))
            return slot
        if token != 1:
            raise MeshError(f'buffer token {token:#x} at {c.p - 4:#x}')
        flags, count = c.u32(), c.u32()
        b = {'kind': kind, 'count': count, 'flags': flags, 'at': c.p - 12}
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
        e = '<' if self.version == 175 else '>'          # LEGO Batman 3 (DX11) stores the data little-endian
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
                    out.append(struct.unpack_from(f'{e}{t}f', d, p))
                elif t == 5:
                    out.append(struct.unpack_from(e + '2e', d, p))
                elif t == 6:
                    out.append(struct.unpack_from(e + '4e', d, p))
                else:
                    out.append(tuple(d[p:p + 4]))
            return out

        pos = [v[:3] for v in col(0)]
        nrm = col(1)
        if nrm and attrs[1][1] in (7, 8, 9):
            nrm = [tuple((x - 127.5) / 127.5 for x in v[:3]) for v in nrm]
        uv = col(5)
        if uv and not all(math.isfinite(x) for t in uv for x in t):
            # a few models hold half-float NaNs in unused texture coordinates (LEGO Batman 3's faces)
            uv = [tuple(x if math.isfinite(x) else 0.0 for x in t) for t in uv]
        colour = col(2)
        if colour and self.version == 175:
            colour = [(c[2], c[1], c[0], c[3]) for c in colour]      # LEGO Batman 3 stores colours as BGRA
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
        fmt = e + ('H' if ib['stride'] == 2 else 'I') * part['index_count']
        idx = struct.unpack_from(fmt, d, ib['start'] + part['first_index'] * ib['stride'])
        tris = [idx[k:k + 3] for k in range(0, len(idx), 3)]
        return pos, nrm, uv, colour, tris, skin


def morph_offsets(section, part_index):
    """{target id: [(dx, dy, dz)] per vertex} of a part's blend shapes (facial expressions).
    Run-length records are 16 bytes: u32 vertex count, 3f offset shared by those vertices."""
    d, part = section.d, section.parts[part_index]
    n = part['vertex_count']
    out = {}
    for target, kind, at, size, *rest in part.get('morphs', ()):
        offs = [(0.0, 0.0, 0.0)] * rest[0] if rest else []
        if kind == 'dense':
            offs = [struct.unpack_from('>3f', d, at + 12 * i) for i in range(size)]
        else:
            for p in range(at, at + size, 16):
                count = struct.unpack_from('>I', d, p)[0]
                offs.extend([struct.unpack_from('>3f', d, p + 4)] * count)
        if len(offs) == n:
            out[target] = offs
    return out
