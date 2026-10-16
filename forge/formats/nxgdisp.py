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
    if d[at + 8:at + 12] == b'ROTV':
        p = at + 8                      # LEGO Batman 3 (DISP 32): no source path
    marked = d[p:p + 4] == b'ROTV'           # LEGO Pirates' DISP 7 / 8 has counts without the marker
    p += 4 if marked else 0
    nc_at = p
    nc = struct.unpack_from('>I', d, p)[0]
    p += 4
    commands = [struct.unpack_from('>BBI', d, p + 6 * i) for i in range(nc)]
    p += 6 * nc
    if marked:
        if d[p:p + 4] != b'ROTV':
            raise DispError('clip table not found')
        p += 4
    ncl = struct.unpack_from('>I', d, p)[0]
    p += 4
    clips, spans = [], []
    for _ in range(ncl):
        start = p
        if version >= 32:
            # LEGO Batman 3: u16 count, then (command, material) pairs
            k = struct.unpack_from('>H', d, p)[0]
            pairs = struct.unpack_from(f'>{2 * k}I', d, p + 2)
            p += 2 + 8 * k
            items, mats = pairs[0::2], pairs[1::2]
            if any(i >= nc for i in items):
                raise DispError('bad clip')
            clips.append(list(zip(mats, items)))
            spans.append((start, p))
            continue
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
        spans.append((start, p))
    if version >= 32:
        specials = _specials32(d, p, clips, commands, part_count)
        return {'version': version, 'commands': commands, 'clips': clips, 'specials': specials,
                'command_count_at': nc_at, 'clip_spans': spans}
    names = d.find(b'LBTN')
    names = names + 12 if names >= 0 else None
    end = d.find(b'TROP', p)
    end = len(d) if end < 0 else end
    q = p
    while True:
        if marked:
            q = d.find(b'ROTV', q, end)
        elif q >= end - 8:
            q = -1
        if q < 0:
            raise DispError('specials not found')
        # without markers (LEGO Pirates): the count is at q, so q - 4 stands in for the marker; a real list
        # starts with a named instance
        specials = (_specials(d, q, end, names, clips, commands, part_count) if marked
                    else _specials(d, q - 4, end, names, clips, commands, part_count, need_name=True))
        if specials is not None:
            return {'version': version, 'commands': commands, 'clips': clips, 'specials': specials,
                    'command_count_at': nc_at, 'clip_spans': spans}
        q += 4 if marked else 1


def _record(d, p, end, names, clips, commands, part_count, i, strict=False):
    """One named model instance at p, or None when p does not hold one. Strict (a list found without markers,
    LEGO Pirates): the name must start at a string boundary and be printable, the matrix finite."""
    if p + 208 > end:
        return None
    off = struct.unpack_from('>I', d, p)[0]
    clip, flags, ranges = struct.unpack_from('>3I', d, p + 180)
    if ranges > 8 or (clip >= len(clips) and clip != 0xFFFFFFFF):
        return None
    if names is not None:
        # a name starts at a string boundary of the name table
        if strict and (off > 0x100000 or names + off > len(d) or (off and d[names + off - 1] != 0)):
            return None
        e = d.find(bytes([0]), names + off, names + off + 256)
        if e < 0:
            return None
        name = d[names + off:e].decode('latin1')
        if strict and (not name or not all(32 < ord(ch) < 127 for ch in name)):
            return None
    else:
        name = f'special{i}'
    matrix = struct.unpack_from('>16f', d, p + 4)
    if strict and not all(abs(v) < 1e4 for v in matrix):          # NaN fails too
        return None
    parts = []
    if clip != 0xFFFFFFFF:
        for mat, ci in clips[clip]:
            op, _fl, value = commands[ci]
            if op in (0x80, 0xB3):
                if value >= part_count:
                    return None
                parts.append((value, mat))
    return {'name': name, 'matrix': matrix, 'clip': clip, 'flags': flags, 'parts': parts,
            'size': 204 + 4 * ranges, 'at': p}


def _specials(d, q, end, names, clips, commands, part_count, need_name=False):
    count = struct.unpack_from('>I', d, q + 4)[0]
    if not 0 < count < 4096:
        return None
    single = False
    if need_name and (count < 2 or struct.unpack_from('>I', d, q + 8)[0] == 0):
        # a one-piece scene (LEGO Pirates' hats): its only instance is the table's first name; accepted only
        # as a whole transform that draws something
        if count != 1:
            return None
        single = True
    p = q + 8
    out = []
    for i in range(count):
        rec = _record(d, p, end, names, clips, commands, part_count, i, strict=need_name)
        if rec is None:
            return None
        out.append(rec)
        p += rec['size']
    if single and (out[0]['matrix'][15] != 1.0 or not out[0]['parts']):
        return None
    if need_name and any(r['name'] == 'default_string' for r in out):
        return None             # the name table's first entry: not a real instance (Davy Jones' false match)
    return out


def _specials32(d, p, clips, commands, part_count):
    """LEGO Batman 3 (DISP 32): 'ROTV', count, then per instance: u16 name length, the name (zero-ended), a 16f
    matrix, 12 floats of bounds, u32 clip, u32 flags, 4 more words (measured on super_minifig_dx11)."""
    if d[p:p + 4] != b'ROTV':
        raise DispError('specials not found')
    count = struct.unpack_from('>I', d, p + 4)[0]
    p += 8
    out = []
    for i in range(count):
        ln = struct.unpack_from('>H', d, p)[0]
        name = d[p + 2:p + 1 + ln].decode('latin1')
        body = p + 2 + ln
        matrix = struct.unpack_from('>16f', d, body)
        clip, flags = struct.unpack_from('>II', d, body + 112)
        parts = []
        if clip < len(clips):
            for mat, ci in clips[clip]:
                op, _fl, value = commands[ci]
                if op in (0x80, 0xB3) and value < part_count:
                    parts.append((value, mat))
        out.append({'name': name, 'matrix': matrix, 'clip': clip, 'flags': flags, 'parts': parts,
                    'size': 2 + ln + 136, 'at': p, 'clip_at': body + 112})
        p = body + 136
    return out

