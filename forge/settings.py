"""Settings and cache folder: %APPDATA%\\FigureForge (or ~/.figureforge elsewhere)."""
import glob
import json
import os
import shutil


def folder():
    if os.environ.get('FIGUREFORGE_HOME'):
        path = os.environ['FIGUREFORGE_HOME']
        os.makedirs(os.path.join(path, 'cache'), exist_ok=True)
        return path
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    path = os.path.join(base, 'FigureForge') if os.environ.get('APPDATA') else os.path.join(base, '.figureforge')
    os.makedirs(os.path.join(path, 'cache'), exist_ok=True)
    return path


def cache_path(name):
    return os.path.join(folder(), 'cache', name)


def load():
    try:
        with open(os.path.join(folder(), 'settings.json'), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'games': {}}


def save(cfg):
    with open(os.path.join(folder(), 'settings.json'), 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)


def find_blender(cfg):
    """Blender's executable: the one set in Settings, else the newest in Program Files, else on PATH."""
    given = cfg.get('blender')
    if given and os.path.isfile(given):
        return given
    found = sorted(glob.glob(r'C:\Program Files\Blender Foundation\Blender *\blender.exe'))
    if found:
        return found[-1]
    return shutil.which('blender') or ''


def export_folder(cfg):
    """Where exports go: Settings' 'exports', else Documents/Figure Forge (created when needed)."""
    path = cfg.get('exports') or os.path.join(os.path.expanduser('~'), 'Documents', 'Figure Forge')
    os.makedirs(path, exist_ok=True)
    return path
