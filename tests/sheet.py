"""Headless contact sheet of several figures (no window):
    python tests/sheet.py <out.png> <base id> [<base id> ...]
Uses tests/games.json; set FIGUREFORGE_HOME to reuse a catalogue cache between runs.
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FIGUREFORGE_HOME', tempfile.mkdtemp(prefix='ff_sheet_'))
from forge import server, settings  # noqa: E402
from check_ui import EDGE, Page, free_port  # noqa: E402


def main(argv):
    out, bases = argv[0], argv[1:]
    from PIL import Image, ImageDraw
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')) as f:
        settings.save({'games': json.load(f)})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = free_port()
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}',
                             f'--user-data-dir={tempfile.mkdtemp(prefix="ff_edge_")}', '--window-size=700,800',
                             '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-allow-origins=*',
                             '--no-first-run', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    tiles = []
    try:
        page = Page(port)
        # one page, on the Export step (no picture grids, so no part pictures are requested); figures are
        # switched in place
        page.call('Page.navigate', url=f'http://127.0.0.1:{httpd.server_address[1]}/')
        page.wait("document.body && document.body.dataset.ready === '1'", int(os.environ.get('SHEET_WAIT', 1800)))
        page.js("(() => { FF.S.step = 'export'; FF.render(); return 1; })()")
        for item in bases:
            back = item.endswith('~back')                # 'lmsh:thor/thor~back' shows the figure from behind
            base, _, clip = item.replace('~back', '').partition('@')   # '...@idle' plays a clip, frozen at 30 %
            try:
                page.js(f"(async () => {{ FF.viewer.stop && FF.viewer.stop(); await FF.setBase('{base}'); return 1; }})()")
                if clip:
                    try:
                        page.js(f"(async () => {{ await FF.playClip('{base}', '{clip}'); FF.viewer.playing = false; "
                                f"FF.viewer.seek(0.3); return 1; }})()")
                    except Exception as e:      # no such clip: the figure as it stands
                        print('  no clip:', str(e)[:120], flush=True)
                info = page.js("""(async () => { await FF.viewer.whenTexturesReady(); FF.viewer.frameFigure();
                    if (%s) { const v = FF.viewer, t = v.controls.target;
                              v.camera.position.set(2 * t.x - v.camera.position.x, v.camera.position.y,
                                                    2 * t.z - v.camera.position.z); v.camera.lookAt(t); }""" % ('true' if back else 'false') + """
                    await new Promise(r => setTimeout(r, 400));
                    return FF.viewer.snapshot(320, 400); })()""")
                im = Image.open(io.BytesIO(base64.b64decode(info.split(',', 1)[1]))).convert('RGB')
            except Exception as e:      # one bad figure must not stop the sheet
                try:
                    shown = page.js("document.body.innerText.slice(0, 300)") or ''
                except Exception:
                    shown = ''
                print('  failed:', ascii(str(e)[:200]), '| page says:', ascii(str(shown).replace(chr(10), ' / ')), flush=True)
                im = Image.new('RGB', (320, 400), (60, 20, 20))
                ImageDraw.Draw(im).text((8, 30), str(e)[:200], fill=(255, 200, 200))
            ImageDraw.Draw(im).text((6, 6), item, fill=(255, 255, 0))
            tiles.append(im)
            print(base, flush=True)
    finally:
        edge.kill()
        httpd.shutdown()
    cols = min(5, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new('RGB', (320 * cols, 400 * rows), (20, 20, 20))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * 320, (i // cols) * 400))
    sheet.save(out)


if __name__ == '__main__':
    main(sys.argv[1:])
