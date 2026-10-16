"""TT's "Deflate_v1.0" streams (animation packs and other files of LEGO Batman 2 onwards).

Format (as documented publicly for QuickBMS's TT games script): 32 bytes "Deflate_v1.0" padded with zeros,
u32 unpacked size (little-endian), then a DEFLATE bit stream (RFC 1951) with two differences: the block types
for "stored" and "dynamic Huffman" are swapped (0 = dynamic, 2 = stored), and a stored block has only its u16
length (no one's-complement copy). Our own decoder.
"""
import struct

MAGIC = b'Deflate_v1.0'.ljust(32, b'\0')

LBASE = (3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195,
         227, 258)
LBITS = (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0)
DBASE = (1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
         4097, 6145, 8193, 12289, 16385, 24577)
DBITS = (0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13)
ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)


class DeflateError(ValueError):
    pass


def is_packed(data):
    return data[:32] == MAGIC


def _table(lengths):
    """Lookup table over the next `width` bits (LSB first): entry = (symbol, code length)."""
    width = max(lengths) if any(lengths) else 1
    table = [None] * (1 << width)
    code = 0
    for n in range(1, 16):
        for sym, ln in enumerate(lengths):
            if ln != n:
                continue
            rev = int(f'{code:0{n}b}'[::-1], 2)
            for fill in range(rev, 1 << width, 1 << n):
                table[fill] = (sym, n)
            code += 1
        code <<= 1
    return table, width


class _Bits:
    __slots__ = ('d', 'pos', 'buf', 'n')

    def __init__(self, d, pos):
        self.d, self.pos, self.buf, self.n = d, pos, 0, 0

    def need(self, k):
        while self.n < k:
            b = self.d[self.pos] if self.pos < len(self.d) else 0
            self.pos += 1
            self.buf |= b << self.n
            self.n += 8

    def take(self, k):
        if k == 0:
            return 0
        self.need(k)
        v = self.buf & ((1 << k) - 1)
        self.buf >>= k
        self.n -= k
        return v

    def sym(self, tab):
        table, width = tab
        self.need(width)
        hit = table[self.buf & ((1 << width) - 1)]
        if hit is None:
            raise DeflateError('bad Huffman code')
        s, ln = hit
        self.buf >>= ln
        self.n -= ln
        return s

    def align(self):
        drop = self.n % 8
        self.buf >>= drop
        self.n -= drop


FIXED = None


def decompress(data):
    global FIXED
    if not is_packed(data):
        raise DeflateError('not a TT deflate stream')
    size = struct.unpack_from('<I', data, 32)[0]
    out = bytearray()
    bits = _Bits(data, 36)
    final = 0
    while not final:
        final = bits.take(1)
        kind = bits.take(2)
        if kind == 2:                                   # stored (TT's number)
            bits.align()
            n = bits.take(16)
            for _ in range(n):
                out.append(bits.take(8))
            continue
        if kind == 1:
            if FIXED is None:
                FIXED = (_table([8] * 144 + [9] * 112 + [7] * 24 + [8] * 8), _table([5] * 30))
            lit, dist = FIXED
        elif kind == 0:                                 # dynamic (TT's number)
            hlit, hdist, hclen = bits.take(5) + 257, bits.take(5) + 1, bits.take(4) + 4
            cl = [0] * 19
            for i in range(hclen):
                cl[ORDER[i]] = bits.take(3)
            ct = _table(cl)
            lens = []
            while len(lens) < hlit + hdist:
                s = bits.sym(ct)
                if s < 16:
                    lens.append(s)
                elif s == 16:
                    lens.extend([lens[-1]] * (3 + bits.take(2)))
                elif s == 17:
                    lens.extend([0] * (3 + bits.take(3)))
                else:
                    lens.extend([0] * (11 + bits.take(7)))
            lit, dist = _table(lens[:hlit]), _table(lens[hlit:hlit + hdist])
        else:
            raise DeflateError('reserved block type')
        while True:
            s = bits.sym(lit)
            if s < 256:
                out.append(s)
            elif s == 256:
                break
            else:
                s -= 257
                length = LBASE[s] + bits.take(LBITS[s])
                d = bits.sym(dist)
                distance = DBASE[d] + bits.take(DBITS[d])
                if distance > len(out):
                    raise DeflateError('match before the start')
                start = len(out) - distance
                if distance >= length:
                    out += out[start:start + length]
                else:
                    for k in range(length):
                        out.append(out[start + k])
        if len(out) > size:
            raise DeflateError('more data than announced')
    if len(out) != size:
        raise DeflateError(f'unpacked {len(out)} bytes, expected {size}')
    return bytes(out)
