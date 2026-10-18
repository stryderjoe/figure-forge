"""Edit a classic TT model (.ghg, NU20) and write it back: LEGO Batman 2008, LEGO Indiana Jones 1 and LEGO Star
Wars: The Complete Saga.

What makes adding things possible (measured on the three games' character models, 2026-10-15):
- The NU20 block is a run of chunks: HEAD, NTBL, (TREF, TST0), MS00, INID, FDNS, BNDS, CDAT (holding DISP),
  (TREF, TST0), VBIB, SALI, DYNO, GSNH, PNTR. PNTR is always last.
- Links inside the block are self-relative i32s (0 = none). PNTR lists every place that holds one:
  'PNTR', u32 chunk size (12 + 4n), u32 n, then n self-relative i32s, each aimed at a link. HEAD's first word
  is PNTR's position - 0x10; its second is GSNH's scene header - 0x20 (the reader's `gsnh`).
- After the block comes the payload. Newer files (Batman, Indy): u32 payload size, every texture's DDS back to
  back, u16 count + {u32 size, bytes} vertex buffers, u16 count + {u32 size, bytes} index buffers.
  Star Wars files keep the same block but put it LAST: u32 = payload length, payload, block; that payload is
  u16 texture count, per texture {6 u32: width, height, levels, type, ?, size} + DDS, then the same buffers.
New structures go at the end of GSNH (its size grows), and their links are added to PNTR.
"""
import struct

MAGIC = b'NU20'


class EditError(ValueError):
    pass


