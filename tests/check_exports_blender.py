"""Open Figure Forge exports in Blender (background) and check them.

    blender -b --factory-startup --python tests/check_exports_blender.py -- <exports folder> <name>

Checks: the GLB, glTF and FBX each give one armature, skinned meshes on it, and an action that moves bones;
the .blend opens with the same; OBJ / STL / PLY import with geometry.
"""
import os
import sys
import zipfile
import tempfile

import bpy

folder, name = sys.argv[sys.argv.index('--') + 1:][:2]
fails = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        fails.append(what)


def rigged(label):
    arms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    skinned = [m for m in meshes if any(md.type == 'ARMATURE' for md in m.modifiers)]
    bones = len(arms[0].data.bones) if arms else 0
    moving = 0
    if arms and arms[0].animation_data and arms[0].animation_data.action:
        arm = arms[0]
        a = arm.animation_data.action
        f0, f1 = a.frame_range
        bpy.context.scene.frame_set(int(f0))
        before = [pb.matrix.copy() for pb in arm.pose.bones]
        bpy.context.scene.frame_set(int((f0 + f1) / 2))
        moving = sum(1 for pb, m in zip(arm.pose.bones, before) if (pb.matrix.to_translation() - m.to_translation()).length > 1e-4
                     or pb.matrix.to_quaternion().rotation_difference(m.to_quaternion()).angle > 1e-3)
    check(len(arms) == 1 and bones > 20 and len(skinned) > 3 and moving > 3,
          f'{label}: {len(arms)} armature, {bones} bones, {len(meshes)} meshes ({len(skinned)} skinned), {moving} bones move')


for ext in ('glb', 'gltf', 'fbx'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path = os.path.join(folder, f'{name}.{ext}')
    if ext == 'fbx':
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    rigged(ext)

bpy.ops.wm.open_mainfile(filepath=os.path.join(folder, f'{name}.blend'))
rigged('blend')

for ext in ('stl', 'ply'):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    path = os.path.join(folder, f'{name}.{ext}')
    (bpy.ops.wm.stl_import if ext == 'stl' else bpy.ops.wm.ply_import)(filepath=path)
    verts = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == 'MESH')
    check(verts > 1000, f'{ext}: {verts} vertices')

bpy.ops.wm.read_factory_settings(use_empty=True)
tmp = tempfile.mkdtemp()
with zipfile.ZipFile(os.path.join(folder, f'{name}.zip')) as z:
    z.extractall(tmp)
    pngs = [n for n in z.namelist() if n.endswith('.png')]
bpy.ops.wm.obj_import(filepath=os.path.join(tmp, f'{name}.obj'))
objs = [o for o in bpy.data.objects if o.type == 'MESH']
textured = sum(1 for m in bpy.data.materials if m.use_nodes and any(n.type == 'TEX_IMAGE' and n.image for n in m.node_tree.nodes))
check(len(objs) > 5 and textured > 0, f'obj: {len(objs)} meshes, {textured} textured materials, {len(pngs)} textures in the zip')
print('SUMMARY', 'all passed' if not fails else f'{len(fails)} failed')
