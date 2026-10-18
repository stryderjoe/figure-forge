"""The figure's portrait put into a game, in place of the body character's or as a new character's own.

Where each game keeps a character's portrait (found 2026-10-17 in the games' files):
- LEGO Batman 2008, Indiana Jones, Star Wars: TCS: an object of the icon scenes (stuff\\icons\\*_icons_all_pc.gsc
  and the icon_*.gsc that holds it), named by the `icon=` line of the character's text: a 256 x 256 DXT5 texture
  with its mip levels;
- LEGO Batman 2, Marvel, Pirates: stuff\\icons\\<name>_nxg.tex and its copy in icons_tex.pak (128 x 128; DXT1,
  Pirates DXT5), <name> being the text's `icon` / `icon_override` (less any 'icon_') or the character's own
  name. Marvel also lays them out on pages (stuff\\icons\\icontpage*_nxg.tex) in cells of 128;
- LEGO Batman 3: icons\\characters\\<name>_dx11.tex (256 x 256 DXT1) and its square on the sheet
  icons\\characters\\charactericontpage_dx11.tsh, found by the FNV-1 hash of the upper-case name.
"""
import io
import os
import re
import struct

import numpy as np

BS = '\\'
ICON_LINE = re.compile(r'(?im)^([ \t]*icon(?:_override)?[ \t]*=?[ \t]*["\']?)([\w]+)')


