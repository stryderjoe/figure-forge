"""Check that the newer games' faces move with their clips, in headless Microsoft Edge (no window).

    python tests/check_faces.py <out folder> <game id>:<character id>[:<clip>] ...

e.g. lb2:joker/joker lmsh:ironman/ironman_mk6. Reads the games from tests/games.json. For each character: its
face pieces carry the game's blend shapes (one per facial target), a clip with a face block keys their weights,
and the face looks different at two frames of the clip with different weights (pictures of the head at both,
in <out folder>). Needs websocket-client.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))
from check_ui import EDGE, Page, free_port  # noqa: E402
import check_ui  # noqa: E402
from forge import server, settings  # noqa: E402

failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(out, *specs):
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    check_ui.out = out
    with open(os.path.join(ROOT, 'tests', 'games.json')) as f:
        games = json.load(f)
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_faces_')
    wanted = sorted({s.split(':')[0] for s in specs})
    settings.save({'games': {g: games[g] for g in wanted}, 'exports': os.path.join(out, 'exports')})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    port = free_port()
    profile = tempfile.mkdtemp(prefix='ff_edge_')
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={profile}',
                             '--window-size=1200,900', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                             '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*', 'about:blank'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        for spec in specs:
            gid, cid, *clip = spec.split(':')
            base = f'{gid}:{cid}'
            page.call('Page.navigate', url=url + '?base=' + base)
            check(page.wait("document.body && document.body.dataset.ready === '1'", 300), f'{base} loaded')
            time.sleep(1.0)
            shapes = page.js("""(() => { let n = 0, t = 0; FF.viewer.figure.traverse(o => {
                if (o.isMesh && o.morphTargetDictionary) { n++; t += Object.keys(o.morphTargetDictionary).length; } });
                return [n, t]; })()""")
            check(shapes[0] > 0, f'{base}: {shapes[0]} face pieces with {shapes[1]} blend shapes')
            # a clip whose face moves: the one named, else the first whose weights change
            got = page.js("""(async () => {
                const want = %s;
                const list = (await (await fetch('/api/clips?char=%s')).json()).clips.map(c => c.name);
                for (const name of (want ? [want] : list)) {
                    const c = await (await fetch('/api/clip?char=%s&name=' + encodeURIComponent(name))).json();
                    const m = c.morphs || {};
                    for (const [id, w] of Object.entries(m)) {
                        const lo = Math.min(...w), hi = Math.max(...w);
                        if (hi - lo > 0.5) return { name, id, low: w.indexOf(lo), high: w.indexOf(hi), frames: c.frames, n: Object.keys(m).length };
                    }
                    if (want) return { name, n: Object.keys(m).length };
                }
                return null;
            })()""" % (json.dumps(clip[0] if clip else None), base, base))
            check(bool(got and got.get('id') is not None), f'{base}: a clip moves the face {got}')
            if not got or got.get('id') is None:
                continue
            page.js(f"(async () => {{ FF.S.clipSource = '{base}'; await FF.playClip('{base}', {json.dumps(got['name'])}); "
                    f"FF.viewer.playing = false; }})()")
            time.sleep(0.5)
            # look at the face from the front, close up (after each seek: clips move the figure)
            page.js("""window.__aim = () => { const V = FF.viewer, T = FF.THREE;
                V.figure.updateMatrixWorld(true);
                const box = new T.Box3().setFromObject(V.figure, true), size = box.getSize(new T.Vector3());
                let p = null;
                V.figure.traverse(o => { if (!p && o.isMesh && o.morphTargetDictionary) {
                    p = new T.Box3().setFromObject(o, true).getCenter(new T.Vector3()); } });
                p = p || box.getCenter(new T.Vector3());
                V.controls.target.copy(p); V.camera.position.set(p.x, p.y, p.z + Math.max(size.y, 0.2) * 0.45);
                V.camera.lookAt(p); V.controls.update(); return p.toArray(); }""")
            pics, infl = [], []
            for k in ('low', 'high'):
                frame = got[k]
                infl.append(page.js(f"""(() => {{ const V = FF.viewer; V.seek({frame} / Math.max(1, {got['frames']} - 1)); window.__aim();
                    let w = 0; V.figure.traverse(o => {{ if (o.morphTargetDictionary && o.morphTargetDictionary['{got['id']}'] !== undefined)
                        w = Math.max(w, o.morphTargetInfluences[o.morphTargetDictionary['{got['id']}']]); }}); return w; }})()"""))
                time.sleep(0.6)
                # the 3D view only (the panels beside it load pictures of their own meanwhile)
                data = page.js("(() => { const V = FF.viewer; V.renderer.render(V.scene, V.camera); "
                               "return V.renderer.domElement.toDataURL('image/png'); })()")
                name = f"face_{gid}_{cid.replace('/', '_')}_{k}.png"
                import base64
                with open(os.path.join(out, name), 'wb') as f:
                    f.write(base64.b64decode(data.split(',', 1)[1]))
                pics.append(os.path.join(out, name))
            check(infl[1] - infl[0] > 0.4, f"{base}: target {got['id']} weight {infl[0]:.2f} at frame {got['low']}, "
                                           f"{infl[1]:.2f} at frame {got['high']} of {got['name']}")
            from PIL import Image, ImageChops
            a, b = (Image.open(p).convert('RGB') for p in pics)
            diff = ImageChops.difference(a, b).convert('L').point(lambda v: 255 if v > 24 else 0)
            changed = sum(diff.histogram()[255:])
            check(changed > 50, f'{base}: the face looks different between the two frames ({changed} pixels)')
            # the GLB export keeps the blend shapes and the clip's weights
            page.js(f"(async () => {{ FF.S.exportName = 'faces_{gid}'; FF.viewer.playing = false; await FF.doExport('glb'); }})()")
            glb_path = os.path.join(out, 'exports', f'faces_{gid}.glb')
            if page.wait("!!FF.S.lastSaved", 120) and os.path.exists(glb_path):
                import struct
                with open(glb_path, 'rb') as f:
                    data = f.read()
                n = struct.unpack_from('<I', data, 12)[0]
                doc = json.loads(data[20:20 + n])
                targets = sum(len(p.get('targets', ())) for m in doc.get('meshes', ()) for p in m['primitives'])
                weights = sum(1 for a in doc.get('animations', ()) for c in a['channels'] if c['target']['path'] == 'weights')
                check(targets > 0 and weights > 0, f'{base}: the GLB has {targets} blend shapes and {weights} animated weight channels')
            else:
                check(False, f'{base}: the GLB export was saved ({glb_path})')
            page.js("FF.S.lastSaved = null")
    finally:
        edge.kill()
        httpd.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
