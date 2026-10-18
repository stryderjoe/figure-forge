"""The figure's portrait, the small picture the games show in the corner and on the character grid.

Every TT game of the three eras draws it the same way (measured on their own portraits, 2026-10-17): the
head seen from the front, filling most of a square, with the top of the torso below the neck cut off by an arc,
on a clear background. This draws the figure's GLB that way with a small software renderer, so the picture
can be made without the viewer (exports, checks).
"""
import io
import re

import numpy as np

from .glb import Glb
from .gamewrite import image_rgba

HEADISH = re.compile(r'head|neck|^hat|^hair|helmet|^face|jaw|^eye|brow|mouth|crown|^hood|^mask|cowl', re.I)
HEAD_SLOTS = {'head', 'face', 'hair', 'hat', 'mask', 'helmet', 'headgear', 'headwear', 'hood', 'visor',
              'goggles', 'glasses', 'beard', 'crown'}


def _srgb(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _triangles(glb):
    """[(positions (n,3), normals (n,3), uvs (n,2) or None, colours (n,4) sRGB or None, indices (m,3),
    image (h,w,4) float 0..1, blend, head mask (n,) bool, slot)]"""
    names = {i: n.get('name', '') for i, n in enumerate(glb.nodes)}
    out = []
    for r in glb.meshes():
        pos = r['positions']
        if len(pos) == 0 or len(r['indices']) == 0:
            continue
        colour, image, mode = glb.material(r['material'])
        img = image_rgba(image, colour).astype(np.float64) / 255.0
        nrm = r['normals'] if r['normals'] is not None else np.tile([0.0, 0.0, 1.0], (len(pos), 1))
        col = None
        if 'colours' in r:
            c = r['colours']
            col = np.c_[_srgb(c[:, :3]), c[:, 3] if c.shape[1] > 3 else np.ones(len(c))]
        if 'joint_index' in r:
            dom = r['joint_index'][np.arange(len(pos)), r['weights'].argmax(1)]
            bone_names = np.array([names.get(r['joints'][int(j)], '') if int(j) < len(r['joints']) else ''
                                   for j in range(int(dom.max()) + 1)])
            head = np.array([bool(HEADISH.search(n)) for n in bone_names])[dom]
        else:
            head = np.full(len(pos), bool(HEADISH.search(names.get(r.get('bone'), '') or '')))
        slot = (r.get('slot') or '').lower()
        if slot == 'cape':
            continue                    # seen edge-on from the front: a line across the neck (the games leave it out)
        if slot in HEAD_SLOTS:
            head[:] = True
        out.append((pos, nrm, r['uv'], col, r['indices'], img, mode == 'BLEND', head, slot))
    return out


def _frame(tris):
    """(centre x, neck y, units per picture side) from the head's pieces.

    The games' portraits keep one scale (LEGO Batman 2, Marvel, Pirates, LEGO Batman 3 and LEGO Batman 2008
    measured alike): the bare head is 47% of the picture wide and ends (the neck) at 72% of its height; hair,
    hats and masks reach up from there as far as they reach."""
    allp = np.vstack([t[0] for t in tris])
    lo_all, hi_all = allp.min(0), allp.max(0)
    core = [t[0] for t in tris if t[8] == 'head']
    heads = [t[0][t[7]] for t in tris if t[7].any()]
    if not heads:
        top = hi_all[1] - 0.27 * (hi_all[1] - lo_all[1])
        heads = [allp[allp[:, 1] >= top]]
    hp = np.vstack(heads)
    cp = np.vstack(core) if core else hp
    lo, hi = cp.min(0), cp.max(0)
    neck = lo[1] if core else np.percentile(hp[:, 1], 0.5)       # the head's own bottom (hair may hang lower)
    extent = (hi[0] - lo[0]) / 0.47
    # everything on the head must fit: above the neck 70% of the picture, across it 96%
    extent = max(extent, (hp[:, 1].max() - neck) / 0.70, 2 * np.abs(hp[:, 0] - (lo[0] + hi[0]) / 2).max() / 0.96)
    return (lo[0] + hi[0]) / 2, neck, extent


def render(glb_bytes, size=256, supersample=2):
    """The portrait as uint8 RGBA (size x size)."""
    glb = Glb(glb_bytes)
    tris = _triangles(glb)
    if not tris:
        raise ValueError('the figure has no meshes')
    cx, neck, extent = _frame(tris)
    n = size * supersample
    k = n / extent

    def to_screen(p):
        # picture coordinates: x right, y down; the neck at 72% of the height
        return np.c_[(p[:, 0] - cx) * k + n / 2, (neck - p[:, 1]) * k + 0.72 * n, p[:, 2]]

    colour = np.zeros((n, n, 3))
    alpha = np.zeros((n, n))
    depth = np.full((n, n), -np.inf)
    light = np.array([-0.35, 0.55, 1.0])
    light /= np.linalg.norm(light)
    half = light + np.array([0.0, 0.0, 1.0])
    half /= np.linalg.norm(half)

    def shade(rgb, nrm):
        nn = nrm / np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-9)
        nn = np.where(nn[..., 2:3] < 0, -nn, nn)                 # two-sided: faces seen from the back
        diff = np.clip(nn @ light, 0, 1)
        spec = np.power(np.clip(nn @ half, 0, 1), 48) * 0.35
        return np.clip(rgb * (0.48 + 0.62 * diff[..., None]) + spec[..., None], 0, 1)

    for blend_pass in (False, True):
        for pos, nrm, uv, col, idx, img, blend, _, _ in tris:
            if blend != blend_pass:
                continue
            s = to_screen(pos)
            ih, iw = img.shape[:2]
            for t in idx:
                a, b, c = s[t[0]], s[t[1]], s[t[2]]
                x0, x1 = int(max(0, np.floor(min(a[0], b[0], c[0])))), int(min(n - 1, np.ceil(max(a[0], b[0], c[0]))))
                y0, y1 = int(max(0, np.floor(min(a[1], b[1], c[1])))), int(min(n - 1, np.ceil(max(a[1], b[1], c[1]))))
                if x0 > x1 or y0 > y1:
                    continue
                area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if abs(area) < 1e-12:
                    continue
                ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
                px, py = xs + 0.5, ys + 0.5
                w0 = ((b[0] - px) * (c[1] - py) - (b[1] - py) * (c[0] - px)) / area
                w1 = ((c[0] - px) * (a[1] - py) - (c[1] - py) * (a[0] - px)) / area
                w2 = 1.0 - w0 - w1
                inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
                if not inside.any():
                    continue
                z = w0 * a[2] + w1 * b[2] + w2 * c[2]
                yy, xx = ys[inside], xs[inside]
                z = z[inside]
                wb = np.c_[w0[inside], w1[inside], w2[inside]]
                closer = z >= depth[yy, xx] - (2e-4 if blend else 0.0)
                if not closer.any():
                    continue
                yy, xx, z, wb = yy[closer], xx[closer], z[closer], wb[closer]
                if uv is not None:
                    u = wb @ uv[t][:, 0]
                    v = wb @ uv[t][:, 1]
                    tx = np.clip((u % 1.0) * iw, 0, iw - 1).astype(int)
                    ty = np.clip((v % 1.0) * ih, 0, ih - 1).astype(int)
                    texel = img[ty, tx]
                else:
                    texel = np.broadcast_to(img[0, 0], (len(z), 4))
                rgb, a_ = texel[:, :3], texel[:, 3]
                if col is not None:
                    vc = wb @ col[t]
                    rgb, a_ = rgb * vc[:, :3], a_ * vc[:, 3]
                lit = shade(rgb, wb @ nrm[t])
                if blend:
                    a_ = np.clip(a_, 0, 1)[:, None]
                    colour[yy, xx] = colour[yy, xx] * (1 - a_) + lit * a_
                    alpha[yy, xx] = np.maximum(alpha[yy, xx], a_[:, 0])
                else:
                    keep = a_ >= 0.5
                    yy, xx = yy[keep], xx[keep]
                    colour[yy, xx] = lit[keep]
                    alpha[yy, xx] = 1.0
                    depth[yy, xx] = z[keep]

    # below the neck, only the top of the torso inside an arc that ends at 86% of the height (measured)
    ys, xs = np.mgrid[0:n, 0:n]
    arc = ((xs + 0.5 - n / 2) ** 2 + (ys + 0.5 - 0.474 * n) ** 2) <= (0.386 * n) ** 2
    alpha = np.where((ys + 0.5 < 0.72 * n) | arc, alpha, 0.0)

    # down to size (premultiplied)
    pm = np.dstack([colour * alpha[..., None], alpha])
    pm = pm.reshape(size, supersample, size, supersample, 4).mean(axis=(1, 3))
    a = pm[..., 3:4]
    rgb = np.where(a > 0, pm[..., :3] / np.maximum(a, 1e-9), 0)
    return np.clip(np.dstack([rgb, a]) * 255 + 0.5, 0, 255).astype(np.uint8)


def png(rgba):
    from PIL import Image
    b = io.BytesIO()
    Image.fromarray(rgba, 'RGBA').save(b, 'PNG')
    return b.getvalue()
