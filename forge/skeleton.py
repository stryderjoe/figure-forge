"""Common bone names across the three games, and matrix helpers.

Every humanoid in the three games uses one of two naming schemes with the same structure (measured on all
character models, 2026-10-15):
    A (LEGO Batman, Indiana Jones): Character, Hips, LeftUpLeg, LeftLeg, LeftFoot, LeftToeBase, ..., Spine,
      Spine1, LeftShoulder, LeftArm, LeftArmRoll, LeftForeArm, LeftForeArmRoll, LeftHand, LeftWeapon, ...,
      Head, Hat (28 bones; extra cape, satchel or prop bones after them)
    B (Star Wars; Bane and the Joker in LEGO Batman): character, upperTorso, leftLeg, leftKnee, leftAnkle,
      leftToe, ..., body, chest, leftArm, leftShoulder, leftElbow, leftElbowLength, leftHand, weaponLeft,
      ..., head (25 bones)
Both map onto one set of common names, so parts and animations can move between characters and games.

Viewer space is glTF's: the game's (x, y, z) becomes (x, y, -z). A game matrix M (row-major, row vectors)
becomes Z M Z there, and its row-major array is then three.js's column-major Matrix4 array.
"""
import numpy as np

COMMON = {
    'character': 'root',
    'hips': 'pelvis', 'uppertorso': 'pelvis',
    'spine': 'spine', 'body': 'spine',
    'spine1': 'chest', 'chest': 'chest',
    'head': 'head', 'hat': 'hat',
}
for side, b_side in (('left', 'left'), ('right', 'right')):
    s = side[0]
    COMMON.update({
        f'{side}upleg': f'{s}_thigh', f'{b_side}leg': f'{s}_thigh',
        f'{side}foot': f'{s}_foot', f'{b_side}ankle': f'{s}_foot',
        f'{side}toebase': f'{s}_toe', f'{b_side}toe': f'{s}_toe',
        f'{b_side}knee': f'{s}_shin',
        f'{side}armroll': f'{s}_upperarm_roll',
        f'{side}forearm': f'{s}_forearm', f'{b_side}elbow': f'{s}_forearm',
        f'{side}forearmroll': f'{s}_forearm_roll', f'{b_side}elbowlength': f'{s}_forearm_roll',
        f'{side}hand': f'{s}_hand',
        f'{side}weapon': f'{s}_weapon', f'weapon{b_side}': f'{s}_weapon',
    })


def common_name(name, parent_common=None):
    """Common name of a game bone. Scheme A's LeftLeg is the shin and LeftArm the upper arm; in scheme B
    leftLeg is the thigh and leftArm the collar bone. The parent decides which."""
    n = name.lower()
    for side in ('left', 'right'):
        s = side[0]
        if n == f'{side}leg':
            return f'{s}_shin' if parent_common == f'{s}_thigh' else f'{s}_thigh'
        if n == f'{side}arm':
            return f'{s}_upperarm' if parent_common == f'{s}_clavicle' else f'{s}_clavicle'
        if n == f'{side}shoulder':
            return f'{s}_upperarm' if parent_common == f'{s}_clavicle' else f'{s}_clavicle'
    return COMMON.get(n, n)


def common_names(bones):
    out = []
    for b in bones:
        parent = out[b['parent']] if 0 <= b['parent'] < len(out) else None
        out.append(common_name(b['name'], parent))
    return out


# what body region a bone moves (for cutting skinned bodies into parts)
REGION = {'root': 'hips', 'pelvis': 'hips', 'spine': 'torso', 'chest': 'torso', 'head': 'head', 'hat': 'head'}
for s in 'lr':
    REGION.update({f'{s}_thigh': 'legs', f'{s}_shin': 'legs', f'{s}_foot': 'legs', f'{s}_toe': 'legs',
                   f'{s}_clavicle': 'arms', f'{s}_upperarm': 'arms', f'{s}_upperarm_roll': 'arms',
                   f'{s}_forearm': 'arms', f'{s}_forearm_roll': 'arms', f'{s}_hand': 'hands',
                   f'{s}_weapon': 'hands'})


def region_of(common):
    if common in REGION:
        return REGION[common]
    c = common.lower()
    if 'cape' in c or 'cloak' in c:
        return 'cape'
    return 'other'


def mirror(m16):
    """Game row-major matrix -> viewer space (Z M Z); also the three.js column-major array."""
    m = np.asarray(m16, dtype=np.float64).reshape(4, 4).copy()
    m[:, 2] *= -1
    m[2, :] *= -1
    return m


def to_viewer_points(p):
    p = np.asarray(p, dtype=np.float64).reshape(-1, 3).copy()
    p[:, 2] *= -1
    return p
