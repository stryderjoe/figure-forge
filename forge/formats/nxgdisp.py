"""Display lists (DISP) of TT's newer models, big-endian, non-DX layout: LEGO Batman 2 (DISP 7 to 14) and LEGO Marvel
Super Heroes (16 to 23).

    'PSID' u32 version, u32 n, n bytes (the source path), 'ROTV' u32 count, count * {u8 op, u8 flags, u32 value}
    'ROTV' u32 clip count, per clip: u16, u32 m, m * u32 material, u32 k, k * u32 command
    then several 'ROTV' arrays, one of which is the specials (named model instances), each
        u32 name (into the name table), 16f matrix, ..., u32 clip at +180, u32 flags, u32 range count,
        range count * f32 LOD thresholds          (204 + 4 * ranges bytes)
Commands 0x80 and 0xB3 draw the mesh part numbered by their value; the others set state.
"""
import struct


class DispError(ValueError):
    pass


def read(d, part_count):
    at = d.find(b'PSID')
    if at < 0:
        raise DispError('no display section')
    version, n = struct.unpack_from('>II', d, at + 4)
    p = at + 12 + n
    if d[p:p + 4] != b'ROTV':
        raise DispError('display commands not found')
    nc = struct.unpack_from('>I', d, p + 4)[0]
    p += 8
    commands = [struct.unpack_from('>BBI', d, p + 6 * i) for i in range(nc)]
    p += 6 * nc
    if d[p:p + 4] != b'ROTV':
        raise DispError('clip table not found')
    ncl = struct.unpack_from('>I', d, p + 4)[0]
    p += 8
    clips = []
    for _ in range(ncl):
        p += 2
        m = struct.unpack_from('>I', d, p)[0]
        mats = struct.unpack_from(f'>{m}I', d, p + 4)
        p += 4 + 4 * m
        k = struct.unpack_from('>I', d, p)[0]
        items = struct.unpack_from(f'>{k}I', d, p + 4)
        p += 4 + 4 * k
        if m != k or any(i >= nc for i in items):
            raise DispError('bad clip')
        clips.append(list(zip(mats, items)))
    names = d.find(b'LBTN')
    names = names + 12 if names >= 0 else None
    end = d.find(b'TROP', p)
    end = len(d) if end < 0 else end
    q = p
    while True:
        q = d.find(b'ROTV', q, end)
        if q < 0:
            raise DispError('specials not found')
        specials = _specials(d, q, end, names, clips, commands, part_count)
        if specials is not None:
            return {'version': version, 'commands': commands, 'clips': clips, 'specials': specials}
        q += 4


def _specials(d, q, end, names, clips, commands, part_count):
    count = struct.unpack_from('>I', d, q + 4)[0]
    if not 0 < count < 4096:
        return None
    p = q + 8
    out = []
    for i in range(count):
        if p + 208 > end:
            return None
        off = struct.unpack_from('>I', d, p)[0]
        clip, flags, ranges = struct.unpack_from('>3I', d, p + 180)
        if ranges > 8 or (clip >= len(clips) and clip != 0xFFFFFFFF):
            return None
        if names is not None:
            e = d.find(b'\0', names + off, names + off + 256)
            if e < 0:
                return None
            name = d[names + off:e].decode('latin1')
        else:
            name = f'special{i}'
        matrix = struct.unpack_from('>16f', d, p + 4)
        parts = []
        if clip != 0xFFFFFFFF:
            for mat, ci in clips[clip]:
                op, _fl, value = commands[ci]
                if op in (0x80, 0xB3):
                    if value >= part_count:
                        return None
                    parts.append((value, mat))
        out.append({'name': name, 'matrix': matrix, 'clip': clip, 'flags': flags, 'parts': parts})
        p += 204 + 4 * ranges
    return out
