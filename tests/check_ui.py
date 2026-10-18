"""Drive the user interface in headless Microsoft Edge (no window) and check every step.

    python tests/check_ui.py <out folder> <lb1 root> <indy root> <tcs root>

Needs websocket-client. Writes screenshots and exports to <out folder>; prints ok / FAIL lines.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
out = None
from forge import server, settings  # noqa: E402
import websocket  # noqa: E402

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Page:
    def __init__(self, port):
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json'))
                page = next(t for t in tabs if t['type'] == 'page')
                break
            except Exception:
                time.sleep(0.2)
        self.ws = websocket.create_connection(page['webSocketDebuggerUrl'], timeout=600, suppress_origin=True)
        self.n = 0
        self.call('Runtime.enable')
        self.call('Page.enable')

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get('id') == self.n:
                if 'error' in msg:
                    raise RuntimeError(msg['error'])
                return msg.get('result', {})
            if msg.get('method') == 'Runtime.exceptionThrown':
                print('  page error:', msg['params']['exceptionDetails'].get('exception', {}).get('description', '')[:300])
            if msg.get('method') == 'Runtime.consoleAPICalled' and msg['params']['type'] == 'error':
                print('  console error:', ' '.join(str(a.get('value', a.get('description', ''))) for a in msg['params']['args'])[:300])

    def js(self, expr):
        r = self.call('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'].get('exception', {}).get('description', str(r['exceptionDetails']))[:600])
        return r['result'].get('value')

    def wait(self, expr, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr):
                return True
            time.sleep(0.25)
        return False

    def shot(self, name):
        data = self.call('Page.captureScreenshot', format='png')['data']
        with open(os.path.join(out, name), 'wb') as f:
            f.write(base64.b64decode(data))


def main():
    global out
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_ui_')
    exports = os.path.join(out, 'exports')
    shutil.rmtree(exports, ignore_errors=True)
    cfg = {'games': dict(zip(('lb1', 'indy', 'tcs'), sys.argv[2:5])), 'exports': exports}
    settings.save(cfg)
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    port = free_port()
    profile = tempfile.mkdtemp(prefix='ff_edge_')
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}', f'--user-data-dir={profile}',
                             '--window-size=1440,900', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
                             '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*', 'about:blank'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        page.call('Page.navigate', url=url + '?base=lb1:batman/batman')
        check(page.wait("document.body && document.body.dataset.ready === '1'", 300), 'app loaded Batman as the body')
        time.sleep(1.5)
        n = page.js("(() => { let n = 0; FF.viewer.figure.traverse(o => { if (o.isMesh) n++; }); return n; })()")
        check(n > 10, f'Batman built from {page.js("FF.S.parts.length")} parts, {n} meshes')
        page.shot('1_body.png')

        # parts across games: Luke's head and hair, Indy's hat
        got = page.js("""(async () => {
            const find = (gid, name, slot) => { const c = FF.S.index[gid].find(c => c.name.toLowerCase() === name);
                const p = c && c.parts.find(p => p.slot === slot && p.default); return p && p.id; };
            const ids = [find('tcs', 'lukeskywalker_tatooine', 'head'), find('tcs', 'lukeskywalker_tatooine', 'hair'),
                         find('indy', 'indianajones', 'headgear')].filter(Boolean);
            for (const id of ids) await FF.addPart(id);
            return { ids, slots: FF.S.parts.map(p => p.slot) };
        })()""")
        check(len(got['ids']) == 3 and got['slots'].count('head') == 1, f'swapped in parts from Star Wars and Indy: {got["ids"]}')
        page.js("FF.S.step = 'parts'; FF.S.slotFilter = 'head'; FF.render(); FF.viewer.frameFigure()")
        page.wait("document.querySelectorAll('#panel .tile img.thumb[src]').length >= 6", 120)
        got = page.js("""(async () => {
            const tiles = document.querySelector('#panel .tiles');
            tiles.scrollTop = 900; await new Promise(r => setTimeout(r, 300));
            const before = tiles.scrollTop;
            const r = tiles.getBoundingClientRect();
            const tile = [...tiles.querySelectorAll('.tile')].find(t => { const b = t.getBoundingClientRect(); return b.top > r.top && b.bottom < r.bottom && !t.classList.contains('on'); });
            tile.click();
            // wait for the part to be fitted (up to 10 s), not a fixed time
            for (let i = 0; i < 40 && (tile.classList.contains('loading') || !FF.S.parts.some(p => p.id === tile.dataset.id)); i++)
              await new Promise(r => setTimeout(r, 250));
            await new Promise(r => setTimeout(r, 300));
            const after = document.querySelector('#panel .tiles').scrollTop;
            return { before, after, on: tile.classList.contains('on'), thumbs: tiles.querySelectorAll('img.thumb[src]').length,
                     panelScroll: document.querySelector('#panel').scrollHeight <= document.querySelector('#panel').clientHeight + 1,
                     tiles: tiles.children.length };
        })()""")
        check(got['before'] > 0 and abs(got['after'] - got['before']) < 2 and got['on'],
              f'parts list keeps its place when a part is put on ({got})')
        check(got['panelScroll'], 'the parts panel does not scroll itself: only its list does')
        page.shot('2_parts_scrolled.png')
        page.js("(async () => { for (const id of FF.S.parts.filter(p => p.slot === 'head').map(p => p.id)) FF.removePart(id); })()")
        page.js("FF.S.parts.length")
        page.js("""(async () => { const c = FF.S.index.tcs.find(c => c.name.toLowerCase() === 'lukeskywalker_tatooine');
            await FF.addPart(c.parts.find(p => p.slot === 'head' && p.default).id); FF.render(); })()""")
        time.sleep(1.0)
        page.shot('2_parts.png')

        # colours: the torso red
        key = page.js("""(() => { const m = FF.figureMaterials().find(m => [...m.parts].some(p => /torso|body/i.test(p)));
            FF.setColour(m.key, [0.706, 0, 0]); FF.S.step = 'colours'; FF.render(); return m.key; })()""")
        check(bool(key), f'recoloured {key}')
        time.sleep(0.8)
        page.shot('3_colours.png')

        # prints: a generated picture on the torso's print, placed where the torso's faces take their print
        key = page.js("""(() => { let k = null; FF.viewer.figure.traverse(o => {
            if (!k && o.isMesh && o.userData.slot === 'torso' && o.material.map) k = o.userData.materialKey; }); return k; })()""")
        ok = page.js("""(async () => {
            const KEY = %s;
            FF.S.step = 'prints'; FF.S.printKey = KEY; FF.render();
            await new Promise(r => setTimeout(r, 400));
            const c = document.createElement('canvas'); c.width = c.height = 128; const g = c.getContext('2d');
            g.fillStyle = '#ffd400'; g.beginPath(); g.arc(64, 64, 56, 0, 7); g.fill();
            g.fillStyle = '#000'; g.font = 'bold 70px sans-serif'; g.textAlign = 'center'; g.fillText('F', 64, 88);
            const blob = await new Promise(r => c.toBlob(r)); const file = new File([blob], 'logo.png');
            await FF.prints.addImage(file);
            const s = FF.prints.state(); const L = s.layers[0];
            let su = 0, sv = 0, n = 0;
            FF.viewer.figure.traverse(o => { if (o.isMesh && o.userData.slot === 'torso' && o.userData.materialKey === KEY) {
                const uv = o.geometry.attributes.uv, nr = o.geometry.attributes.normal;
                for (let i = 0; i < uv.count; i++) if (nr.getZ(i) > 0.8) { su += uv.getX(i); sv += uv.getY(i); n++; } } });
            L.x = su / n * s.w; L.y = (1 - sv / n) * s.h; L.scale = 0.25 * s.w / 128; L.rot = 15; FF.prints.apply();
            return s.layers.length === 1 && !!FF.viewer.materials.get(KEY).map.isCanvasTexture;
        })()""" % json.dumps(key))
        check(bool(ok), 'print editor put an uploaded picture on the torso')
        time.sleep(0.8)
        page.shot('4_prints.png')

        # render styles
        for style in ('studio', 'flat', 'classic'):
            page.js(f"FF.viewer.setStyle('{style}'); FF.viewer.restyleAll((k, old) => FF.S.colours[k] || old.userData.info.colour)")
            time.sleep(0.4)
            page.shot(f'5_render_{style}.png')
        check(page.js("FF.viewer.styleName") == 'classic', 'render styles switch')

        # animation: Batman's own run, then Luke's walk retargeted
        page.js("FF.playClip('lb1:batman/batman', 'run')")
        time.sleep(0.6)
        moved = page.js("""(() => { FF.viewer.playing = false; let most = 0;
            const b = FF.viewer.bones.find(b => b.userData.common === 'l_thigh');
            for (const f of [0.1, 0.3, 0.5, 0.7, 0.9]) { FF.viewer.seek(f); most = Math.max(most, 1 - Math.abs(b.quaternion.dot(b.userData.bind.q))); }
            FF.viewer.playing = true; return most; })()""")
        check(moved > 1e-3, f'Batman run plays (left thigh turned {moved:.4f} from rest)')
        page.js("FF.viewer.playing = false; FF.viewer.seek(0.3); FF.S.step = 'animate'; FF.render()")
        time.sleep(1.0)
        page.shot('6_anim_run.png')
        page.js("FF.playClip('tcs:lukeskywalker/lukeskywalker_tatooine', 'walk')")
        time.sleep(0.5)
        page.js("FF.viewer.playing = false; FF.viewer.seek(0.25)")
        time.sleep(0.5)
        check(page.js("!!FF.viewer.clip && FF.viewer.clip.name.toLowerCase() === 'walk'"), "Luke's walk retargeted onto the figure")
        page.shot('7_anim_retarget.png')

        # export everything
        page.js("FF.S.exportName = 'test_figure'; FF.S.step = 'export'; FF.render()")
        for fmt in ('glb', 'gltf', 'obj', 'stl', 'ply', 'png', 'fbx', 'blend'):
            err = page.js(f"FF.doExport('{fmt}').then(() => '').catch(e => e.message)")
            check(not err, f'export {fmt}' + (f': {err}' if err else ''))
        page.js("(async () => { await fetch('/api/save?name=test_figure.forge.json', {method: 'POST', body: JSON.stringify(FF.projectJson())}); })()")
        files = sorted(os.listdir(exports)) if os.path.isdir(exports) else []
        check(len(files) >= 9, f'files written: {files}')
        page.shot('8_export.png')

        # reopen the project in a fresh page
        proj = json.load(open(os.path.join(exports, 'test_figure.forge.json')))
        page.call('Page.navigate', url=url)
        page.wait("document.body && document.body.dataset.ready === '1'", 300)
        page.js(f"FF.openProject({json.dumps(proj)})")
        time.sleep(1.5)
        check(page.js("FF.S.parts.length") == len(proj['parts']) and page.js("FF.S.clipName") == proj['clip']['name'],
              'project reopens with the same parts and clip')
        page.shot('9_reopened.png')

        # other games as the body, with their own clips
        for gid, cid, clip in (('tcs', 'tcs:lukeskywalker/lukeskywalker_tatooine', 'walk'),
                               ('indy', 'indy:indianajones/indianajones', 'run')):
            page.js(f"(async () => {{ FF.S.customs = []; FF.S.colours = {{}}; FF.prints = null; await FF.setBase('{cid}'); "
                    f"FF.S.clipSource = '{cid}'; await FF.playClip('{cid}', '{clip}'); FF.viewer.playing = false; FF.viewer.seek(0.3); "
                    f"FF.S.step = 'animate'; FF.render(); FF.viewer.frameFigure(); }})()")
            time.sleep(1.2)
            n = page.js("(() => { let n = 0; FF.viewer.figure.traverse(o => { if (o.isMesh) n++; }); return n; })()")
            check(n > 5, f'{gid} body {cid} with its own {clip}: {n} meshes')
            page.shot(f'10_{gid}_base.png')

        # uploads: an OBJ box held in the right hand, an LDraw plate skinned at the chest
        obj = ('v -0.02 0 -0.02\\nv 0.02 0 -0.02\\nv 0.02 0 0.02\\nv -0.02 0 0.02\\nv -0.02 0.2 -0.02\\nv 0.02 0.2 -0.02\\n'
               'v 0.02 0.2 0.02\\nv -0.02 0.2 0.02\\nf 1 2 3 4\\nf 5 8 7 6\\nf 1 5 6 2\\nf 2 6 7 3\\nf 3 7 8 4\\nf 4 8 5 1\\n')
        ldr = ('0 test plate\\n4 4 -40 0 -20 40 0 -20 40 0 20 -40 0 20\\n4 4 -40 -8 -20 40 -8 -20 40 -8 20 -40 -8 20\\n')
        got = page.js("""(async () => {
            const add = async (name, ext, text, bone, mode, unit) => {
                const c = { id: 'custom:' + name, name, ext, data: 'data:text/plain;base64,' + btoa(text), unit, bone, mode,
                            t: { px: 0, py: 0, pz: 0, rx: 0, ry: 0, rz: 0, s: 1 } };
                FF.S.customs.push(c); await FF.attachCustom(c);
                return (FF.viewer.parts.get(c.id) || []).length;
            };
            return [await add('sword.obj', 'obj', %s, 'r_hand', 'rigid', 'm'), await add('plate.ldr', 'ldr', %s, 'chest', 'skin', 'ldu')];
        })()""" % (json.dumps(obj.replace('\\n', '\n')), json.dumps(ldr.replace('\\n', '\n'))))
        check(got and all(got), f'uploaded OBJ (rigid on the hand) and LDraw (skinned) parts: {got}')
        # a skinned upload takes the body's own game weights: a copy of the body's skinned mesh, uploaded, gets
        # back (nearly) the weights it has in the game
        got = page.js("""(() => {
            const V = FF.viewer;
            let src = null;
            V.figure.traverse(o => { if (!src && o.isSkinnedMesh && o.userData.slot !== 'custom' && o.geometry.attributes.position.count > 100) src = o; });
            if (!src) return null;
            V.restPose();
            const g = new FF.THREE.BufferGeometry();
            g.setAttribute('position', src.geometry.attributes.position.clone());
            g.setIndex(src.geometry.index ? src.geometry.index.clone() : null);
            const m = new FF.THREE.Mesh(g, new FF.THREE.MeshBasicMaterial());
            m.applyMatrix4(src.matrixWorld);
            const objs = V.addCustom('custom:copy', m, 'chest', 'skin');
            const a = src.geometry.attributes, b = objs[0].geometry.attributes, n = a.position.count;
            let same = 0, sums = 0, blended = 0;
            const top = (si, sw, i) => { let k = 0; for (let c = 1; c < 4; c++) if (sw.getComponent(i, c) > sw.getComponent(i, k)) k = c; return si.getComponent(i, k); };
            for (let i = 0; i < n; i++) {
                if (top(a.skinIndex, a.skinWeight, i) === top(b.skinIndex, b.skinWeight, i)) same++;
                let s = 0, used = 0; for (let c = 0; c < 4; c++) { s += b.skinWeight.getComponent(i, c); if (b.skinWeight.getComponent(i, c) > 0.05) used++; }
                if (Math.abs(s - 1) < 1e-4) sums++;
                if (used > 1) blended++;
            }
            V.removePart('custom:copy');
            return [n, same, sums, blended];
        })()""")
        check(bool(got) and got[1] >= 0.9 * got[0] and got[2] == got[0],
              f"a skinned upload copies the body's game weights (vertices, same main bone, weights sum to 1, blended): {got}")
        page.js("FF.viewer.playing = true")
        time.sleep(0.4)
        page.js("FF.viewer.playing = false; FF.S.step = 'rig'; FF.render()")
        time.sleep(0.6)
        page.shot('11_uploads.png')

        # the animation editor on Batman: pose and key an arm, a face expression, a new bone, export
        page.js("(async () => { FF.S.customs = []; FF.S.customBones = []; FF.prints = null; await FF.setBase('lb1:batman/batman'); "
                "FF.S.step = 'animate'; FF.S.animMode = 'create'; FF.editor.start(); FF.anim.drawBar(); FF.render(); FF.viewer.frameFigure(); })()")
        time.sleep(1.0)
        got = page.js("""(async () => {
            const E = FF.editor, V = FF.viewer, THREE_q = (b) => b.quaternion.toArray();
            const arm = V.bones.find(b => b.userData.common === 'r_upperarm');
            E.select(arm);
            E.setFrame(0); E.key(arm);
            E.setFrame(24); arm.rotateZ(1.2); arm.updateMatrixWorld(true); E.key(arm);
            E.setFrame(12);
            const mid = arm.quaternion.clone();
            const restDot = Math.abs(mid.dot(arm.userData.bind.q));
            // a face expression: the current print, keyed from frame 20
            const fk = [...FF.faces.sets.keys()][0] || (() => { let k = null; V.figure.traverse(o => { if (!k && o.isMesh && o.userData.slot === 'face' && o.material.map) k = o.userData.materialKey; }); return k; })();
            const src = FF.faces.snapshot(fk);
            const idx = await FF.faces.add(fk, 'Wink', src);
            E.keyFace(fk, idx, 20);
            E.setFrame(22);
            const swapped = V.materials.get(fk).map === FF.faces.textureFor(fk, idx);
            E.setFrame(5);
            const back = V.materials.get(fk).map === FF.faces.textureFor(fk, 0);
            // a new bone on the pelvis
            const before = V.bones.length;
            const pelvis = V.bones.find(b => b.userData.common === 'pelvis');
            V.addBone('tail_1', pelvis.name, [0, 0, -0.04]); FF.S.customBones = [{ name: 'tail_1', parent: pelvis.name, offset: [0, 0, -0.04] }];
            E.buildHandles();
            E.setFrame(12);
            return { restDot, keys: E.keyFrames(arm), swapped, back, bones: V.bones.length - before, fk, idx };
        })()""")
        check(got['keys'] == [0, 24] and got['restDot'] < 0.9999, f'arm keyed at 0 and 24, turned between them ({got})')
        check(got['swapped'] and got['back'], 'face expression shows from its key and not before')
        check(got['bones'] == 1, 'a new bone was added to the skeleton')
        time.sleep(0.8)
        page.shot('12_editor.png')
        page.js("FF.S.exportName = 'editor_figure'")
        for fmt in ('glb', 'fbx'):
            err = page.js(f"FF.doExport('{fmt}').then(() => '').catch(e => e.message)")
            check(not err, f'editor animation exported as {fmt}' + (f': {err}' if err else ''))
        # a classic clip copied into the editor
        n = page.js("""(async () => { await FF.playClip('lb1:batman/batman', 'run'); FF.editor.fromViewerClip(2);
            FF.S.animMode = 'create'; FF.anim.drawBar(); FF.render();
            return Object.keys(FF.editor.anim.keys).length; })()""")
        check(n > 20, f'classic run copied into the editor: {n} animated bones')
        time.sleep(0.6)
        page.shot('13_editor_from_clip.png')
    finally:
        edge.kill()
        httpd.shutdown()
    print(f'SUMMARY {"all passed" if not failures else str(len(failures)) + " failed"}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
