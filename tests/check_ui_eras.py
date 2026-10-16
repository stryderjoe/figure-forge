"""Drive the user interface on the newer eras' bodies in headless Microsoft Edge (no window) and check each step.

    python tests/check_ui_eras.py <out folder>

Uses tests/games.json. For each newer game (LEGO Batman 2, Marvel, Pirates, LEGO Batman 3): the body loads,
a part from another game of the era and one from a classic game are put on, the torso is recoloured and given
a print, the body's own idle plays and a clip of another game is retargeted onto it, and the figure exports
(GLB, FBX, OBJ, PNG). Prints ok / FAIL lines and screenshots into <out folder>.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_ui import EDGE, Page, free_port  # noqa: E402

failures = []
# body, a part from another game of the era (character, slot), another game's clip (character, clip)
CASES = [
    ('lb2:batman/batman', ('lmsh:thor/thor', 'torso'), ('lmsh:thor/thor', 'run')),
    ('lmsh:ironman/ironman', ('lb2:joker/joker', 'torso'), ('lb2:batman/batman', 'run')),
    ('pirates:jacksparrow/jacksparrow_captain', ('lb2:robin/robin', 'legs'), ('lb2:batman/batman', 'run')),
    ('lb3:batman/batman', ('lmsh:thor/thor', 'torso'), ('lb2:batman/batman', 'run')),
]


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(out):
    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    os.environ.setdefault('FIGUREFORGE_HOME', tempfile.mkdtemp(prefix='ff_uie_'))   # reuse a cache when given
    import check_ui
    check_ui.out = out
    from forge import server, settings
    exports = os.path.join(out, 'exports')
    shutil.rmtree(exports, ignore_errors=True)
    with open(os.path.join(ROOT, 'tests', 'games.json')) as f:
        settings.save({'games': json.load(f), 'exports': exports})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    port = free_port()
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}',
                             f'--user-data-dir={tempfile.mkdtemp(prefix="ff_edge_")}', '--window-size=1440,900',
                             '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-first-run',
                             '--remote-allow-origins=*', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        page.call('Page.navigate', url=url)
        check(page.wait("document.body && document.body.dataset.ready === '1'", 3000), 'app loaded with every game')
        # the modes: one per era, each listing only its games' characters; Settings has every game
        got = page.js("""(async () => {
            const sel = document.querySelector('#era');
            const modes = [...sel.options].map(o => o.value);
            const games = {};
            for (const m of modes) {
                sel.value = m; sel.dispatchEvent(new Event('change')); await new Promise(r => setTimeout(r, 300));
                FF.S.step = 'body'; FF.render();
                games[m] = [...new Set([...document.querySelectorAll('#panel .tile small')].map(t => t.textContent.toLowerCase()))].sort();
            }
            sel.value = 'all'; sel.dispatchEvent(new Event('change')); await new Promise(r => setTimeout(r, 300));
            document.querySelector('#btn-settings').click();
            const fields = document.querySelectorAll('#game-fields input').length, installs = document.querySelectorAll('#install-fields input').length;
            document.querySelector('#settings').close();
            return { modes, games, fields, installs };
        })()""")
        check(got['modes'] == ['all', 'classic', 'nxg', 'dx11'], f'modes: {got["modes"]}')
        check(got['games'].get('classic') == ['indy', 'lb1', 'tcs'] and got['games'].get('nxg') == ['lb2', 'lmsh', 'pirates']
              and got['games'].get('dx11') == ['lb3'], f'each mode lists its own games: {got["games"]}')
        check(got['fields'] == 7 and got['installs'] == 7, f'Settings has a folder and an install for every game ({got["fields"]}, {got["installs"]})')
        meshes = "(() => { let n = 0; FF.viewer.figure.traverse(o => { if (o.isMesh) n++; }); return n; })()"
        for base, (donor, slot), (clip_char, clip) in CASES:
            gid = base.split(':')[0]
            err = page.js(f"FF.setBase('{base}').then(() => '').catch(e => e.message)")
            n = page.js(meshes)
            check(not err and n > 5, f'{gid}: {base} loads ({n} meshes){" " + err if err else ""}')
            # a part of another game of the era, and a classic head
            got = page.js("""(async () => {
                const find = (cid, slot) => { const g = cid.split(':')[0]; const c = (FF.S.index[g] || []).find(c => c.id === cid);
                    const p = c && c.parts.find(p => p.slot === slot && p.default); return p && p.id; };
                const ids = [find(%s, %s), find('lb1:batman/batman', 'head')].filter(Boolean);
                for (const id of ids) await FF.addPart(id);
                return { ids, on: ids.filter(id => FF.S.parts.some(p => p.id === id)).length };
            })()""" % (json.dumps(donor), json.dumps(slot)))
            check(got['on'] == 2, f'{gid}: parts from {donor} ({slot}) and LEGO Batman (head) put on: {got["ids"]}')
            key = page.js("""(() => { const m = FF.figureMaterials().find(m => [...m.parts].some(p => /torso|body/i.test(p)));
                if (!m) return null; FF.setColour(m.key, [0.1, 0.4, 0.9]); return m.key; })()""")
            check(bool(key), f'{gid}: recoloured {key}')
            pkey = page.js("""(() => { let k = null; FF.viewer.figure.traverse(o => {
                if (!k && o.isMesh && o.userData.slot === 'torso' && o.material.map) k = o.userData.materialKey; }); return k; })()""")
            ok = page.js("""(async () => {
                const KEY = %s; if (!KEY) return false;
                FF.S.step = 'prints'; FF.S.printKey = KEY; FF.render();
                await new Promise(r => setTimeout(r, 400));
                const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
                g.fillStyle = '#ffd400'; g.fillRect(8, 8, 48, 48);
                const blob = await new Promise(r => c.toBlob(r));
                await FF.prints.addImage(new File([blob], 'logo.png')); FF.prints.apply();
                return !!FF.viewer.materials.get(KEY).map.isCanvasTexture;
            })()""" % json.dumps(pkey))
            check(bool(ok), f'{gid}: a print on the torso')
            page.js("FF.prints = null; FF.S.step = 'animate'; FF.render()")
            # the body's own idle, then another game's clip
            moved = {}

            def named(who, want):
                # the character's own clip whose name holds the word (Idle, IDLE1, Hammer_Idle, Run ...)
                names = page.js("fetch('/api/clips?char=' + encodeURIComponent(%s)).then(r => r.json()).then(d => d.clips.map(c => c.name))"
                                % json.dumps(who))
                hits = [x for x in names if x.lower() == want] + [x for x in names if want in x.lower()]
                return hits[0] if hits else want
            idle_name, clip_name = named(base, 'idle'), named(clip_char, clip)
            for who, name in ((base, idle_name), (clip_char, clip_name)):
                err = page.js(f"FF.playClip('{who}', '{name}').then(() => '').catch(e => e.message)")
                time.sleep(0.4)
                m = page.js("""(() => { FF.viewer.playing = false; let most = 0;
                    for (const b of FF.viewer.bones) for (const f of [0.2, 0.5, 0.8]) { FF.viewer.seek(f);
                        if (b.userData.bind) most = Math.max(most, 1 - Math.abs(b.quaternion.dot(b.userData.bind.q))); }
                    return most; })()""")
                moved[name] = (err, m)
            check(not moved[idle_name][0] and moved[idle_name][1] > 1e-4, f'{gid}: its {idle_name} plays ({moved[idle_name]})')
            check(not moved[clip_name][0] and moved[clip_name][1] > 1e-3,
                  f'{gid}: {clip_char} {clip_name} retargeted ({moved[clip_name]})')
            page.js("FF.viewer.seek(0.3); FF.viewer.frameFigure()")
            time.sleep(0.6)
            page.shot(f'{gid}.png')
            page.js(f"FF.S.exportName = '{gid}_figure'; FF.S.step = 'export'; FF.render()")
            for fmt in ('glb', 'fbx', 'obj', 'png'):
                err = page.js(f"FF.doExport('{fmt}').then(() => '').catch(e => e.message)")
                check(not err, f'{gid}: export {fmt}' + (f': {err}' if err else ''))
        files = sorted(os.listdir(exports)) if os.path.isdir(exports) else []
        check(len(files) >= 4 * len(CASES), f'{len(files)} files written')
    finally:
        edge.kill()
        httpd.shutdown()
    print('SUMMARY', 'all passed' if not failures else f'{len(failures)} failed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