class ModelFile:
    def __init__(self, data):
        data = bytes(data)
        self.old_layout = data[:4] != MAGIC
        if self.old_layout:
            (at,) = struct.unpack_from('<I', data, 0)
            if data[at + 4:at + 8] != MAGIC:
                raise EditError('not an NU20 model')
            payload, block = data[4:at + 4], data[at + 4:]
            # scenes (.gsc) end with 128 more bytes after the block: kept as they are
            size = -struct.unpack_from('<i', block, 4)[0]
            block, self.after = block[:size], block[size:]
        else:
            (neg,) = struct.unpack_from('<i', data, 4)
            size = -neg
            block, payload = data[:size], data[size:]
            # scenes (.gsc) end with 128 more bytes after the payload its size word counts: kept as they are
            (total,) = struct.unpack_from('<I', payload, 0)
            payload, self.after = payload[:4 + total], payload[4 + total:]
        self.block = bytearray(block)
        self._chunks()
        self._payload(payload)
        self.new = bytearray()
        self.new_ptrs = []

    # -- the block ----------------------------------------------------------------------------------
    def _chunks(self):
        b = self.block
        neg = struct.unpack_from('<i', b, 4)[0]
        if -neg != len(b):
            raise EditError(f'block size {-neg} but {len(b)} bytes')
        self.pntr_at = struct.unpack_from('<I', b, 0x18)[0] + 0x10
        self.gsnh_head = struct.unpack_from('<I', b, 0x1C)[0] + 0x20
        if b[self.pntr_at:self.pntr_at + 4] != b'PNTR':
            raise EditError('PNTR not where HEAD says')
        size, n = struct.unpack_from('<II', b, self.pntr_at + 4)
        # PNTR is last. The block ends on 16 bytes: the zeros that make it so are either counted in PNTR's size
        # (inner) or follow it (outer); both are met
        self.inner_pad = size - (12 + 4 * n)
        self.block_pad = len(b) - (self.pntr_at + size)
        if not 0 <= self.inner_pad < 16 or not 0 <= self.block_pad < 16 or any(b[self.pntr_at + 12 + 4 * n:]):
            raise EditError('PNTR is not the last chunk')
        first = self.pntr_at + 12
        self.ptrs = [first + 4 * i + struct.unpack_from('<i', b, first + 4 * i)[0] for i in range(n)]
        # GSNH: the chunk that ends where PNTR starts
        g = self.pntr_at - 4
        while g >= 0x10:
            if b[g:g + 4] == b'GSNH' and g + struct.unpack_from('<I', b, g + 4)[0] == self.pntr_at:
                break
            g -= 4
        else:
            raise EditError('no GSNH chunk before PNTR')
        self.gsnh_chunk = g
        self.ptr_set = set(self.ptrs)

    def _payload(self, p):
        self.textures = []      # [header bytes (old layout: 24) or b'', DDS bytes]
        pos = 0
        if self.old_layout:
            # the count is of texture entries; a cubemap has one record and DDS for itself and its five
            # other faces (six entries)
            (count,) = struct.unpack_from('<H', p, 0)
            self.texture_count = count
            pos, i = 2, 0
            while i < count:
                head = p[pos:pos + 24]
                size = struct.unpack_from('<I', head, 20)[0]
                dds = p[pos + 24:pos + 24 + size]
                self.textures.append([head, dds])
                pos += 24 + size
                i += 6 if len(dds) >= 128 and struct.unpack_from('<I', dds, 112)[0] & 0xFE00 else 1
        else:
            (total,) = struct.unpack_from('<I', p, 0)
            if total != len(p) - 4:
                raise EditError('payload size word does not match')
            pos = 4
            # texture sizes come from the texture records in the block (scene header + 4, size at +0x44)
            for size in self.texture_sizes():
                self.textures.append([b'', p[pos:pos + size]])
                pos += size
        self._loaded_textures = len(self.textures)
        self.vertex_buffers, pos = self._buffers(p, pos)
        self.index_buffers, pos = self._buffers(p, pos)
        self.payload_tail = p[pos:]          # a few files carry spare bytes at the end: kept as they are

    @staticmethod
    def _buffers(p, pos):
        (n,) = struct.unpack_from('<H', p, pos)
        pos += 2
        out = []
        for _ in range(n):
            (size,) = struct.unpack_from('<I', p, pos)
            out.append(p[pos + 4:pos + 4 + size])
            pos += 4 + size
        return out, pos

    def texture_records(self):
        g = self.gsnh_head
        count = self.u32(g)
        table = self.ptr(g + 4)
        return [self.ptr(table + 4 * i) for i in range(count)]

    def texture_sizes(self):
        return [self.u32(e + 0x44) for e in self.texture_records()]

    # -- reading and writing anywhere (the block, or the new area that will follow GSNH) ------------------
    def _where(self, at, n):
        if at + n <= self.pntr_at:
            return self.block, at
        off = at - self.pntr_at
        if 0 <= off and off + n <= len(self.new):
            return self.new, off
        raise EditError(f'address {at:#x} is outside the model')

    def read(self, at, n):
        buf, off = self._where(at, n)
        return bytes(buf[off:off + n])

    def write(self, at, data):
        buf, off = self._where(at, len(data))
        buf[off:off + len(data)] = data

    def u32(self, at):
        return struct.unpack('<I', self.read(at, 4))[0]

    def i32(self, at):
        return struct.unpack('<i', self.read(at, 4))[0]

    def set_u32(self, at, v):
        self.write(at, struct.pack('<I', v & 0xFFFFFFFF))

    def ptr(self, at):
        v = self.i32(at)
        return at + v if v else 0

    def set_ptr(self, at, target):
        """A link at `at` to `target` (0 = none), registered in PNTR."""
        self.write(at, struct.pack('<i', target - at if target else 0))
        if at not in self.ptr_set:
            self.ptr_set.add(at)
            self.new_ptrs.append(at)

    def alloc(self, size, align=16):
        """Room for a new structure at the end of GSNH; returns its address (the block's own numbering)."""
        pad = (-(self.pntr_at + len(self.new))) % align
        self.new += b'\0' * pad
        at = self.pntr_at + len(self.new)
        self.new += b'\0' * size
        return at

    def clone(self, src, size, align=16):
        """Copy a structure; every link inside it is copied too (aimed at the same targets) and registered."""
        at = self.alloc(size, align)
        self.write(at, self.read(src, size))
        for p in [p for p in self.ptr_set if src <= p < src + size] + [p for p in self.new_ptrs if src <= p < src + size]:
            v = self.i32(p)
            self.set_ptr(at + (p - src), p + v if v else 0)
        return at

    # -- writing ---------------------------------------------------------------------------------------
    def tobytes(self):
        new = bytes(self.new)
        new += b'\0' * ((-(self.pntr_at + len(new))) % 16)
        grow = len(new)
        locs = self.ptrs + self.new_ptrs
        pntr_at = self.pntr_at + grow
        first = pntr_at + 12
        body = 12 + 4 * len(locs)
        pad = (-(pntr_at + body)) % 16 if (self.inner_pad or self.block_pad) else 0
        inner = pad if self.inner_pad else 0
        table = bytearray(b'PNTR' + struct.pack('<II', body + inner, len(locs)))
        for i, loc in enumerate(locs):
            table += struct.pack('<i', loc - (first + 4 * i))
        block = bytearray(self.block[:self.pntr_at]) + new + table + b'\0' * pad
        struct.pack_into('<i', block, 4, -len(block))
        struct.pack_into('<I', block, 0x18, pntr_at - 0x10)
        size = struct.unpack_from('<I', block, self.gsnh_chunk + 4)[0]
        struct.pack_into('<I', block, self.gsnh_chunk + 4, size + grow)
        payload = bytearray()
        if self.old_layout:
            count = self.texture_count + len(self.textures) - self._loaded_textures
            payload += struct.pack('<H', count)
            for head, dds in self.textures:
                head = bytearray(head)
                struct.pack_into('<I', head, 20, len(dds))
                payload += head + dds
        else:
            for dds in (t[1] for t in self.textures):
                payload += dds
        for bufs in (self.vertex_buffers, self.index_buffers):
            payload += struct.pack('<H', len(bufs))
            for buf in bufs:
                payload += struct.pack('<I', len(buf)) + buf
        payload += self.payload_tail
        if self.old_layout:
            return struct.pack('<I', len(payload)) + bytes(payload) + bytes(block) + self.after
        return bytes(block) + struct.pack('<I', len(payload)) + bytes(payload) + self.after
