"""Figure Forge's local web server: the user interface and a small JSON API over the user's extracted games.

Nothing leaves the computer: the server listens on 127.0.0.1 only. Game files are read, never written.
"""
import io
import json
import mimetypes
import os
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import anim, catalog, parts, settings
from .formats import classic

UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ui')


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.games = {}
        self.index = {}             # game id -> catalogue index (see build_index)
        self.indexing = {}          # game id -> (done, total) while building
        self.textures = {}
        self.load()

    def load(self):
        cfg = settings.load()
        self.cfg = cfg
        self.games = {}
        for gid, root in cfg.get('games', {}).items():
            if root and os.path.isdir(root):
                try:
                    self.games[gid] = catalog.Game(gid, root)
                except classic.FormatError:
                    pass

    def game_of(self, char_id):
        gid = char_id.split(':', 1)[0]
        if gid not in self.games:
            raise KeyError(f'game {gid} is not set up')
        return self.games[gid]


STATE = None


def build_index(state, gid):
    """Per game: every character with its humanoid flag, scheme and parts (cached on disk by file times)."""
    game = state.games[gid]
    entries = game.characters()
    stamp = sum(int(os.path.getmtime(e['model'])) for e in entries) + len(entries)
    path = settings.cache_path(f'index_{gid}.json')
    try:
        with open(path, encoding='utf-8') as f:
            cached = json.load(f)
        if cached.get('stamp') == stamp and cached.get('version') == 4:
            return cached
    except (OSError, ValueError):
        pass
    chars = []
    for i, e in enumerate(entries):
        state.indexing[gid] = (i, len(entries))
        try:
            c = game.character(e['id'])
            plist = catalog.parts(c)
            chars.append({'id': e['id'], 'name': e['name'], 'folder': e['folder'], 'bones': len(c.bones),
                          'humanoid': catalog.is_humanoid(c), 'scheme': catalog.scheme(c),
                          'parts': [{k: p[k] for k in ('id', 'slot', 'label', 'default')} for p in plist]})
        except Exception as ex:      # one unreadable model must not stop the index
            chars.append({'id': e['id'], 'name': e['name'], 'folder': e['folder'], 'error': str(ex)[:200],
                          'humanoid': False, 'parts': []})
    state.indexing.pop(gid, None)
    data = {'version': 4, 'stamp': stamp, 'game': gid, 'characters': chars}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    return data


def texture_png(state, char_id, index):
    key = (char_id, index)
    if key in state.textures:
        return state.textures[key]
    from PIL import Image
    char = state.game_of(char_id).character(char_id)
    t = char.nu.textures[index]
    data = char.nu.d[t['data_offset']:t['data_offset'] + t['size']]
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode not in ('RGBA', 'RGB'):
        im = im.convert('RGBA')
    out = io.BytesIO()
    im.save(out, 'PNG')
    png = out.getvalue()
    state.textures[key] = png
    if len(state.textures) > 400:
        state.textures.pop(next(iter(state.textures)))
    return png


