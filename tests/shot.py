"""Headless snapshot of one figure (no window): python tests/shot.py <out.png> <base id> [clip] [js to run first]

Uses the games set in tests/games.json ({"lb1": root, "indy": root, "tcs": root}).
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('FIGUREFORGE_HOME', tempfile.mkdtemp(prefix='ff_shot_'))
from forge import server, settings  # noqa: E402
from check_ui import EDGE, Page, free_port  # noqa: E402


def main(argv):
    out, base = argv[0], argv[1]
    clip = argv[2] if len(argv) > 2 and argv[2] != '-' else None
    pre = argv[3] if len(argv) > 3 else ''
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')) as f:
        settings.save({'games': json.load(f)})
    httpd = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = free_port()
    edge = subprocess.Popen([EDGE, '--headless=new', f'--remote-debugging-port={port}',
                             f'--user-data-dir={tempfile.mkdtemp(prefix="ff_edge_")}', '--window-size=1000,1000',
                             '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--remote-allow-origins=*',
                             '--no-first-run', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = Page(port)
        page.call('Page.navigate', url=f'http://127.0.0.1:{httpd.server_address[1]}/?base={base}')
        page.wait("document.body && document.body.dataset.ready === '1'", 1800)
        if pre:
            print(page.js(pre))
        if clip:
            page.js(f"FF.playClip('{base}', '{clip}').then(() => {{ FF.viewer.playing = false; FF.viewer.seek(0.3); }})")
        page.js("document.querySelector('main').style.gridTemplateColumns = '0 0 1fr'; FF.viewer.frameFigure()")
        time.sleep(2.0)
        data = page.call('Page.captureScreenshot', format='png')['data']
        import base64
        with open(out, 'wb') as f:
            f.write(base64.b64decode(data))
    finally:
        edge.kill()
        httpd.shutdown()


if __name__ == '__main__':
    main(sys.argv[1:])
