# PyInstaller recipe: one folder holding FigureForge.exe, its runtime and the user interface.
#   .venv\Scripts\python -m PyInstaller FigureForge.spec --noconfirm
a = Analysis(['run.py'], pathex=['.'], datas=[('ui', 'ui'), ('forge/blender_convert.py', 'forge')],
             hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium', 'clr'],
             excludes=['tkinter', 'matplotlib', 'scipy', 'pytest'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FigureForge', console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.datas, name='FigureForge')