def dds_layout(dds):
    """(width, height, fourcc, levels, image data size) of a DDS."""
    h, w = struct.unpack_from('<II', dds, 12)
    levels = max(1, struct.unpack_from('<I', dds, 28)[0])
    fourcc = bytes(dds[84:88])
    block = 8 if fourcc == b'DXT1' else 16
    size = 0
    for k in range(levels):
        size += max(1, (w >> k) // 4) * max(1, (h >> k) // 4) * block
    return w, h, fourcc, levels, size


def _encoder(fourcc):
    from .formats import dxt
    if fourcc == b'DXT1':
        return dxt.encode_dxt1a
    if fourcc == b'DXT5':
        return dxt.encode_dxt5
    raise ValueError(f'a {fourcc!r} portrait is not written')


def _resize(rgba, w, h):
    """Resized with premultiplied alpha, so the clear surround does not darken the edges."""
    from PIL import Image
    if rgba.shape[1] == w and rgba.shape[0] == h:
        return rgba
    a = rgba[..., 3:4].astype(np.float64) / 255
    pm = np.clip(np.dstack([rgba[..., :3] * a, rgba[..., 3:]]) + 0.5, 0, 255).astype(np.uint8)
    im = np.asarray(Image.fromarray(pm, 'RGBA').resize((w, h), Image.LANCZOS), dtype=np.float64)
    al = im[..., 3:4] / 255
    rgb = np.where(al > 0, im[..., :3] / np.maximum(al, 1e-9), 0)
    return np.clip(np.dstack([rgb, im[..., 3:]]) + 0.5, 0, 255).astype(np.uint8)


def restamp(dds, rgba):
    """The DDS `dds` showing `rgba` instead: its header, size, format and levels kept; whatever follows its image
    data kept too."""
    w, h, fourcc, levels, size = dds_layout(dds)
    enc = _encoder(fourcc)
    data = b''
    for k in range(levels):
        data += enc(_resize(rgba, max(4, w >> k), max(4, h >> k)))
    if len(data) != size:
        raise ValueError('the portrait does not fit its image')
    return bytes(dds[:128]) + data + bytes(dds[128 + size:])


def restamp_all(data, picture):
    """data with every square image it holds (a DDS of equal sides) showing the picture, each at its own size."""
    d = bytearray(data)
    p = d.find(b'DDS ')
    while p >= 0:
        w, h, fourcc, _levels, size = dds_layout(d[p:p + 128])
        if w == h and fourcc in (b'DXT1', b'DXT5') and p + 128 + size <= len(d):
            d[p:p + 128 + size] = restamp(bytes(d[p:p + 128 + size]), picture)
        p = d.find(b'DDS ', p + 4)
    return bytes(d)


# LEGO Pirates leaves a new character out of the game as soon as it finds a portrait of its own for it in
# icons_tex.pak (tried in the running game, 2026-10-17): with a 128, 64 or 32 pixel portrait it never joined the
# party, with a 4 x 4 one it did; the pack's size, order, spelling and numbers, the collect line and the figure's
# texture size were each ruled out. Pointing the new character at a cut character's portrait (SHELLMAN, its
# image redrawn as the figure, the pack's entries and size unchanged) left it out too (2026-10-18): what counts
# is a full-size portrait that is not its body's. So its new characters keep their body's portrait (the text's
# icon line is left naming it).
NO_NEW_PORTRAIT = {'pirates': "LEGO Pirates: the new character shows its body's portrait (the game leaves a new "
                              'character out when it has one of its own).'}

# Portraits in LEGO Pirates' icons_tex.pak that no character of the game names (not in any character's text,
# collection.txt, a level's or a cutscene's text): cut characters
SPARE_PORTRAITS = ('SHELLMAN', 'DRIVER', 'JACOBY_CURSED', 'BLACKBEARD_UNDEAD', 'BLACKBEARD_ZOMBIE')
PACK_SLACK = 204                       # measured: the pack grew by 204 bytes and the game took it


def fit_pack(data, size):
    """LEGO Pirates leaves a new character out of the game when icons_tex.pak grows by more than a little (found in
    the running game, 2026-10-17: 204 bytes more did no harm, 2.2 KB did; the pack's own order and layout kept). A
    pack that holds the spare portraits and has grown past `size` gets room back from them: each in turn, while the
    pack is too big, is redrawn as a 4 x 4 image of its own. Other packs are returned as they are."""
    from .formats import pack
    if len(data) <= size + PACK_SLACK:
        return data
    entries = pack.read(data)[0]
    for spare in SPARE_PORTRAITS:
        if len(data) <= size + PACK_SLACK:
            break
        hit = next((e for e in entries if e[0].upper().endswith(BS + spare + '_NXG.TEX')), None)
        if hit is None or len(hit[2]) <= 256:
            continue
        w, h, fourcc, _l, _s = dds_layout(hit[2])
        small = new_dds(hit[2], _resize(decode(hit[2]), 4, 4), fourcc, 4)
        data = pack.replace(data, {hit[0]: small})
        entries = pack.read(data)[0]
    return data


def new_dds(like, picture, fourcc, side):
    """A DDS in the header style of `like` (one level) showing picture at side x side, in fourcc."""
    head = bytearray(like[:128])
    block = 8 if fourcc == b'DXT1' else 16
    struct.pack_into('<II', head, 12, side, side)
    struct.pack_into('<I', head, 20, (side // 4) * (side // 4) * block)
    struct.pack_into('<I', head, 28, 0)
    head[84:88] = fourcc
    return bytes(head) + _encoder(fourcc)(_resize(picture, side, side))


def decode(dds):
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(bytes(dds))).convert('RGBA'))


def icon_name(txt_bytes, stem, classic=False):
    """The name a character's portrait goes by: its text's icon line, else its own name. The newer games file
    it without the 'icon_' some texts give it."""
    m = ICON_LINE.search((txt_bytes or b'').decode('latin1'))
    name = m.group(2) if m else stem
    return name if classic else re.sub(r'(?i)^icon_', '', name)


def renamed_icon(txt_bytes, new):
    """A new character's text with its icon line naming the new character's own portrait (the line's form kept;
    a text without one is returned as it is: the game then goes by the character's name)."""
    t = (txt_bytes or b'').decode('latin1')
    m = ICON_LINE.search(t)
    if not m:
        return txt_bytes
    prefix = 'icon_' if m.group(2).lower().startswith('icon_') else ''
    return (t[:m.start(2)] + prefix + new + t[m.end(2):]).encode('latin1')


def classic_new_icon(base_icon, new):
    """The icon name of a new classic character, in the form of its base's (icon_X, Icon_X or x_icon)."""
    if base_icon.lower().startswith('icon_'):
        return base_icon[:5] + new
    if base_icon.lower().endswith('_icon'):
        return new + base_icon[-5:]
    return 'icon_' + new


def classic_new_files(root, rel, base_icon, new, picture, report=print):
    r"""A classic game's icon scenes that hold every icon (stuff\icons\*_icons_all_pc.gsc: LEGO Batman's exe
    loads stuff\icons\batman_icons_all.gsc) with an icon of the new character's own."""
    folder = find(root, 'stuff', 'icons')
    out = {}
    new_icon = classic_new_icon(base_icon, new)
    for fn in sorted(os.listdir(folder)) if folder else []:
        if fn.lower().endswith('_icons_all_pc.gsc'):
            p = os.path.join(folder, fn)
            out[rel(p)] = add_icon(_read(p), base_icon, new_icon, picture)
    # the scene the game loads a character's portrait from in play is named after its icon (icon_Batman is in
    # icon_batman_pc.gsc, Luke_hoth_icon in LUKE_HOTH_ICON_PC.GSC; seen in the running LEGO Batman: an icon only
    # in the scene of every icon left the new character's portrait empty): a copy of the base's, with the new icon
    own = find(root, 'stuff', 'icons', f'{base_icon}_pc.gsc')
    if own:
        name = f'{new_icon}_pc.gsc'
        name = name.upper() if os.path.basename(own).isupper() else name.lower()
        out[BS.join([*rel(own).split(BS)[:-1], name])] = renamed_scene(_read(own), base_icon, new_icon, picture)
    if not out:
        report('no icon scene was found for the new character')
    return out


def find(root, *parts):
    """root/parts... matched without regard to case, or None."""
    p = root
    for part in parts:
        try:
            hit = {n.lower(): n for n in os.listdir(p)}.get(part.lower())
        except OSError:
            return None
        if hit is None:
            return None
        p = os.path.join(p, hit)
    return p


def _read(p):
    with open(p, 'rb') as f:
        return f.read()


def put_blocks(dxt_data, pw, fourcc, x, y, rgba):
    """Draw rgba at (x, y) of the first level of a DXT page (dxt_data: a writable buffer of its data): only the
    4 x 4 blocks it covers are encoded again. x, y and rgba's sides are multiples of 4."""
    h, w = rgba.shape[:2]
    if x % 4 or y % 4 or w % 4 or h % 4:
        raise ValueError('a page cell must lie on 4 x 4 blocks')
    block = 8 if fourcc == b'DXT1' else 16
    data = _encoder(fourcc)(rgba)
    per_row = (w // 4) * block
    for by in range(h // 4):
        at = (((y // 4) + by) * (pw // 4) + x // 4) * block
        dxt_data[at:at + per_row] = data[by * per_row:(by + 1) * per_row]


def paste_on_page(d, p, x, y, w, h, picture):
    """Draw the portrait (fitted, centred) into the w x h square at (x, y) of the DXT page whose DDS starts at
    d[p] (a bytearray): the 4 x 4 blocks the square touches are drawn again, the page's own pixels kept around
    the square."""
    pw, ph, fourcc, _l, _s = dds_layout(d[p:p + 128])
    x0, y0 = x - x % 4, y - y % 4
    x1, y1 = min(pw, (x + w + 3) // 4 * 4), min(ph, (y + h + 3) // 4 * 4)
    page = decode(d[p:])
    region = page[y0:y1, x0:x1].copy()
    region[y - y0:y - y0 + h, x - x0:x - x0 + w] = fit(picture, w, h)
    put_blocks(memoryview(d)[p + 128:], pw, fourcc, x0, y0, region)


def fit(rgba, w, h):
    """The portrait's drawn part scaled to fit w x h, in proportion, centred, clear around (a sheet keeps each
    portrait cropped to what it shows)."""
    from PIL import Image
    ys, xs = np.nonzero(rgba[..., 3] > 0)
    crop = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else rgba
    s = min(w / crop.shape[1], h / crop.shape[0])
    small = _resize(crop, max(1, round(crop.shape[1] * s)), max(1, round(crop.shape[0] * s)))
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(Image.fromarray(small, 'RGBA'), ((w - small.shape[1]) // 2, (h - small.shape[0]) // 2))
    return np.asarray(out)


def cell_of(page, icon, cell):
    """(x, y) of the page cell showing icon (the closest), or None."""
    ph, pw = page.shape[:2]
    ic = icon.astype(np.float64)
    best, at = None, None
    for y in range(0, ph - cell + 1, cell):
        for x in range(0, pw - cell + 1, cell):
            d = np.abs(page[y:y + cell, x:x + cell].astype(np.float64) - ic).mean()
            if best is None or d < best:
                best, at = d, (x, y)
    return at if best is not None and best < 12 else None


def fnv1_upper(name):
    h = 0x811C9DC5
    for c in name.upper().encode('latin1'):
        h = ((h * 0x01000193) & 0xFFFFFFFF) ^ c
    return h


def tsh_squares(d):
    """({hash: (x, y, w, h)}, DDS position) of a LEGO Batman 3 texture sheet (.tsh). Its records are 56 bytes:
    u0 v0 u1 v1 (floats; v runs up the page), x y (the square's top left) and the source's width and height
    (u32), '1TXD', four u32, and last the hash of the name the square is for (big endian)."""
    p = d.find(b'DDS ')
    h, w = struct.unpack_from('<II', d, p + 12)
    out = {}
    q = d.find(b'1TXD')
    while 0 < q < p:
        if q >= 32 and q + 24 <= p:
            u0, v0, u1, v1 = struct.unpack_from('>4f', d, q - 32)
            x, y = struct.unpack_from('>II', d, q - 16)
            hs = struct.unpack_from('>I', d, q + 20)[0]
            rw, rh = round((u1 - u0) * w), round((v1 - v0) * h)
            if 0 <= x < w and 0 <= y < h and 0 < rw and 0 < rh:
                out[hs] = (x, y, rw, rh)
        q = d.find(b'1TXD', q + 4)
    return out, p


def files(game, txt_bytes, stem, picture, new=None, report=print, collection=None):
    """{archive path: bytes}: the game's portrait files with picture (uint8 RGBA, 256 x 256) in place of the body
    character's portrait (new=None), or as the new character `new`'s own (collection: the collection.txt that lists
    it, for LEGO Marvel's page)."""
    from .gameexport import archive_path
    root = game.root

    def rel(p):
        return archive_path(root, p)

    if game.id in ('lb1', 'indy', 'tcs'):
        base_icon = icon_name(txt_bytes, stem, classic=True)
        if new is None:
            return classic_files(root, rel, base_icon, picture, report)
        return classic_new_files(root, rel, base_icon, new, picture, report)
    name = icon_name(txt_bytes, stem)
    out = {}
    if game.id == 'lb3':
        tex = find(root, 'icons', 'characters', f'{name}_dx11.tex')
        if new is not None:
            like = tex or find(root, 'icons', 'characters', 'batman_dx11.tex')
            out[BS.join(['icons', 'characters', f'{new}_dx11.tex'])] = restamp(_read(like), picture)
            sheet = find(root, 'icons', 'characters', 'charactericontpage_dx11.tsh')
            if sheet:
                out[rel(sheet)] = tsh_add(_read(sheet), name, new, picture)
            return out
        if tex:
            out[rel(tex)] = restamp(_read(tex), picture)
        sheet = find(root, 'icons', 'characters', 'charactericontpage_dx11.tsh')
        if sheet:
            d = bytearray(_read(sheet))
            squares, p = tsh_squares(bytes(d))
            sq = squares.get(fnv1_upper(name))
            if sq:
                paste_on_page(d, p, *sq, picture)
                out[rel(sheet)] = bytes(d)
            else:
                report(f'the character sheet has no square for {name}')
        if not out:
            report(f'no portrait of {name} was found')
        return out
    # LEGO Batman 2, LEGO Marvel, LEGO Pirates
    if new is not None and game.id in NO_NEW_PORTRAIT:
        report(NO_NEW_PORTRAIT[game.id])
        return out
    icons = find(root, 'stuff', 'icons')
    tex = find(root, 'stuff', 'icons', f'{name}_nxg.tex')
    if tex is None and new is None:
        report(f'no portrait of {name} was found')
        return out
    like = tex or find(root, 'stuff', 'icons', f'{stem}_nxg.tex') or os.path.join(
        icons, sorted(n for n in os.listdir(icons) if n.lower().endswith('_nxg.tex') and 'tpage' not in n.lower())[0])
    original = _read(like)
    like_path = like
    target = f'{new}_nxg.tex' if new else os.path.basename(tex)
    mine = restamp(original, picture)
    out[rel(tex) if new is None else BS.join(['stuff', 'icons', target])] = mine
    if new is None:
        # LEGO Pirates keeps the main characters' portraits once more in a scene of their own
        # (stuff\icons\icon_willturner_nxg.gsc: the object Icon_WillTurner, one 128 x 128 DXT5 image)
        scene = find(root, 'stuff', 'icons', f'icon_{name}_nxg.gsc')
        if scene:
            out[rel(scene)] = restamp_all(_read(scene), picture)
    pak = find(root, 'icons_tex.pak')
    pak_data = None
    if pak:
        from .formats import pack
        pak_data = _read(pak)
        key = f'stuff{BS}icons{BS}{target}'
        entries = pack.read(pak_data)[0]
        if key.lower() in {n.lower() for n, *_r in entries}:
            pak_data = pack.replace(pak_data, {key: mine})
        else:
            # spelled and numbered like the base's portrait (LEGO Pirates: stuff\icons\WillTurner_NXG.tex)
            like = next((e for e in entries if e[0].lower() == f'stuff{BS}icons{BS}{os.path.basename(like_path)}'.lower()),
                        None)
            extra = (0, 0, 0)
            if like:
                stem = os.path.basename(like_path)[:-len('_nxg.tex')]
                key = like[0][:len(f'stuff{BS}icons{BS}')] + new + like[0][len(f'stuff{BS}icons{BS}') + len(stem):]
                extra = like[3]
            if any(n != n.lower() for n, *_r in entries):
                # a pack of mostly upper-case names in byte order (LEGO Pirates): the game looks a portrait up
                # without regard to case, so a lower-case name past every upper-case one is never found (seen in
                # the running game: the new character's portrait stayed empty). In upper case it sorts alike both ways
                key = key[:len(f'stuff{BS}icons{BS}')] + key[len(f'stuff{BS}icons{BS}'):].upper()
            pak_data = pack.replace(pak_data, {}, add=[(key, 4, mine, extra)])
            pak_data = fit_pack(pak_data, os.path.getsize(pak))
    if game.id == 'lmsh' and new is None:
        # the pages of portraits: the cell showing the body's portrait
        cell = decode(original)
        for page_name in ('icontpage_nxg.tex', 'icontpage_dlc0_nxg.tex', 'icontpage_dlc1_nxg.tex'):
            page = find(root, 'stuff', 'icons', page_name)
            if not page:
                continue
            d = bytearray(_read(page))
            at = cell_of(decode(d), cell, cell.shape[0])
            if at is None:
                continue
            pw, _ph, fourcc, _l, _s = dds_layout(d)
            put_blocks(memoryview(d)[128:], pw, fourcc, at[0], at[1], _resize(picture, *cell.shape[1::-1]))
            out[rel(page)] = bytes(d)
            if pak_data is not None:
                from .formats import pack
                pak_data = pack.replace(pak_data, {f'stuff{BS}icons{BS}{os.path.basename(page)}': bytes(d)})
    if game.id == 'lmsh' and new is not None and collection is not None:
        page = find(root, 'stuff', 'icons', 'icontpage_nxg.tex')
        if page:
            d = place_on_page(_read(page), collection, new, mine)
            out[rel(page)] = d
            if pak_data is not None:
                from .formats import pack
                pak_data = pack.replace(pak_data, {f'stuff{BS}icons{BS}{os.path.basename(page)}': d})
    if pak_data is not None:
        out[rel(pak)] = pak_data
    return out


def classic_files(root, rel, name, picture, report=print):
    """LEGO Batman 2008, Indiana Jones, Star Wars: the icon object's texture, in every icon scene that has it."""
    from .formats.nu20 import NU20
    folder = find(root, 'stuff', 'icons')
    out = {}
    if not folder:
        return out
    want = name.lower().encode('latin1')
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith('.gsc'):
            continue
        p = os.path.join(folder, fn)
        data = _read(p)
        if want not in data.lower():
            continue
        try:
            scene = NU20(p)
        except ValueError:
            continue
        texs = set()
        for o in scene.special_objects:
            if o['name'].lower() == name.lower() and o['model'] >= 0:
                for mi in scene.models[o['model']]['materials']:
                    t = scene.materials[mi]['texture']
                    if t >= 0:
                        texs.add(t)
        if not texs:
            continue
        # each texture's image is overwritten where it lies in the file (same size; Star Wars keeps its images
        # before the scene description, the others after it)
        new = bytearray(data)
        for t in texs:
            tx = scene.textures[t]
            dds = scene.d[tx['data_offset']:tx['data_offset'] + tx['size']]
            at = data.find(dds)
            if dds[:4] != b'DDS ' or at < 0:
                raise ValueError(f'{fn}: the icon image was not found')
            new[at:at + len(dds)] = restamp(dds, picture)
        out[rel(p)] = bytes(new)
    if not out:
        report(f'no icon {name} was found in the icon scenes')
    return out


def add_icon(data, base_name, new_name, picture):
    """A classic icon scene (data: its bytes) with an icon object of its own for a new character: each of the
    base's icon objects (icon_X and its twin icon_X1) copied under the new name, drawing the same quad with a
    material and texture of its own that show the picture. The model and object tables grow by one entry per
    copy (moved to the end of the scene description; every link into them follows)."""
    import tempfile
    from .formats.nu20 import NU20
    from .formats.nu20edit import ModelFile
    fd, tmp = tempfile.mkstemp(suffix='.gsc')
    os.write(fd, data)
    os.close(fd)
    try:
        scene = NU20(tmp)
    finally:
        os.remove(tmp)
    m = ModelFile(data)
    g, disp = m.gsnh_head, scene.disp
    low = base_name.lower()
    bases = [o for o in scene.special_objects if o['name'].lower() in (low, low + '1') and o['model'] >= 0]
    if not bases:
        raise ValueError(f'no icon {base_name} in the scene')
    if any(o['name'].lower() in (new_name.lower(), new_name.lower() + '1') for o in scene.special_objects):
        raise ValueError(f'the scene already has an icon {new_name}')

    # the texture: a copy of the base's record and image, showing the picture
    first_mat = scene.models[bases[0]['model']]['materials'][0]
    t = scene.materials[first_mat]['texture']
    recs = m.texture_records()
    r = m.clone(recs[t], 128)
    if m.old_layout:
        m.write(r + 0x30, os.urandom(16))
    else:
        m.write(r + 8, os.urandom(16))
    head, dds = m.textures[t]
    m.textures.append([head, restamp(dds, picture)])
    tex_new = len(recs)
    table = m.alloc(4 * (len(recs) + 1))
    for i, rr in enumerate(recs + [r]):
        m.set_ptr(table + 4 * i, rr)
    m.set_ptr(g + 4, table)
    m.set_u32(g, len(recs) + 1)

    # the materials: a copy of each base object's own (Indiana Jones gives the twins one each), with the new texture
    mt, mat_count = m.ptr(g + 8), m.u32(g + 0xC)
    mat_recs = [m.ptr(mt + 4 * i) for i in range(mat_count)]
    own = []
    for o in bases:
        src = scene.models[o['model']]['materials'][0]
        if src not in own:
            own.append(src)
    mat_new = {}
    added = []
    for src in own:
        mat = m.clone(mat_recs[src], 708)
        m.write(mat + 0x74, struct.pack('<h', tex_new))
        m.write(mat + 0xB8, struct.pack('<i', tex_new))
        mat_new[src] = mat_count + len(added)
        added.append(mat)
    table = m.alloc(4 * (mat_count + len(added)))
    for i, rr in enumerate(mat_recs + added):
        m.set_ptr(table + 4 * i, rr)
    m.set_ptr(g + 8, table)
    m.set_u32(g + 0xC, mat_count + len(added))

    def grow(count_at, table_at, size, extra):
        """Move the array the link at table_at points to (count at count_at, records of size bytes) to a new
        place with room for `extra` more; links into it follow. Returns (new array, old count)."""
        n = m.i32(count_at)
        old = m.ptr(table_at)
        new = m.clone(old, size * n)
        m.alloc(size * extra, align=1)
        for loc in list(m.ptr_set):
            if loc < m.pntr_at and not (old <= loc < old + size * n):
                target = m.ptr(loc)
                if target and old <= target < old + size * n:
                    m.set_ptr(loc, new + (target - old))
        m.set_ptr(table_at, new)
        m.set_u32(count_at, n + extra)
        return new, n

    # the models: a copy of each base object's model, drawing its meshes with the new material
    k = len(bases)
    models, n_models = grow(disp + 0x10, disp + 0x14, 12, k)
    model_at = []
    for j, o in enumerate(bases):
        src = models + 12 * o['model']
        q = models + 12 * (n_models + j)
        meshes = m.u32(src)
        m.set_u32(q, meshes)
        mats = m.alloc(4 * meshes)
        old_mats = m.ptr(src + 4)
        for i in range(meshes):
            m.set_u32(mats + 4 * i, mat_new.get(m.u32(old_mats + 4 * i), mat_new[own[0]]))
        m.set_ptr(q + 4, mats)
        m.set_ptr(q + 8, m.ptr(src + 8))
        model_at.append(q)
    # each model's first mesh (u16 per model)
    starts_old = m.ptr(disp + 0x1C)
    starts = m.alloc(2 * (n_models + k), align=4)
    m.write(starts, m.read(starts_old, 2 * n_models))
    for j, o in enumerate(bases):
        m.write(starts + 2 * (n_models + j), m.read(starts_old + 2 * o['model'], 2))
    m.set_ptr(disp + 0x1C, starts)

    # the objects: copies of the base's, named for the new character
    objects, n_obj = grow(disp + 0x6C, disp + 0x70, 0xD0, k)
    for j, o in enumerate(bases):
        at = objects + 0xD0 * (n_obj + j)
        src = objects + 0xD0 * o['index']
        m.write(at, m.read(src, 0xD0))
        for off in range(0, 0xD0, 4):
            if src + off in m.ptr_set or src + off in m.new_ptrs:
                m.set_ptr(at + off, m.ptr(src + off))
        m.set_ptr(at + 0xB0, model_at[j])
        name = new_name + o['name'][len(base_name):]
        s = m.alloc(len(name) + 1, align=1)
        m.write(s, name.encode('latin1') + b'\0')
        m.set_ptr(at + 0xB4, s)
    return m.tobytes()


def collect_index(collection, name):
    """The position of `collect "<name>"` among collection.txt's collect lines, or None."""
    names = re.findall(rb'(?im)^\s*collect\s+"([^"]+)"', collection)
    low = name.lower().encode('latin1')
    for i, n in enumerate(names):
        if n.lower() == low:
            return i
    return None


def place_on_page(page, collection, name, icon_dds):
    """LEGO Marvel's portrait page (icontpage_nxg.tex) with a character's portrait in its cell: cell i (rows of
    the page's width in cells of the portrait's size, left to right) shows the character of collection.txt's
    i-th collect line (measured: all 247 drawn cells of the game's page). A page too short for the cell grows
    by whole rows of cells."""
    i = collect_index(collection, name)
    if i is None:
        raise ValueError(f'{name} is not in collection.txt')
    icon = decode(icon_dds)
    cell = icon.shape[0]
    d = bytearray(page)
    pw, ph, fourcc, levels, size = dds_layout(d)
    if levels != 1:
        raise ValueError('the portrait page has mip levels')
    per_row = pw // cell
    x, y = (i % per_row) * cell, (i // per_row) * cell
    if y + cell > ph:
        rows = (y + cell - ph + cell - 1) // cell
        block = 8 if fourcc == b'DXT1' else 16
        clear = _encoder(fourcc)(np.zeros((cell * rows, pw, 4), np.uint8))
        d = d[:128 + size] + clear + d[128 + size:]
        ph += cell * rows
        struct.pack_into('<I', d, 12, ph)
        struct.pack_into('<I', d, 20, (pw // 4) * (ph // 4) * block)
    put_blocks(memoryview(d)[128:], pw, fourcc, x, y, icon)
    return bytes(d)


def tsh_add(d, base_name, new_name, picture):
    """A LEGO Batman 3 texture sheet (.tsh bytes) with a square of the new character's own: the size of the base's
    square, with the base record's numbers, showing the picture."""
    squares, p = tsh_squares(bytes(d))
    base_hash = fnv1_upper(base_name)
    if base_hash not in squares:
        raise ValueError(f'the sheet has no square for {base_name}')
    _x, _y, w, h = squares[base_hash]
    return tsh_insert(d, tsh_record(d, base_hash), fnv1_upper(new_name), fit(picture, w, h))


def tsh_record(d, name_hash):
    """The 56-byte record of a sheet's square for name_hash."""
    _squares, p = tsh_squares(bytes(d))
    q = p - 56
    while q >= 0 and struct.unpack_from('>I', d, q + 52)[0] != name_hash:
        q -= 56
    if q < 0 or d[q + 32:q + 36] != b'1TXD':
        raise ValueError('the record was not found')
    return bytes(d[q:q + 56])


def tsh_insert(d, like, name_hash, image):
    """The sheet with one more square for name_hash showing image (h x w RGBA): a record like `like` (its numbers
    after the place kept) after the last one, in a free part of the page, which grows at the bottom by a strip of
    clear blocks when it is full (the game's is). The record count after 'ROTV' and the size word that covers the
    records grow with it."""
    d = bytearray(d)
    squares, p = tsh_squares(bytes(d))
    if name_hash in squares:
        raise ValueError('the sheet already has that square')
    pw, ph, fourcc, levels, size = dds_layout(d[p:p + 128])
    h, w = image.shape[:2]
    rotv = d.find(b'ROTV', d.find(b'HSXT'))
    count_at = rotv + 4

    def free_spot():
        taken = np.zeros((ph // 4 + 2, pw // 4 + 2), bool)
        for x, y, sw, sh in squares.values():
            taken[max(0, y // 4 - 1):(y + sh + 3) // 4 + 1, max(0, x // 4 - 1):(x + sw + 3) // 4 + 1] = True
        bw, bh = (w + 3) // 4, (h + 3) // 4
        for by in range(0, ph // 4 - bh + 1):
            free = ~taken[by:by + bh, :pw // 4].any(axis=0)
            run = 0
            for bx in range(pw // 4):
                run = run + 1 if free[bx] else 0
                if run == bw:
                    return (bx - bw + 1) * 4, by * 4
        return None

    spot = free_spot()
    if spot is None:
        # v runs up the page: every square's v is measured again against the new height
        if levels != 1:
            raise ValueError('the character sheet has mip levels')
        extra = (h + 8 + 3) // 4 * 4
        block = 8 if fourcc == b'DXT1' else 16
        d[p + 128 + size:p + 128 + size] = _encoder(fourcc)(np.zeros((extra, pw, 4), np.uint8))
        new_h = ph + extra
        struct.pack_into('<I', d, p + 12, new_h)
        struct.pack_into('<I', d, p + 20, (pw // 4) * (new_h // 4) * block)
        for r in range(count_at + 4, p, 56):
            u0, v0, u1, v1 = struct.unpack_from('>4f', d, r)
            struct.pack_into('>4f', d, r, u0, 1 - (1 - v0) * ph / new_h, u1, 1 - (1 - v1) * ph / new_h)
        spot = (0, ph + 4)
        ph = new_h
    x, y = spot
    rec = struct.pack('>4f', x / pw, 1 - (y + h) / ph, (x + w) / pw, 1 - y / ph) + struct.pack('>II', x, y) + \
        like[24:52] + struct.pack('>I', name_hash)
    size_at = next(i for i in range(0, rotv) if struct.unpack_from('>I', d, i)[0] == p - (i + 4))
    struct.pack_into('>I', d, count_at, struct.unpack_from('>I', d, count_at)[0] + 1)
    struct.pack_into('>I', d, size_at, struct.unpack_from('>I', d, size_at)[0] + 56)
    d[p:p] = rec
    paste_on_page(d, p + 56, x, y, w, h, image)
    return bytes(d)


def tsh_merge(cur, orig, mine):
    """An earlier export's sheet (cur) with this export's changes against the game's (orig): squares it drew again
    are drawn in cur too, squares it added are added."""
    sq_m, pm = tsh_squares(mine)
    sq_o, po = tsh_squares(orig)
    page_m, page_o = decode(mine[pm:]), decode(orig[po:])
    out = cur
    for hs, (x, y, w, h) in sq_m.items():
        crop = page_m[y:y + h, x:x + w]
        if hs in sq_o:
            ox, oy, _w, _h = sq_o[hs]
            if np.array_equal(crop, page_o[oy:oy + h, ox:ox + w]):
                continue
        sq_c, pc = tsh_squares(out)
        if hs in sq_c:
            d = bytearray(out)
            paste_on_page(d, pc, *sq_c[hs], crop)
            out = bytes(d)
        else:
            out = tsh_insert(out, tsh_record(mine, hs), hs, crop)
    return out


def collect_index(collection, name):
    """The position of `collect "<name>"` among collection.txt's collect lines, or None."""
    names = re.findall(rb'(?im)^\s*collect\s+"([^"]+)"', collection)
    low = name.lower().encode('latin1')
    for i, n in enumerate(names):
        if n.lower() == low:
            return i
    return None


def place_on_page(page, collection, name, icon_dds):
    """LEGO Marvel's portrait page (icontpage_nxg.tex) with a character's portrait in its cell: cell i (rows of
    the page's width in cells of the portrait's size, left to right) shows the character of collection.txt's
    i-th collect line (measured: all 247 drawn cells of the game's page). A page too short for the cell grows
    by whole rows of cells."""
    i = collect_index(collection, name)
    if i is None:
        raise ValueError(f'{name} is not in collection.txt')
    icon = decode(icon_dds)
    cell = icon.shape[0]
    d = bytearray(page)
    pw, ph, fourcc, levels, size = dds_layout(d)
    if levels != 1:
        raise ValueError('the portrait page has mip levels')
    per_row = pw // cell
    x, y = (i % per_row) * cell, (i // per_row) * cell
    if y + cell > ph:
        rows = (y + cell - ph + cell - 1) // cell
        block = 8 if fourcc == b'DXT1' else 16
        clear = _encoder(fourcc)(np.zeros((cell * rows, pw, 4), np.uint8))
        d = d[:128 + size] + clear + d[128 + size:]
        ph += cell * rows
        struct.pack_into('<I', d, 12, ph)
        struct.pack_into('<I', d, 20, (pw // 4) * (ph // 4) * block)
    put_blocks(memoryview(d)[128:], pw, fourcc, x, y, icon)
    return bytes(d)


def tsh_add(d, base_name, new_name, picture):
    """A LEGO Batman 3 texture sheet (.tsh bytes) with a square of the new character's own, the size of the base's
    and with the base record's four numbers, in a free part of the page: one more 56-byte record after the last
    (the record count after 'ROTV' and the size word that covers the records grow with it)."""
    d = bytearray(d)
    squares, p = tsh_squares(bytes(d))
    base_hash, new_hash = fnv1_upper(base_name), fnv1_upper(new_name)
    if new_hash in squares:
        raise ValueError(f'the sheet already has a square for {new_name}')
    if base_hash not in squares:
        raise ValueError(f'the sheet has no square for {base_name}')
    pw, ph, _f, _l, _s = dds_layout(d[p:p + 128])
    _x, _y, w, h = squares[base_hash]
    # a free place: no square within 4 pixels, on 4 x 4 blocks
    taken = np.zeros((ph // 4 + 2, pw // 4 + 2), bool)
    for x, y, sw, sh in squares.values():
        taken[max(0, y // 4 - 1):(y + sh + 3) // 4 + 1, max(0, x // 4 - 1):(x + sw + 3) // 4 + 1] = True
    bw, bh = (w + 3) // 4, (h + 3) // 4
    spot = None
    for by in range(0, ph // 4 - bh + 1):
        row = taken[by:by + bh]
        free = ~row.any(axis=0)
        run = 0
        for bx in range(pw // 4):
            run = run + 1 if free[bx] else 0
            if run == bw:
                spot = ((bx - bw + 1) * 4, by * 4)
                break
        if spot:
            break
    rotv = d.find(b'ROTV', d.find(b'HSXT'))
    count_at = rotv + 4
    if spot is None:
        # the sheet is full (the game's is): it grows at the bottom by a strip of clear blocks. v runs up the page,
        # so every square's v is measured again against the new height
        extra = (h + 8 + 3) // 4 * 4
        pw, ph, fourcc, levels, size = dds_layout(d[p:p + 128])
        if levels != 1:
            raise ValueError('the character sheet has mip levels')
        block = 8 if fourcc == b'DXT1' else 16
        clear = _encoder(fourcc)(np.zeros((extra, pw, 4), np.uint8))
        d[p + 128 + size:p + 128 + size] = clear
        new_h = ph + extra
        struct.pack_into('<I', d, p + 12, new_h)
        struct.pack_into('<I', d, p + 20, (pw // 4) * (new_h // 4) * block)
        for r in range(count_at + 4, p, 56):
            u0, v0, u1, v1 = struct.unpack_from('>4f', d, r)
            struct.pack_into('>4f', d, r, u0, 1 - (1 - v0) * ph / new_h, u1, 1 - (1 - v1) * ph / new_h)
        spot = (0, ph + 4)
        ph = new_h
    x, y = spot
    # the base's record: its four numbers after '1TXD'
    q = p - 56
    while q >= 0 and struct.unpack_from('>I', d, q + 52)[0] != base_hash:
        q -= 56
    if q < 0 or d[q + 32:q + 36] != b'1TXD':
        raise ValueError('the base record was not found')
    rec = struct.pack('>4f', x / pw, 1 - (y + h) / ph, (x + w) / pw, 1 - y / ph) + struct.pack('>II', x, y) + \
        bytes(d[q + 24:q + 32]) + bytes(d[q + 32:q + 52]) + struct.pack('>I', new_hash)
    # the record count (after the second 'ROTV') and the size word that reaches the image
    size_at = next(i for i in range(0, rotv) if struct.unpack_from('>I', d, i)[0] == p - (i + 4))
    struct.pack_into('>I', d, count_at, struct.unpack_from('>I', d, count_at)[0] + 1)
    struct.pack_into('>I', d, size_at, struct.unpack_from('>I', d, size_at)[0] + 56)
    d[p:p] = rec
    paste_on_page(d, p + 56, x, y, w, h, picture)
    return bytes(d)


def _scene(data):
    import tempfile
    from .formats.nu20 import NU20
    fd, tmp = tempfile.mkstemp(suffix='.gsc')
    os.write(fd, data)
    os.close(fd)
    try:
        return NU20(tmp)
    finally:
        os.remove(tmp)


def _object_image(scene, o):
    t = scene.materials[scene.models[o['model']]['materials'][0]]['texture']
    tx = scene.textures[t]
    return scene.d[tx['data_offset']:tx['data_offset'] + tx['size']]


def gsc_merge(cur, orig, mine):
    """A classic icon scene an earlier export changed (cur) with this export's changes against the game's (orig):
    icon images it drew again are drawn in cur too; icons it added (copies of a base icon: the same matrix) are
    added to cur."""
    s_c, s_o, s_m = _scene(cur), _scene(orig), _scene(mine)
    out = bytearray(cur)
    old = {o['name'].lower(): o for o in s_o.special_objects}
    have = {o['name'].lower() for o in s_c.special_objects}
    for o in s_m.special_objects:
        if o['name'].lower() in old:
            before, after = _object_image(s_o, old[o['name'].lower()]), _object_image(s_m, o)
            if before != after:
                at = bytes(out).find(before)
                if at >= 0:
                    out[at:at + len(before)] = after
    added = [o for o in s_m.special_objects if o['name'].lower() not in old and o['name'].lower() not in have]
    names = {o['name'] for o in added if not o['name'].endswith('1') or o['name'][:-1] not in
             {a['name'] for a in added}}
    for name in sorted(names):
        o = next(a for a in added if a['name'] == name)
        base = next((b for b in s_o.special_objects if b['matrix'] == o['matrix']), None)
        if base is None:
            continue
        pic = decode(_object_image(s_m, o))
        out = bytearray(add_icon(bytes(out), base['name'], name, pic))
    return bytes(out)


def renamed_scene(data, base_name, new_name, picture):
    """A copy of a classic character's own icon scene for a new character: the base's icon objects (X and X1)
    renamed for the new character and their image showing the picture. Nothing else of the scene changes (the
    new names are added at the end of its description; adding whole objects to this scene made LEGO Batman
    crash as the portrait came on screen, 2026-10-17)."""
    from .formats.nu20edit import ModelFile
    scene = _scene(data)
    m = ModelFile(data)
    low = base_name.lower()
    objects = m.ptr(scene.disp + 0x70)
    texs = set()
    for o in scene.special_objects:
        if o['name'].lower() in (low, low + '1') and o['model'] >= 0:
            at = objects + 0xD0 * o['index']
            name = new_name + o['name'][len(base_name):]
            s = m.alloc(len(name) + 1, align=1)
            m.write(s, name.encode('latin1') + b'\0')
            m.set_ptr(at + 0xB4, s)
            for mi in scene.models[o['model']]['materials']:
                if scene.materials[mi]['texture'] >= 0:
                    texs.add(scene.materials[mi]['texture'])
    if not texs:
        raise ValueError(f'no icon {base_name} in the scene')
    for t in texs:
        m.textures[t][1] = restamp(m.textures[t][1], picture)
    return m.tobytes()
