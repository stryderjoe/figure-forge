"""Classic animation clips (.an3) as tracks for the viewer.

A clip's node j drives its owner's bone j. Channels are translation, Euler rotation (x then y then z, row
vectors) and scale in the animation files' space, which is the model's space mirrored along Z: so in the
viewer's space (also mirrored) the node's local matrix is the channel matrix itself.
Tracks are named by common bone name (skeleton.py), so a clip can play on any figure of either scheme.
"""
import os

import numpy as np

from . import skeleton
from .formats import classic


def _quat(r):
    """Rotation matrix (column vectors) -> (x, y, z, w)."""
    t = np.trace(r)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = ((r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s)
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = (0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s)
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = ((r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s)
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = ((r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s)
    q = np.array(q)
    return q / np.linalg.norm(q)


def clip_list(char):
    """Clips the character's .txt declares: [{name, file, fps, action}] (fps None = not given)."""
    out = []
    for name, path, fps, action in classic.declared_clips(char.path):
        out.append({'name': name, 'file': path, 'fps': fps, 'action': action})
    return out


# Star Wars stores each rotation relative to the bone's bind rotation (local = S B R T, as in LEGO Star Wars
# II); LEGO Batman and Indiana Jones store the whole rotation (local = S R T). Measured 2026-10-15 by
# skinning each game's meshes with both rules: Star Wars stretches least bind-relative (Luke 4.3 % against
# 5.8 %, Vader 8.2 % against 9.3 %), Batman, the Joker and Indy least absolute (4.5 / 6.1 / 2.9 % against
# 7.8 / 8.4 / 4.4 %).
BIND_RELATIVE = {'tcs'}


def node_matrix(char, j, values):
    """Viewer-space local matrix of bone j of `char` from one node's channels, by its game's rule."""
    m = np.asarray(classic.channel_matrix(values), dtype=np.float64).reshape(4, 4)
    if char.id.split(':', 1)[0] in BIND_RELATIVE and j < len(char.bones):
        bind, _ = _rot(skeleton.mirror(char.bones[j]['bind_local']))
        scale = np.linalg.norm(m[:3, :3], axis=1)
        r = m[:3, :3] / np.where(scale > 1e-9, scale, 1.0)[:, None]
        m[:3, :3] = np.diag(scale) @ bind @ r
    return m


def _bind_locals(char):
    return [skeleton.mirror(b['bind_local']) for b in char.bones]


def _fk(locals_, bones):
    world = []
    for j, b in enumerate(bones):
        m = locals_[j]
        world.append(m if b['parent'] < 0 else m @ world[b['parent']])
    return world


def _rot(m):
    r = m[:3, :3].copy()
    n = np.linalg.norm(r, axis=1)
    return r / np.where(n > 1e-9, n, 1.0)[:, None], n


def retarget_json(owner, target, path, fps=None):
    """A clip of `owner` played on `target` (another skeleton): each common bone gets the owner's turn away
    from its own bind pose, applied in world space to the target's bind pose; translations stay the
    target's own except the root and pelvis, scaled by the ratio of the two pelvis heights."""
    clip = classic.load_clip(path)
    nodes = min(clip.nodes, len(owner.bones))
    o_bind, t_bind = _bind_locals(owner), _bind_locals(target)
    o_bind_w, t_bind_w = _fk(o_bind, owner.bones), _fk(t_bind, target.bones)
    o_index = {c: i for i, c in enumerate(owner.common) if i < nodes}
    pel_o = o_index.get('pelvis')
    pel_t = target.common.index('pelvis') if 'pelvis' in target.common else None
    ratio = 1.0
    if pel_o is not None and pel_t is not None:
        ho, ht = abs(o_bind_w[pel_o][3, 1]), abs(t_bind_w[pel_t][3, 1])
        ratio = ht / ho if ho > 1e-6 else 1.0
    frames = []
    for f in range(clip.frames):
        pose = clip.pose(f)
        loc = [node_matrix(owner, j, pose[j]) if j < nodes else o_bind[j]
               for j in range(len(owner.bones))]
        frames.append(_fk(loc, owner.bones))
    tracks = []
    for j, tb in enumerate(target.bones):
        i = o_index.get(target.common[j])
        if i is None:
            continue
        t, q, s = [], [], []
        prev = None
        delta0 = _rot(o_bind_w[i])[0].T                    # owner bind world rotation, inverted
        for f, ow in enumerate(frames):
            r_o, scale = _rot(ow[i])
            want = _rot(t_bind_w[j])[0] @ delta0 @ r_o       # row vectors: target bind, then owner's turn
            p = tb['parent']
            if p >= 0:
                # the parent's animated world rotation on the target, rebuilt the same way
                pi = o_index.get(target.common[p])
                parent_w = (_rot(t_bind_w[p])[0] @ _rot(o_bind_w[pi])[0].T @ _rot(frames[f][pi])[0]
                            if pi is not None else _rot(t_bind_w[p])[0])
                local_r = want @ parent_w.T
            else:
                local_r = want
            quat = _quat(local_r.T)
            if prev is not None and np.dot(quat, prev) < 0:
                quat = -quat
            prev = quat
            q += quat.tolist()
            if target.common[j] in ('root', 'pelvis'):
                own_local = node_matrix(owner, i, clip.pose(f)[i])
                t += (own_local[3, :3] * ratio).tolist()
            else:
                t += t_bind[j][3, :3].tolist()
            s += np.clip(scale, 0.0, 10.0).tolist()
        tracks.append({'bone': target.common[j], 'name': tb['name'], 't': [round(x, 5) for x in t],
                       'q': [round(x, 6) for x in q], 's': [round(x, 5) for x in s]})
    return {'name': os.path.splitext(os.path.basename(path))[0], 'fps': fps or 30.0, 'fpsAssumed': fps is None,
            'frames': clip.frames, 'owner': owner.id, 'target': target.id, 'retargeted': True, 'tracks': tracks}


def clip_json(char, path, fps=None):
    """Tracks of one clip owned by `char`: {name, fps, frames, tracks: [{bone, t, q, s}]}."""
    clip = classic.load_clip(path)
    nodes = min(clip.nodes, len(char.bones))
    poses = [clip.pose(f) for f in range(clip.frames)]
    tracks = []
    for j in range(nodes):
        t, q, s = [], [], []
        prev = None
        for pose in poses:
            v = pose[j]
            m = node_matrix(char, j, v)
            scale = np.linalg.norm(m[:3, :3], axis=1)
            r = m[:3, :3] / np.where(scale > 1e-9, scale, 1.0)[:, None]
            if (scale <= 1e-9).any():
                quat = prev if prev is not None else np.array([0, 0, 0, 1.0])
            else:
                quat = _quat(r.T)                 # row-vector rotation -> column-vector rotation
            if prev is not None and np.dot(quat, prev) < 0:
                quat = -quat
            prev = quat
            t += m[3, :3].tolist()
            q += quat.tolist()
            s += scale.tolist()
        tracks.append({'bone': char.common[j], 'name': char.bones[j]['name'],
                       't': [round(x, 5) for x in t], 'q': [round(x, 6) for x in q], 's': [round(x, 5) for x in s]})
    return {'name': os.path.splitext(os.path.basename(path))[0], 'fps': fps or 30.0, 'fpsAssumed': fps is None,
            'frames': clip.frames, 'owner': char.id, 'tracks': tracks}
