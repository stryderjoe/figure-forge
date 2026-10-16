"""Run inside Blender (background): import a GLB and save it as FBX, .blend, Collada or USD.

    blender -b --factory-startup --python blender_convert.py -- <in.glb> <out.fbx|.blend|.dae|.usd>
"""
import os
import sys

import bpy

src, dst = sys.argv[sys.argv.index('--') + 1:][:2]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
ext = os.path.splitext(dst)[1].lower()
if ext == '.fbx':
    bpy.ops.export_scene.fbx(filepath=dst, path_mode='COPY', embed_textures=True, add_leaf_bones=False,
                             bake_anim=True)
elif ext == '.blend':
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=dst, compress=True)
elif ext == '.dae':
    if not hasattr(bpy.ops.wm, 'collada_export'):
        raise SystemExit('this Blender has no Collada export')
    bpy.ops.wm.collada_export(filepath=dst)
elif ext == '.usd':
    bpy.ops.wm.usd_export(filepath=dst)
print('CONVERTED', dst)
