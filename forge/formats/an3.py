"""Reader for the original game's character animation files (*_pc.an3), used as STAND-IN animation.

Layout (header fields as documented by the community tool BactaTank Classic / EasyAN3; the key packing and
channel meaning were worked out here against the skeleton, see GAME_STUDY.md):
    u32 tag ('8INA' / '6INA' in the 2008 games, '4INA' in LEGO Star Wars: TCS), u16 nodes, u16 frames, u16 curve_group_size, u16 original_frames,
    u16 curve_count (9), u16 first_frame, u8 end_frames, u8 short_count, u8 fixed_up, 5 pad,
    u32 frame_index_ptr, f32 const_base, f32 const_scale,
    u32 scales_mins_ptr, u32 constants_ptr, u32 key_types_ptr, u32 keys_ptr, u32 curve_set_flags_ptr, u32 tangent_keys_ptr
    key types : nodes * 9 u16. Per node: translation x y z, rotation x y z (radians), scale x y z.
                value >= 16 -> constant number (value - 16);  value 6 -> animated curve (in order of appearance)
    constants : u16 each; real value = const_base + u16 * const_scale
    scales/mins: per curve f32 scale, f32 min
    keys      : u32 per curve per 4-frame segment, stored segment-major (all curves for segment 0, then 1, ...).
                low byte = anchor (0..255); four 6-bit fields = the segment's four frames, each a blend 0..63
                from this anchor to the next segment's anchor.  value = min + scale * blended_anchor

    python tools/an3.py info <file.an3>
"""
import math
import struct
import sys


