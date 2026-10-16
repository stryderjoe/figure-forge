"""Skeletons (HGOL) of TT's newer models, big-endian. Version 9 (LEGO Batman 2) and 10 (LEGO Marvel Super Heroes)
share one layout; names are offsets into the model's name table (NTBL, data at its offset + 12).

    'LOGH' u32 version, u32 joint count,
        joints: u32 name, 16f orient, 3f locator, u8 parent (255 = none), u8 flags        (82 bytes each)
    u32 count, count * 16f local bind matrix (row major)
    u32 count, count * 16f inverse world bind matrix
    u32 n, n bytes                                                    (before the locators)
    u32 n, n * {u32 name, 16f matrix, u8 joint}                       (locators, "points of interest")
    u32 n, n bytes                                                    (locator remap)
    u32 n, n bytes                                                    (not understood; version 10 only)
    u32 n, n * {u8 kind (0 rigid, 1 skinned), u8 joint, u16 display special, u8 layer}
    u32 n, n * {u32 name, u16 first metadata, u16 rigid count, u16 skinned count}     (layers)
A model can hold several skeletons (LEGO Batman 2's minifig has 103-, 93- and 54-joint ones); the one whose
layers cover the model's display is the one in use.
"""
import struct


class SkelError(ValueError):
    pass


def name_table(d):
    at = d.find(b'LBTN')
    if at < 0:
        return None
    return at + 12


def _rd(d, p, fmt):
    return struct.unpack_from('>' + fmt, d, p), p + struct.calcsize('>' + fmt)


def read_all(d):
    names = name_table(d)

    def nm(o):
        if names is None:
            return f'#{o}'
        e = d.find(b'\0', names + o)
        return d[names + o:e].decode('latin1')

    out = []
    at = d.find(b'LOGH')
    while at >= 0:
        try:
            out.append(_parse(d, at, nm))
        except (SkelError, struct.error, IndexError):
            pass
        at = d.find(b'LOGH', at + 4)
    return out


def _parse(d, at, nm):
    (ver, n), p = _rd(d, at + 4, 'II')
    if ver not in (5, 6, 9, 10) or not 0 < n < 256:
        raise SkelError(f'skeleton version {ver}')
    joints = []
    for i in range(n):
        (off,), _ = _rd(d, p, 'I')
        orient, _ = _rd(d, p + 4, '16f')
        loc, _ = _rd(d, p + 68, '3f')
        par, flags = d[p + 80], d[p + 81]
        joints.append({'name': nm(off), 'parent': None if par == 255 else par, 'flags': flags,
                       'orient': orient, 'locator': loc})
        if par != 255 and par >= i:
            raise SkelError('parent after child')
        p += 82
    for field in ('bind', 'inverse_bind'):
        (c,), p = _rd(d, p, 'I')
        if c != n:
            raise SkelError('bind matrix count differs')
        for j in joints:
            j[field], p = _rd(d, p, '16f')
    (c,), p = _rd(d, p, 'I')
    p += c
    (c,), p = _rd(d, p, 'I')
    locators = []
    for _ in range(c):
        (off,), _x = _rd(d, p, 'I')
        m, _x = _rd(d, p + 4, '16f')
        locators.append({'name': nm(off), 'matrix': m, 'joint': d[p + 68]})
        p += 69
    (c,), p = _rd(d, p, 'I')
    remap = list(d[p:p + c])
    p += c
    if ver >= 10:
        (c,), p = _rd(d, p, 'I')
        p += c
    (c,), p = _rd(d, p, 'I')
    meta = []
    size = 5 if ver >= 9 else 4                  # version 5 / 6 (LEGO Pirates): no layer byte
    for _ in range(c):
        kind, joint = d[p], d[p + 1]
        (special,), _x = _rd(d, p + 2, 'H')
        meta.append({'kind': kind, 'joint': joint, 'special': special, 'layer': d[p + 4] if size == 5 else None})
        p += size
    (c,), p = _rd(d, p, 'I')
    if c > 256:
        raise SkelError('layer count')
    layers = []
    for _ in range(c):
        (off, first, rigid, skinned), p = _rd(d, p, 'IHHH')
        layers.append({'name': nm(off), 'first': first, 'rigid': rigid, 'skinned': skinned,
                       'items': meta[first:first + rigid + skinned]})
    return {'at': at, 'version': ver, 'joints': joints, 'locators': locators, 'remap': remap,
            'meta': meta, 'layers': layers, 'end': p}
