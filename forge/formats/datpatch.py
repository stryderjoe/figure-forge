"""Put changed files into a classic TT .DAT archive (LEGO Batman 2008 index -2, LEGO Star Wars TCS -3,
LEGO Indiana Jones 1): the new data is appended at the end of the archive, stored (not compressed), and the
file's index entry is pointed at it. Nothing else in the archive moves.

Index layout (see tt-oldera-tools/tools/ttdat.py):
    u32 a, u32 info_size             info_off = ((~a) << 8) + 0x100 when a's top bit is set, else a
    @info_off: i32 version, i32 file_count,
               file_count * { u32 off_hi, u32 zsize, u32 size, u32 flags }
                   offset = (off_hi << 8) | ((flags >> 8) & 0xff); flags & 0xff = compression (0 none, 2 LZ2K)
               names (a tree of records), names blob, then one u32 per file (a hash of its path in -3)
Only a file that already exists in the archive can be replaced.
"""
import os
import struct


class DatPatchError(ValueError):
    pass


def _index(f):
    f.seek(0)
    a, info_size = struct.unpack('<II', f.read(8))
    info_off = (((a ^ 0xFFFFFFFF) << 8) + 0x100) if a & 0x80000000 else a
    f.seek(info_off)
    info = f.read(info_size)
    version, count = struct.unpack_from('<ii', info, 0)
    return info_off, info, version, count


def paths(dat_path):
    """{lower-case path: file index} of an archive (uses the reader in this package)."""
    from .dat import DatArchive
    arc = DatArchive(dat_path)
    out = {e.path.lower(): i for i, e in enumerate(arc.entries)}
    arc.f.close()
    return out


def patch(dat_path, files):
    """files: {path inside the archive: bytes}. Appends each and repoints its entry. Returns the paths done."""
    index_of = paths(dat_path)
    missing = [p for p in files if p.lower().replace('/', '\\') not in index_of]
    if missing:
        raise DatPatchError(f'not in {os.path.basename(dat_path)}: {", ".join(missing)}')
    done = []
    with open(dat_path, 'r+b') as f:
        info_off, info, version, count = _index(f)
        for path, data in files.items():
            i = index_of[path.lower().replace('/', '\\')]
            f.seek(0, 2)
            end = f.tell()
            pad = (-end) % 2048                          # the archives keep every file on a 2 KB boundary
            f.write(b'\0' * pad)
            offset = end + pad
            f.write(data)
            off_hi, low = offset >> 8, offset & 0xFF
            rec = info_off + 8 + i * 16
            f.seek(rec)
            _old_hi, _old_z, _old_size, old_flags = struct.unpack('<IIII', f.read(16))
            flags = (old_flags & 0xFFFF0000) | (low << 8) | 0       # stored, not compressed
            f.seek(rec)
            f.write(struct.pack('<IIII', off_hi, len(data), len(data), flags))
            done.append(path)
    return done