class An3:
    """One animation block. Handles both generations with the same code:
       LEGO Batman 1 (.an3): little-endian, tag '8INA' / '6INA', 9 channels per node (t xyz, r xyz, s xyz)
       LEGO Marvel Super Heroes (.an4): big-endian, tag 'ANID', the channel count is in the header (6 seen:
       t xyz, r xyz; other counts appear on special one-node blocks), several blocks per file (see An4Set).
    Pointers inside a block are relative to the block's start."""

    def __init__(self, path, data=None, base=0, name=None):
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        d = data[base:]
        self._base = base
        # Unrelocated block (tag '5INA'; one file known, a level's own animation in LEGO Star Wars: TCS,
        # LEVELS\EPISODE_IV\DEATHSTARESCAPE\DEATHSTARESCAPE_INTRO\DEATHSTARESCAPE_INTRO.AN3): a u32 in front of
        # the tag gives the offset of a fix-up table (count, then the places of the header's seven pointers),
        # and each pointer is relative to its own place instead of to the block's start. 0 = no pointer.
        self.self_relative = d[1:4] != b"INA" and d[:3] != b"ANI" and d[5:8] == b"INA"
        if self.self_relative:
            d = d[4:]
        self.tag, self.name = d[:4], name
        self.big_endian = d[:3] == b"ANI"
        # the newer block layout (ANI-B / ANI-D): LEGO Batman 2 stores some little-endian ('BINA')
        self.newer = self.big_endian or d[:4] in (b"BINA", b"DINA")
        e = ">" if self.big_endian else "<"
        self.nodes, self.frames, group, self.original_frames, self.curve_count = struct.unpack_from(e + "5H", d, 4)
        self.channels = self.curve_count if self.newer else 9
        cbase, cscale = struct.unpack_from(e + "ff", d, 28)
        sm, cp, ktp, kp, csf = struct.unpack_from(e + "5I", d, 36)
        # LEGO Pirates (ANI9): each pointer is relative to its own place, like the unrelocated block below
        # (measured on Idle5: key types, curve scales, keys and node flags then meet end to end)
        if self.self_relative or d[:4] == b"ANI9":
            sm, cp, ktp, kp, csf = [v + 36 + 4 * i if v else 0 for i, v in enumerate((sm, cp, ktp, kp, csf))]
        raw_types = struct.unpack_from(f"{e}{self.nodes * self.channels}H", d, ktp)
        # ANI-D / ANI-B (LEGO Batman 2, LEGO Marvel, LEGO Batman 3): bit 15 of a key type marks a stepped curve,
        # the clip's frame count is at +22 (the header's second count is the number of KEYS) and a frame maps
        # to key position (frame - first) * ratio; a node's flags switch its channels on (bit 2 translation,
        # bit 1 rotation, bit 8 the scale channels of 9- and 10-channel blocks); constants are base + type * scale
        # (header flag 0x20) or a float table after the integer constants. Other blocks read as before.
        self.modern = d[:4] in (b"ANID", b"ANIB", b"DINA", b"BINA")
        self.stepped = [bool(kt & 0x8000) for kt in raw_types] if self.modern else [False] * len(raw_types)
        self.key_types = tuple(kt & 0x7FFF for kt in raw_types) if self.modern else raw_types
        self.keys = self.frames
        self.ratio, self.first, self.flags, self.int_constants = 1.0, 0.0, 0, 0
        if self.modern:
            self.int_constants, self.flags = d[17], d[19]
            (self.frames,) = struct.unpack_from(e + "H", d, 22)
            self.ratio, self.first = struct.unpack_from(e + "ff", d, 72)
        self.node_flags = list(d[csf:csf + self.nodes]) if csf else []
        # a face block (the newer games' 'BSA' block: one node, one channel per facial target, 37 in LEGO Batman
        # 2 and Marvel): every channel is a weight, none is switched off by the node's flags
        self.face = self.modern and self.nodes == 1 and self.channels > 10
        self.active = []
        for node in range(self.nodes):
            fl = self.node_flags[node] if node < len(self.node_flags) else 0xFF
            for ch in range(self.channels):
                if not self.modern or self.channels == 1 or self.face:
                    on = True
                elif ch < 3:
                    on = bool(fl & 2)
                elif ch < 6:
                    on = bool(fl & 1)
                elif self.channels in (9, 10) and ch < 9:
                    on = bool(fl & 8)
                else:
                    on = True
                self.active.append(on)
        # Key slots per 4-frame segment. A type 6 curve takes one u32 slot; a type 7 curve (.an4 only) takes two:
        # a 16-bit anchor and three words whose low 12 bits are the blends of the segment's first three frames;
        # the fourth frame's blend is made of the top 4 bits of those three words.
        self.slots = group // 4
        self.curve_slot, self.curve_kind = [], []
        slot = 0
        for kt, on in zip(self.key_types, self.active):
            if kt in (6, 7) and on:
                self.curve_slot.append(slot)
                self.curve_kind.append(kt)
                slot += 2 if kt == 7 else 1
        self.approximate = 7 in self.key_types and not self.modern
        self.moving = len(self.curve_slot)
        n_const = (ktp - cp) // 2
        self.const_base, self.const_scale = cbase, cscale
        self.constants = [cbase + v * cscale for v in struct.unpack_from(f"{e}{n_const}H", d, cp)]
        self.curve_params = [struct.unpack_from(e + "ff", d, sm + 8 * i) for i in range(self.moving)]
        if self.slots:
            self.segments = ((csf - kp) // 4) // self.slots if csf > kp else (self.frames + 3) // 4 + 1
        else:
            self.segments = 0
        self.key_words = struct.unpack_from(f"{e}{self.slots * self.segments}I", d, kp) if self.slots else ()
        self.float_pool = cp + ((self.int_constants * 2 + 3) & ~3)
        self._d, self._e = d, e
        self._stepped_curves = [st for kt, on, st in zip(self.key_types, self.active, self.stepped) if kt in (6, 7) and on]
        # Key types. Both generations: 6 = animated curve; >= 16 = a constant.
        #   .an3: constant number (type - 16) in the constants table.
        #   ANI-D / ANI-B: see above; 14 = fixed at 0, 15 = fixed at 1.
        allowed = (6, 7, 14, 15) if self.newer else (6,)
        unknown = sorted({v for v in self.key_types if v < 16 and v not in allowed})
        if unknown:
            raise ValueError(f"{path}: unhandled key types {unknown}")

    def _word(self, seg, slot, pair=False):
        at = min(seg, self.segments - 1) * self.slots + slot
        if not pair:
            return self.key_words[at]
        return struct.unpack(self._e + "4H", struct.pack(self._e + "2I", self.key_words[at], self.key_words[at + 1]))

    def curve_at_key(self, index, key):
        """A curve's value at a whole key position."""
        scale, low = self.curve_params[index]
        seg, quarter, slot = key // 4, key % 4, self.curve_slot[index]
        if self.curve_kind[index] == 7 and self.modern:
            words, after = self._word(seg, slot, True), self._word(seg + 1, slot, True)
            a, nxt = words[0], after[0]
            if quarter < 3:
                blend = (words[1 + quarter] & 0xFFF) / 4095.0
            else:
                blend = ((words[1] >> 12) | ((words[2] >> 12) << 4) | ((words[3] >> 12) << 8)) / 4095.0
        else:
            word = self._word(seg, slot)
            a, nxt = word & 0xFF, self._word(seg + 1, slot) & 0xFF
            blend = ((word >> (8 + 6 * quarter)) & 63) / 63.0
        return low + scale * (a + (nxt - a) * blend)

    def curve(self, index, frame):
        if not self.modern:
            return self.curve_at_key(index, frame)
        pos = max(0.0, min(self.keys - 1.0, (frame - self.first) * self.ratio))
        whole, frac = int(pos), pos - int(pos)
        v0 = self.curve_at_key(index, whole)
        if not frac or self._stepped_curves[index]:
            return v0
        return v0 + (self.curve_at_key(index, min(whole + 1, self.keys - 1)) - v0) * frac

    def constant(self, kt):
        if not self.newer:
            return self.constants[kt - 16]
        if not self.modern:
            return self.const_base + (kt - 16) * self.const_scale       # LEGO Pirates (ANI9), as before
        if self.flags & 0x20:
            return self.const_base + kt * self.const_scale
        return struct.unpack_from(self._e + "f", self._d, self.float_pool + (kt - 16) * 4)[0]

    def pose(self, frame):
        """Per node: (tx, ty, tz, rx, ry, rz[, sx, sy, sz]) at a frame (as many values as the block has channels).
        A channel its node switches off reads 0 (a scale channel 1)."""
        frame = max(0, min(frame, self.frames - 1))
        out, curve, i = [], 0, 0
        for node in range(self.nodes):
            values = []
            for ch in range(self.channels):
                kt, on = self.key_types[i], self.active[i]
                i += 1
                if not on and self.modern and self.channels in (9, 10) and 6 <= ch < 9:
                    values.append(1.0)          # scale switched off: the bone keeps its size
                elif not on or kt == 14:
                    values.append(0.0)
                elif kt >= 16:
                    values.append(self.constant(kt))
                elif kt == 15:
                    values.append(1.0)
                else:
                    values.append(self.curve(curve, frame))
                    curve += 1
            out.append(tuple(values))
        return out


class An4Set:
    """A LEGO Marvel .an4 file: a big-endian set of animation blocks (each read by An3) with a name list at
    the end. Blocks are found by their 'ANID' tag; the set's own header (version 13, total size, counts,
    per-block tables) is not fully decoded, so names are matched to blocks in order where the counts agree."""

    def __init__(self, path, data=None):
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.data = data
        self.version, self.size = struct.unpack_from(">II", self.data, 0)
        (names_at,) = struct.unpack_from(">I", self.data, 16)
        self.names = [n.decode("latin1") for n in self.data[names_at:].split(b"\0") if n]
        self.blocks, self.errors = [], []
        # 'ANID' in LEGO Marvel Super Heroes, 'ANIB' (same block layout) in LEGO Batman 2
        import re
        for m in re.finditer(rb"ANI[BD]|[BD]INA", self.data):
            pos = m.start()
            try:
                self.blocks.append(An3(path, self.data, pos))
            except Exception as ex:   # keep going: one odd block must not hide the others
                self.errors.append((pos, str(ex)))
        if len(self.names) == len(self.blocks) + len(self.errors):
            for block, name in zip(self.blocks, self.names):
                block.name = name

    def face(self):
        """The set's face block: the block right after a 'BSA' header (20 bytes; reversed when little-endian)
        that reads as a face block (one node, a weight per facial target) with its keys where they should be,
        or None. Channel i is the weight of the facial target numbered i in the models."""
        import re
        for m in re.finditer(rb"BSA\0|\0ASB", self.data):
            at = m.start() + 20
            block = next((b for b in self.blocks if b._base == at), None)
            if block is not None and block.face and block.moving == block.slots:
                return block
        return None


def euler_matrix(rx, ry, rz, order="xyz"):
    """3x3 rotation, row-vector convention (v' = v * M), applying the axes in the given order."""
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mats = {"x": [[1, 0, 0], [0, cx, sx], [0, -sx, cx]],
            "y": [[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]],
            "z": [[cz, sz, 0], [-sz, cz, 0], [0, 0, 1]]}
    m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for axis in order:
        b = mats[axis]
        m = [[sum(m[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    return m


if __name__ == "__main__" and len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)
elif __name__ == "__main__" and sys.argv[2].lower().endswith(".an4"):
    s = An4Set(sys.argv[2])
    print(f"{sys.argv[2]}: set version {s.version}, {len(s.blocks)} blocks, names {s.names}, unread blocks {s.errors}")
    for b in s.blocks:
        p0 = b.pose(0)
        print(f"  {b.name or '?':<14} nodes {b.nodes:3d} frames {b.frames:3d} channels {b.channels} animated curves {b.moving:3d} "
              f"constants {len(b.constants)} | node 1 frame 0: {[None if v is None else round(v, 3) for v in p0[min(1, b.nodes - 1)]][:6]}")
elif __name__ == "__main__":
    a = An3(sys.argv[2])
    print(f"{sys.argv[2]}: tag {a.tag!r} nodes {a.nodes} frames {a.frames} (original {a.original_frames}) "
          f"animated curves {a.moving} constants {len(a.constants)} segments {a.segments}")
    p0, p_mid = a.pose(0), a.pose(a.frames // 2)
    for node in (n for n in (0, 1, 2, 13) if n < a.nodes):
        print(f"  node {node}: frame 0 {[round(v, 3) for v in p0[node]]}")
        print(f"           mid     {[round(v, 3) for v in p_mid[node]]}")