class Handler(BaseHTTPRequestHandler):
    server_version = 'FigureForge'

    def log_message(self, fmt, *args):      # quiet
        pass

    def send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def static(self, root, rel):
        path = os.path.normpath(os.path.join(root, rel))
        if not path.startswith(os.path.normpath(root)) or not os.path.isfile(path):
            return self.send(404, {'error': 'not found'})
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if path.endswith('.js'):
            ctype = 'text/javascript'
        with open(path, 'rb') as f:
            self.send(200, f.read(), ctype)

    def body_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ('/', '/index.html'):
                return self.static(UI, 'index.html')
            if url.path.startswith('/ui/'):
                return self.static(UI, url.path[4:])
            if url.path.startswith('/ldraw/'):
                lib = STATE.cfg.get('ldraw', '')
                return self.static(lib, url.path[7:]) if lib else self.send(404, {'error': 'no LDraw library set'})
            if url.path.startswith('/api/'):
                if url.path[5:] in ('base', 'part', 'texture', 'clips', 'clip'):
                    # one at a time: the character cache and part lists are shared between request threads
                    with STATE.lock:
                        return self.api_get(url.path[5:], q)
                return self.api_get(url.path[5:], q)
            return self.send(404, {'error': 'not found'})
        except KeyError as e:
            return self.send(404, {'error': f'not found: {e}'})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {'error': str(e)})

    def api_get(self, route, q):
        st = STATE
        if route == 'settings':
            return self.send(200, {'settings': st.cfg, 'games': {gid: {'title': t, 'ready': gid in st.games}
                                                                 for gid, t in catalog.GAMES.items()},
                                   'blender': settings.find_blender(st.cfg)})
        if route == 'characters':
            gid = q['game']
            if gid not in st.games:
                return self.send(400, {'error': f'{gid} is not set up'})
            return self.send(200, build_index(st, gid))
        if route == 'progress':
            return self.send(200, {g: {'done': d, 'total': t} for g, (d, t) in st.indexing.items()})
        if route == 'base':
            char = st.game_of(q['char']).character(q['char'])
            return self.send(200, parts.skeleton_json(char))
        if route == 'part':
            base = st.game_of(q['base']).character(q['base'])
            game = st.game_of(q['id'])
            return self.send(200, parts.part_json(game, q['id'], base, model_space=q.get('space') == 'model'))
        if route == 'texture':
            return self.send(200, texture_png(st, q['char'], int(q['index'])), 'image/png')
        if route == 'clips':
            char = st.game_of(q['char']).character(q['char'])
            return self.send(200, {'char': q['char'], 'clips': [{k: c[k] for k in ('name', 'fps', 'action')}
                                                                 for c in anim.clip_list(char)]})
        if route == 'clip':
            char = st.game_of(q['char']).character(q['char'])
            clip = next((c for c in anim.clip_list(char) if c['name'] == q['name']), None)
            if clip is None:
                raise KeyError(q['name'])
            target = q.get('target')
            if target and target != q['char']:
                tchar = st.game_of(target).character(target)
                return self.send(200, anim.retarget_json(char, tchar, clip['file'], clip['fps']))
            return self.send(200, anim.clip_json(char, clip['file'], clip['fps']))
        return self.send(404, {'error': f'no such call: {route}'})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            route = url.path[5:] if url.path.startswith('/api/') else ''
            if route == 'settings':
                data = self.body_json()
                cfg = settings.load()
                for gid, root in (data.get('games') or {}).items():
                    if gid in catalog.GAMES:
                        cfg.setdefault('games', {})[gid] = root
                for key in ('ldraw', 'blender', 'exports'):
                    if key in data:
                        cfg[key] = data[key]
                settings.save(cfg)
                STATE.load()
                return self.send(200, {'ok': True, 'ready': list(STATE.games)})
            if route == 'convert':
                return self.convert()
            if route == 'save':
                return self.save_file(parse_qs(url.query).get('name', ['figure'])[0])
            return self.send(404, {'error': 'no such call'})
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {'error': str(e)})

    def save_file(self, name):
        """Write an export into the export folder (Settings, else Documents/Figure Forge)."""
        name = os.path.basename(name).strip() or 'figure'
        folder = settings.export_folder(STATE.cfg)
        n = int(self.headers.get('Content-Length') or 0)
        data = self.rfile.read(n)
        stem, ext = os.path.splitext(name)
        if ext.lower() == '.json' and stem.lower().endswith('.forge'):
            stem, ext = stem[:-6], '.forge.json'
        path = os.path.join(folder, stem + ext)
        k = 2
        while os.path.exists(path):
            path = os.path.join(folder, f'{stem} ({k}){ext}')
            k += 1
        with open(path, 'wb') as f:
            f.write(data)
        return self.send(200, {'path': path, 'bytes': len(data)})

    def convert(self):
        """GLB from the viewer -> FBX, .blend or Collada through Blender in the background."""
        fmt = (self.headers.get('X-Format') or '').lower()
        if fmt not in ('fbx', 'blend', 'dae', 'usd'):
            return self.send(400, {'error': 'format must be fbx, blend, dae or usd'})
        blender = settings.find_blender(STATE.cfg)
        if not blender:
            return self.send(400, {'error': 'Blender was not found: set its path in Settings'})
        n = int(self.headers.get('Content-Length') or 0)
        glb = self.rfile.read(n)
        work = tempfile.mkdtemp(prefix='figureforge_')
        src = os.path.join(work, 'figure.glb')
        dst = os.path.join(work, f'figure.{fmt}')
        with open(src, 'wb') as f:
            f.write(glb)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blender_convert.py')
        started = time.time()
        run = subprocess.run([blender, '-b', '--factory-startup', '--python', script, '--', src, dst],
                             capture_output=True, text=True, timeout=600,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not os.path.exists(dst):
            tail = (run.stdout + run.stderr)[-1500:]
            return self.send(500, {'error': 'Blender could not convert the figure', 'log': tail})
        with open(dst, 'rb') as f:
            data = f.read()
        self.send(200, data, 'application/octet-stream')
        print(f'converted to {fmt} in {time.time() - started:.1f} s')


def serve(port=0):
    global STATE
    STATE = State()
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return httpd
