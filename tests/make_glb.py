"""Build a figure headlessly and export it as GLB (no window).

    python tests/make_glb.py <out.glb> <base id> [part id ...]

Uses tests/games.json. Each part id is added to the figure fitted to the base, replacing its slot.
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
os.environ['FIGUREFORGE_HOME'] = tempfile.mkdtemp(prefix='ff_glb_')
from forge import server, settings  # noqa: E402
from check_ui import EDGE, Page, free_port  # noqa: E402


def main(argv):
    out, base, parts = os.path.abspath(argv[0]), argv[1], argv[2:]
    exports = tempfile.mkdtemp(prefix='ff_glb_out_')
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')) as f:
        settings.save({'games': json.load(f), 'exports': exports})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = free_port()
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}',
                             f'--user-data-dir={tempfile.mkdtemp(prefix="ff_edge_")}', '--window-size=1000,800',
                             '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-allow-origins=*',
                             '--no-first-run', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        page.call('Page.navigate', url=f'http://127.0.0.1:{httpd.server_address[1]}/?base={base}')
        page.wait("document.body && document.body.dataset.ready === '1'", 300)
        for p in parts:
            page.js(f"FF.addPart({json.dumps(p)})")
        page.js("FF.S.exportName = 'figure'")
        err = page.js("FF.doExport('glb').then(() => '').catch(e => e.message)")
        print('status:', page.js("document.querySelector('#status').textContent"), 'meshes:', page.js("(() => { let n = 0; FF.viewer.figure.traverse(o => { if (o.isMesh) n++; }); return n; })()"))
        if err:
            raise SystemExit(err)
        time.sleep(0.5)
        shutil.copy(os.path.join(exports, 'figure.glb'), out)
        print('wrote', out, page.js("JSON.stringify(FF.S.parts.map(p => p.slot + ': ' + p.label))"))
    finally:
        edge.kill()
        httpd.shutdown()


if __name__ == '__main__':
    main(sys.argv[1:])
