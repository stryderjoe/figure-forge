"""Check "Into the game" headlessly (no window, no game run), on COPIES of the games' archives.

    python tests/check_game_export.py <work folder>

Uses tests/games.json for the extracted games and copies each installed game's GAME.DAT into
<work folder>/<game>/ as the install the export writes to. For each game: replace the body character and add a
new one through the user interface (headless Edge), then read the archive back: the files are there, the
models read, the lists name the new character; then a mod folder; then Restore original.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_ui import EDGE, Page, free_port  # noqa: E402

INSTALLS = {'lb1': r'C:\Program Files (x86)\Steam\steamapps\common\Lego Batman',
            'indy': r'C:\Program Files (x86)\Steam\steamapps\common\LEGO Indiana Jones The Original Adventures',
            'tcs': r'C:\Program Files (x86)\Steam\steamapps\common\Lego Star Wars Saga'}
BODIES = {'lb1': 'lb1:batman/batman', 'indy': 'indy:indianajones/indianajones', 'tcs': 'tcs:obiwankenobi/obiwankenobi'}
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what, flush=True)
    if not cond:
        failures.append(what)


def main(work):
    os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_gx_')
    from forge import server, settings
    from forge.formats import classic
    from forge.formats.dat import DatArchive
    games = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')))
    installs = {}
    for gid, src in INSTALLS.items():
        dst = os.path.join(work, gid)
        os.makedirs(dst, exist_ok=True)
        shutil.copy2(os.path.join(src, 'GAME.DAT'), os.path.join(dst, 'GAME.DAT'))
        for leftover in ('GAME.DAT.figureforge-backup',):
            if os.path.exists(os.path.join(dst, leftover)):
                os.remove(os.path.join(dst, leftover))
        installs[gid] = dst
    exports = os.path.join(work, 'exports')
    settings.save({'games': games, 'installs': installs, 'exports': exports})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = free_port()
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}',
                             f'--user-data-dir={tempfile.mkdtemp(prefix="ff_edge_")}', '--window-size=1200,900',
                             '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-allow-origins=*',
                             '--no-first-run', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        for gid, body in BODIES.items():
            page.call('Page.navigate', url=f'http://127.0.0.1:{httpd.server_address[1]}/?base={body}')
            page.wait("document.body && document.body.dataset.ready === '1'", 300)
            # a part from another game, so the figure is mixed
            other = 'lb1:batman/batman|20|r24' if gid != 'lb1' else 'indy:indianajones/indianajones|8|r27'
            page.js(f"FF.addPart({json.dumps(other)})")
            dat = os.path.join(installs[gid], 'GAME.DAT')
            for mode, name in (('replace', ''), ('new', f'ff_test_{gid}')):
                res = page.js(f"""(async () => {{ FF.S.step = 'export'; FF.S.gx = {{ mode: '{mode}', name: '{name}', delivery: 'patch' }};
                    await FF.gameExport(); return FF.S.gxResult; }})()""")
                check(res and not res.get('error'), f'{gid} {mode}: {res}')
            arc = DatArchive(dat)
            paths = {e.path.lower(): e for e in arc.entries}
            base_model = os.path.relpath(classic.Character.__init__ and
                                         os.path.join(games[gid], *body.split(':')[1].split('/')[:1]), games[gid])
            new_model = f'chars\\ff_test_{gid}\\ff_test_{gid}_pc.ghg'
            check(new_model in paths, f'{gid}: new model in the archive')
            tmp = os.path.join(work, f'{gid}_check_pc.ghg')
            for key, label in ((new_model, 'new'),):
                if key in paths:
                    open(tmp, 'wb').write(arc.read(paths[key]))
                    c = classic.Character(tmp)
                    readable = sum(1 for L in c.layers for p in L['parts'] for mi in p['meshes'] if c.geometry(mi))
                    check(readable > 3, f'{gid}: {label} model reads ({readable} meshes)')
            chars = arc.read(paths['chars\\chars.txt']).lower()
            coll = arc.read(paths['chars\\collection.txt']).lower()
            check(f'ff_test_{gid}'.encode() in chars and f'ff_test_{gid}'.encode() in coll, f'{gid}: lists name the new character')
            del base_model
            arc.f.close()
            check(os.path.exists(dat + '.figureforge-backup'), f'{gid}: backup kept')
            res = page.js("""(async () => { FF.S.gx = { mode: 'replace', name: '', delivery: 'mod' }; await FF.gameExport(); return FF.S.gxResult; })()""")
            check(res and not res.get('error') and 'Mod folder' in res['text'], f'{gid} mod folder: {res}')
            page.js(f"FF.gameRestore('{gid}')")
            import time
            time.sleep(1)
            same = open(dat, 'rb').read(1 << 20) == open(os.path.join(INSTALLS[gid], 'GAME.DAT'), 'rb').read(1 << 20) and \
                os.path.getsize(dat) == os.path.getsize(os.path.join(INSTALLS[gid], 'GAME.DAT'))
            check(same, f'{gid}: Restore original gives back the original archive')
        page.call('Page.navigate', url=f'http://127.0.0.1:{httpd.server_address[1]}/?base=lb1:batman/batman&step=export')
        page.wait("document.body && document.body.dataset.ready === '1'", 300)
        import base64
        open(os.path.join(work, 'export_panel.png'), 'wb').write(base64.b64decode(page.call('Page.captureScreenshot', format='png')['data']))
    finally:
        edge.kill()
        httpd.shutdown()
    print(f'SUMMARY {"all passed" if not failures else str(len(failures)) + " failed"}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(os.path.abspath(sys.argv[1])))
